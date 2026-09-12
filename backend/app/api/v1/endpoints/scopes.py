import logging
import os
import tempfile
from typing import List, Optional
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased
from pydantic import BaseModel, Field
from app.db import models
from app.db.session import get_db
from app.db.models import Scope, ScopeDomain, Subnet, HostSubnetMapping, SubnetLabel, SubnetLabelAssignment, Site
from app.schemas.dns_names import (
    ScopeDomainBatchCreate,
    ScopeDomainBatchResponse,
    ScopeDomainPage,
    ScopeDomainRow,
)
from app.services import dns_name_service
from app.services.host_query_common import escape_like
from app.api.v1.endpoints.auth import get_current_user, require_role
from app.db.models_auth import User, UserRole
from app.api.deps import get_current_project, require_project_role, read_upload_capped
from app.db.models_project import Project, ProjectRole
from app.schemas.pagination import Paginated
from app.schemas.schemas import (
    Scope as ScopeSchema,
    ScopeSummary,
    ScopeCreate,
    ScopeUpdate,
    Subnet as SubnetSchema,
    SubnetCreate,
    SubnetUpdate,
    SubnetBatchCreate,
    SubnetFileUploadResponse,
    HostSubnetMapping as HostSubnetMappingSchema,
    ScopeCoverageSummary,
    ScopeCoverageHost,
)
from app.parsers.subnet_parser import SubnetParser
from app.services.agent_key_ttl import resolve_ttl_hours
from app.services.agent_prompt_service import resolve_base_url
from app.services.mcp_client_setup_service import build_mcp_clients
from app.services.subnet_correlation import SubnetCorrelationService

router = APIRouter(dependencies=[Depends(get_current_user)])


class MessageResponse(BaseModel):
    message: str


class CorrelateResponse(BaseModel):
    message: str
    mappings_created: int
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default scope helper (v2.9.4)
# ---------------------------------------------------------------------------
#
# As of v2.9.4 the user never names or manages a "scope container" — a
# project has exactly one scope conceptually, and the user sees a flat
# list of subnet/IP entries with optional labels.  The backend Scope
# model is kept as-is (no migration, no data loss on rollback), but all
# write paths funnel through this helper so every new project gets one
# sentinel-named scope and every upload/add operation appends to it
# rather than minting a new scope.

DEFAULT_SCOPE_NAME = "__default__"

# v2.244.0 — POST / and PATCH /{scope_id} (create + rename a scope container)
# were removed. They were leftovers from the pre-v2.9.4 model in which users
# named and managed scopes; since then a project has exactly one implicit
# scope and every write path funnels through get_or_create_default_scope
# below. Nothing in the UI had called them for that entire time.


def get_or_create_default_scope(db: Session, project_id: int, user_id: Optional[int] = None) -> Scope:
    """Return the project's default scope, creating it if it doesn't exist.

    If the project already has at least one scope (either a legacy
    named scope or the sentinel default), this returns the
    lowest-id existing scope so legacy projects land in a stable,
    deterministic "first" scope rather than minting yet another one.
    Projects with zero scopes get a freshly-created sentinel scope
    named ``__default__``.
    """
    existing = (
        db.query(Scope)
        .filter(Scope.project_id == project_id)
        .order_by(Scope.id.asc())
        .first()
    )
    if existing:
        return existing
    scope = Scope(
        name=DEFAULT_SCOPE_NAME,
        description="Project scope",
        project_id=project_id,
        uploaded_by_id=user_id,
    )
    db.add(scope)
    db.commit()
    db.refresh(scope)
    return scope


def _get_or_create_site(db: Session, project_id: int, name: Optional[str], user_id: Optional[int], cache: Optional[dict] = None) -> Optional[Site]:
    """Get-or-create the project's Site for ``name`` (blank → None).

    Keyed by (project_id, name) so the Site metadata (criticality tier / owner
    / expected host count) attaches to the human-entered site name set from
    CSV col 4 or inline edit — keeping subnet.site_id in sync with the string.
    """
    name = (name or "").strip()
    if not name:
        return None
    if cache is not None and name in cache:
        return cache[name]
    site = db.query(Site).filter(Site.project_id == project_id, Site.name == name).first()
    if site is None:
        site = Site(project_id=project_id, name=name, created_by_id=user_id)
        db.add(site)
        db.flush()
    if cache is not None:
        cache[name] = site
    return site


@router.post("/upload-subnets", response_model=SubnetFileUploadResponse, dependencies=[Depends(require_project_role(ProjectRole.ANALYST))])
async def upload_subnet_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Upload a scope file and append its entries to the project's scope.

    Per v2.9.4, a project has exactly one conceptual scope.  Every
    uploaded file's CIDRs (and single IPs, which are accepted via
    ``ipaddress.ip_network(strict=False)``) are appended to that
    scope.  Duplicate entries already present in the scope are
    silently skipped so re-uploading the same file is idempotent.

    v2.326.0: a row that isn't a subnet is a domain name.  Domain rows go
    to ``scope_domains`` through the same upsert as the domains card
    (``*.example.com`` → ``example.com`` + include_subdomains), so a
    client-supplied scope list mixing ranges and FQDNs uploads in one go.
    Name scope and subnet scope stay independent — a domain row never
    creates a subnet, and vice versa.
    """
    # Audit finding C3: the previous implementation read the entire
    # upload into memory via ``await file.read()`` with no size check,
    # so an authenticated analyst could OOM a worker with a 2GB file.
    # The byte cap is the memory guard: 2 MB holds ~50K plain CIDR lines.
    # The entry cap bounds the single transaction this handler runs (one
    # flush for the batch, then a trie-based re-correlation of the project's
    # hosts) and is sized to what the byte cap can actually carry — v2.332.4
    # raised it from the 10,000 that was borrowed from the test-plan import
    # cap at audit time, when this path was still per-row and O(n²).
    MAX_SUBNET_FILE_BYTES = 2 * 1024 * 1024  # 2 MB
    MAX_SUBNETS_PER_UPLOAD = 50_000

    allowed_extensions = ['.txt', '.csv']
    if not any(file.filename.lower().endswith(ext) for ext in allowed_extensions):
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Supported types: {', '.join(allowed_extensions)}"
        )

    # Bounded read — reject an oversize file before it materializes in memory
    # (see read_upload_capped; the old ``await file.read()`` + len() check ran
    # only after the whole upload was already in RAM).
    content = await read_upload_capped(
        file,
        MAX_SUBNET_FILE_BYTES,
        detail=(
            f"File too large. Maximum allowed: {MAX_SUBNET_FILE_BYTES:,} bytes "
            f"(~{MAX_SUBNETS_PER_UPLOAD:,} CIDR entries)."
        ),
    )
    try:
        file_content = content.decode('utf-8')
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="File must be UTF-8 encoded text"
        )

    # Everything from here is synchronous DB/CPU work.  The handler is
    # ``async`` only for the bounded upload read above; running the parse,
    # writes and project-wide correlation inline would block this API
    # process's event loop for the duration, so it goes to the threadpool
    # (the same place FastAPI runs plain ``def`` endpoints).
    def _ingest() -> SubnetFileUploadResponse:
        # .csv → row-per-entry with optional space-delimited labels in column 2;
        # .txt → flat list (no labels).  Both normalize identically and both
        # split subnet rows from domain rows.
        is_csv = file.filename.lower().endswith('.csv')
        domain_rows_ignored_cols = 0
        try:
            parser = SubnetParser(db)
            if is_csv:
                entries, domain_entries, domain_rows_ignored_cols = parser.parse_scope_csv(file_content)
            else:
                cidrs, domain_entries = parser.parse_scope_list(file_content)
                entries = [(c, [], "", "") for c in cidrs]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if len(entries) + len(domain_entries) > MAX_SUBNETS_PER_UPLOAD:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"File contains {len(entries) + len(domain_entries):,} scope entries; "
                    f"maximum per upload is {MAX_SUBNETS_PER_UPLOAD:,}. "
                    f"Split the file into smaller uploads."
                ),
            )

        # Resolve (or create) the project's single scope.  Subnets dedup by cidr
        # (never a duplicate row); labels merge (add, never replace) so repeated
        # uploads accumulate labels onto the same subnet.
        scope = get_or_create_default_scope(db, project.id, user_id=current_user.id)
        existing_subnets = {
            s.cidr: s for s in db.query(Subnet).filter(Subnet.scope_id == scope.id).all()
        }
        added = 0
        descriptions_set = 0
        sites_set = 0
        site_cache: dict = {}  # name -> Site, get-or-created once per upload
        for cidr, _labels, description, site in entries:
            site_obj = _get_or_create_site(db, project.id, site, current_user.id, site_cache) if site else None
            sub = existing_subnets.get(cidr)
            if sub is None:
                sub = Subnet(
                    cidr=cidr, scope_id=scope.id,
                    description=(description or None),
                    site=(site or None), site_id=(site_obj.id if site_obj else None),
                )
                db.add(sub)
                existing_subnets[cidr] = sub
                added += 1
                if description:
                    descriptions_set += 1
                if site:
                    sites_set += 1
            else:
                # Update description/site on an existing subnet when the CSV
                # provides them (empty cols leave the existing values intact).
                if description:
                    sub.description = description
                    descriptions_set += 1
                if site:
                    sub.site = site
                    sub.site_id = site_obj.id if site_obj else None
                    sites_set += 1
        # One flush for the whole batch — populates .id on every pending Subnet
        # (the existing_subnets map holds live object refs) for the label
        # assignments below.  Avoids a per-row round-trip (and a regression on the
        # label-less .txt path, which never flushed in the loop before).
        db.flush()

        # Labels: get-or-create the project label, then add only assignments that
        # don't already exist (the uq_subnet_label_assignment dedup, applied in
        # code) — existing labels on a subnet are left intact.
        label_cache: dict = {}

        def _get_label(name: str) -> SubnetLabel:
            lbl = label_cache.get(name)
            if lbl is None:
                lbl = (
                    db.query(SubnetLabel)
                    .filter(SubnetLabel.project_id == project.id, SubnetLabel.name == name)
                    .first()
                )
                if lbl is None:
                    lbl = SubnetLabel(project_id=project.id, name=name, created_by_id=current_user.id)
                    db.add(lbl)
                    db.flush()
                label_cache[name] = lbl
            return lbl

        affected_ids = [existing_subnets[c].id for c, _, _, _ in entries]
        existing_assignments = set()
        if affected_ids:
            for sid, lid in (
                db.query(SubnetLabelAssignment.subnet_id, SubnetLabelAssignment.label_id)
                .filter(SubnetLabelAssignment.subnet_id.in_(affected_ids))
                .all()
            ):
                existing_assignments.add((sid, lid))

        labels_applied = 0
        for cidr, label_names, _description, _site in entries:
            if not label_names:
                continue
            sub = existing_subnets[cidr]
            for name in label_names:
                lbl = _get_label(name)
                key = (sub.id, lbl.id)
                if key in existing_assignments:
                    continue
                db.add(SubnetLabelAssignment(
                    subnet_id=sub.id, label_id=lbl.id, created_by_id=current_user.id,
                ))
                existing_assignments.add(key)
                labels_applied += 1

        # Domain rows: same upsert as POST /scopes/{id}/domains, so an existing
        # entry widens (exact → include_subdomains) and never narrows.  The
        # parser already validated every name, so ``invalid`` is empty here;
        # it is asserted rather than trusted.
        domains_added = domains_updated = 0
        if domain_entries:
            domains_added, domains_updated, invalid_domains = dns_name_service.upsert_scope_domains(
                db, scope, domain_entries, created_by_id=current_user.id,
            )
            if invalid_domains:
                db.rollback()
                raise HTTPException(
                    status_code=400,
                    detail="Invalid domain entries: " + "; ".join(invalid_domains[:5]),
                )

        db.commit()

        correlation_service = SubnetCorrelationService(db)
        correlation_service.invalidate_subnet_cache()
        correlated_hosts = None
        # Only a NEW subnet changes host membership; a duplicate-only or
        # description/label-only upload must not trigger the project-wide rebuild.
        if added:
            try:
                correlated_hosts = correlation_service.correlate_all_hosts_to_subnets(project_id=project.id)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Subnet correlation after upload failed: %s", exc)

        skipped = len(entries) - added
        parts = []
        if entries or not domain_entries:
            part = f"{added} subnet(s)"
            if skipped > 0:
                part += f" ({skipped} duplicate{'s' if skipped != 1 else ''} skipped)"
            parts.append(part)
        if domain_entries:
            skipped_domains = len(domain_entries) - domains_added - domains_updated
            part = f"{domains_added} domain(s)"
            extras = []
            if domains_updated:
                extras.append(f"{domains_updated} widened to include subdomains")
            if skipped_domains > 0:
                extras.append(f"{skipped_domains} duplicate{'s' if skipped_domains != 1 else ''} skipped")
            if extras:
                part += f" ({', '.join(extras)})"
            parts.append(part)
        message = "Added " + " and ".join(parts) + " to the project scope"
        if domain_rows_ignored_cols > 0:
            message += (
                f"; labels/site ignored on {domain_rows_ignored_cols} domain "
                f"row{'s' if domain_rows_ignored_cols != 1 else ''} (subnet-only columns)"
            )
        if labels_applied > 0:
            message += f"; applied {labels_applied} label assignment{'s' if labels_applied != 1 else ''}"
        if descriptions_set > 0:
            message += f"; set {descriptions_set} description{'s' if descriptions_set != 1 else ''}"
        if sites_set > 0:
            message += f"; set {sites_set} site{'s' if sites_set != 1 else ''}"
        if correlated_hosts is not None:
            message += f"; correlated {correlated_hosts} host-subnet relationships"

        return SubnetFileUploadResponse(
            message=message,
            scope_id=scope.id,
            subnets_added=added,
            domains_added=domains_added,
            filename=file.filename
        )

    return await run_in_threadpool(_ingest)

def _serialize_scope_with_subnets(
    db: Session,
    scope: Scope,
    with_findings_only: bool,
    subnets_skip: int,
    subnets_limit: Optional[int],
    subnets_search: Optional[str] = None,
) -> dict:
    """Build the ScopeSchema payload with a server-paginated subnets array.

    Shared by ``GET /scopes/default`` and ``GET /scopes/{scope_id}``.
    ``with_findings_only`` restricts to subnets with at least one correlated
    host; ``subnets_limit`` (when set) caps the page so a 6000+ subnet project
    doesn't ship a multi-MB body.  ``subnets_search`` (case-insensitive
    substring over cidr + description) lets the UI jump straight to an entry
    instead of paging to find it.  ``subnets_total`` always carries the count
    of the *filtered* set so the frontend's "Showing N of T" + 'load more'
    affordance stay correct under search.
    """
    # Build the search predicate once so it's applied identically to the page
    # query AND the count query — otherwise subnets_total would describe the
    # full set while subnets shows the filtered page.
    search_filter = None
    if subnets_search and subnets_search.strip():
        like = f"%{escape_like(subnets_search.strip())}%"
        search_filter = or_(
            Subnet.cidr.ilike(like, escape="\\"),
            Subnet.description.ilike(like, escape="\\"),
        )

    subnet_q = db.query(Subnet).filter(Subnet.scope_id == scope.id)
    if search_filter is not None:
        subnet_q = subnet_q.filter(search_filter)
    if with_findings_only:
        subnet_q = (
            subnet_q
            .outerjoin(HostSubnetMapping)
            .group_by(Subnet.id)
            .having(func.count(HostSubnetMapping.id) > 0)
        )
        total_q = (
            db.query(func.count(func.distinct(Subnet.id)))
            .select_from(Subnet)
            .outerjoin(HostSubnetMapping)
            .filter(Subnet.scope_id == scope.id)
            .group_by(Subnet.id)
            .having(func.count(HostSubnetMapping.id) > 0)
        )
        if search_filter is not None:
            total_q = total_q.filter(search_filter)
        subnets_total_count = total_q.count()
    else:
        total_q = db.query(func.count(Subnet.id)).filter(Subnet.scope_id == scope.id)
        if search_filter is not None:
            total_q = total_q.filter(search_filter)
        subnets_total_count = total_q.scalar() or 0

    subnet_q = subnet_q.order_by(Subnet.id.asc()).offset(subnets_skip)
    if subnets_limit is not None:
        subnet_q = subnet_q.limit(subnets_limit)
    subnet_rows = subnet_q.all()

    # Hosts mapped to each subnet on this page: the same HostSubnetMapping
    # count the /hosts subnet facet shows, so the two pages agree. One grouped
    # query per page, never one per row.
    page_ids = [s.id for s in subnet_rows]
    host_counts = dict(
        db.query(HostSubnetMapping.subnet_id, func.count(HostSubnetMapping.id))
        .filter(HostSubnetMapping.subnet_id.in_(page_ids))
        .group_by(HostSubnetMapping.subnet_id)
        .all()
    ) if page_ids else {}
    subnets = [
        SubnetSchema.model_validate(s).model_copy(update={"host_count": host_counts.get(s.id, 0)})
        for s in subnet_rows
    ]

    return {
        "id": scope.id,
        "name": scope.name,
        "description": scope.description,
        "created_at": scope.created_at,
        "updated_at": scope.updated_at,
        "subnets": subnets,
        "subnets_total": subnets_total_count,
        "subnets_skip": subnets_skip if subnets_limit is not None else None,
        "subnets_limit": subnets_limit,
    }


@router.get(
    "/default",
    response_model=ScopeSchema,
    summary="Fetch the project's scope (creating it if needed)",
)
def get_default_scope(
    with_findings_only: bool = Query(
        False,
        description=(
            "Restrict to subnets with at least one correlated host.  Defaults "
            "to False here — the flat subnet editor shows every entry."
        ),
    ),
    subnets_skip: int = Query(0, ge=0, description="Offset into the subnets list (pagination)."),
    subnets_limit: Optional[int] = Query(
        None,
        ge=1,
        le=500,
        description=(
            "Page size for the subnets array.  Omitted = every subnet (legacy "
            "behaviour).  6000+ subnet projects should pass e.g. 200 and use "
            "subnets_total to drive a 'load more' affordance."
        ),
    ),
    subnets_search: Optional[str] = Query(
        None,
        description=(
            "Case-insensitive substring filter over subnet cidr + description. "
            "Applied before pagination, and reflected in subnets_total."
        ),
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """Return the project's single scope with a (paginated) subnet list.

    The v2.9.4 UI treats a project as having exactly one scope, so this is
    the canonical endpoint for the flat subnet editor.  If no scope exists
    yet, one is created on the fly (empty) so the caller can start appending
    entries immediately.  v2.94.0 — the subnets array is server-paginated
    (subnets_skip/subnets_limit) so a 6000-subnet project no longer blocks
    /scopes on a multi-MB payload + inline serialization.
    """
    scope = get_or_create_default_scope(db, project.id, user_id=current_user.id)
    return _serialize_scope_with_subnets(
        db, scope, with_findings_only, subnets_skip, subnets_limit, subnets_search
    )


@router.get("/", response_model=List[ScopeSummary])
def get_scopes(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get all scopes with summary information."""
    scopes = db.query(
        Scope.id,
        Scope.name,
        Scope.description,
        Scope.created_at,
        func.count(Subnet.id).label('subnet_count')
    ).outerjoin(Subnet).filter(Scope.project_id == project.id).group_by(Scope.id).all()

    return [
        ScopeSummary(
            id=scope.id,
            name=scope.name,
            description=scope.description,
            created_at=scope.created_at,
            subnet_count=scope.subnet_count
        )
        for scope in scopes
    ]


@router.get("/coverage", response_model=ScopeCoverageSummary)
def get_scope_coverage(
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Return aggregate coverage information and recent out-of-scope hosts."""

    total_scopes = db.query(func.count(Scope.id)).filter(Scope.project_id == project.id).scalar() or 0
    total_subnets = (
        db.query(func.count(Subnet.id))
        .join(Scope, Scope.id == Subnet.scope_id)
        .filter(Scope.project_id == project.id)
        .scalar() or 0
    )
    total_hosts = db.query(func.count(models.Host.id)).filter(models.Host.project_id == project.id).scalar() or 0
    scoped_hosts = (
        db.query(func.count(func.distinct(HostSubnetMapping.host_id)))
        .join(models.Host, models.Host.id == HostSubnetMapping.host_id)
        .filter(models.Host.project_id == project.id)
        .scalar() or 0
    )

    # v2.322.0 — domain scope.  Hosts an in-scope name resolves to (and that
    # no subnet covers) are a third state: not counted as scoped, not
    # reported as out of scope.
    total_domains = (
        db.query(func.count(ScopeDomain.id))
        .join(Scope, Scope.id == ScopeDomain.scope_id)
        .filter(Scope.project_id == project.id)
        .scalar() or 0
    )
    name_reachable_cond = dns_name_service.host_reachable_via_in_scope_name_condition(project.id)
    name_reachable_hosts = 0
    if total_domains:
        name_reachable_hosts = (
            db.query(func.count(models.Host.id))
            .outerjoin(HostSubnetMapping, HostSubnetMapping.host_id == models.Host.id)
            .filter(
                models.Host.project_id == project.id,
                HostSubnetMapping.host_id.is_(None),
                name_reachable_cond,
            )
            .scalar() or 0
        )

    out_of_scope_count = max(total_hosts - scoped_hosts - name_reachable_hosts, 0)
    coverage_percentage = (
        (scoped_hosts / total_hosts) * 100 if total_hosts > 0 else 0.0
    )

    scan_alias = aliased(models.Scan)

    recent_out_of_scope = (
        db.query(
            models.Host.id.label("host_id"),
            models.Host.ip_address,
            models.Host.hostname,
            models.Host.last_seen,
            models.Host.last_updated_scan_id,
            scan_alias.filename.label("scan_filename"),
        )
        .outerjoin(HostSubnetMapping, HostSubnetMapping.host_id == models.Host.id)
        .outerjoin(scan_alias, scan_alias.id == models.Host.last_updated_scan_id)
        .filter(HostSubnetMapping.host_id.is_(None))
        .filter(~name_reachable_cond)
        .filter(models.Host.project_id == project.id)
        .order_by(models.Host.last_seen.desc().nullslast())
        .limit(limit)
        .all()
    )

    recent_entries = [
        ScopeCoverageHost(
            host_id=row.host_id,
            ip_address=row.ip_address,
            hostname=row.hostname,
            last_seen=row.last_seen,
            last_scan_id=row.last_updated_scan_id,
            last_scan_filename=row.scan_filename,
        )
        for row in recent_out_of_scope
    ]

    # v2.12.1: top technologies observed on project hosts via the
    # web_interfaces table.  Counts distinct hosts per tech (not
    # distinct interfaces) so a single host running both "Nginx" and
    # "React" adds 1 to each rather than skewing the list.  Null
    # technologies arrays are skipped.
    from app.schemas.schemas import TopTechnology
    tech_rows = (
        db.query(models.WebInterface.host_id, models.WebInterface.technologies)
        .filter(
            models.WebInterface.project_id == project.id,
            models.WebInterface.technologies.isnot(None),
        )
        .all()
    )
    tech_host_sets: dict = {}
    for host_id, tech_list in tech_rows:
        if not tech_list:
            continue
        for t in tech_list:
            if not t:
                continue
            tech_host_sets.setdefault(str(t), set()).add(host_id)
    top_techs = sorted(
        ({'name': name, 'host_count': len(hosts)} for name, hosts in tech_host_sets.items()),
        key=lambda x: (-x['host_count'], x['name'].lower()),
    )[:10]
    top_technologies = [TopTechnology(**t) for t in top_techs]

    return ScopeCoverageSummary(
        total_scopes=total_scopes,
        total_subnets=total_subnets,
        total_domains=total_domains,
        name_reachable_hosts=name_reachable_hosts,
        total_hosts=total_hosts,
        scoped_hosts=scoped_hosts,
        out_of_scope_hosts=out_of_scope_count,
        coverage_percentage=coverage_percentage,
        has_scope_configuration=(total_subnets > 0 or total_domains > 0),
        recent_out_of_scope_hosts=recent_entries,
        top_technologies=top_technologies,
    )


@router.get("/{scope_id}", response_model=ScopeSchema)
def get_scope(
    scope_id: int,
    with_findings_only: Optional[bool] = Query(True, description="Only show subnets with correlated host findings"),
    subnets_skip: int = Query(
        0,
        ge=0,
        description=(
            "Offset into the scope's subnets list.  Default 0.  Combined "
            "with subnets_limit for server-paginated detail pages "
            "(v2.85.0)."
        ),
    ),
    subnets_limit: Optional[int] = Query(
        None,
        ge=1,
        le=500,
        description=(
            "Maximum number of subnets to return.  When omitted (the "
            "pre-v2.85.0 default), every subnet is returned in one shot "
            "for backward compatibility.  Frontends scaling to 6000+ "
            "subnet projects should pass a page size (e.g. 200) and use "
            "subnets_total to drive a 'load more' affordance."
        ),
    ),
    subnets_search: Optional[str] = Query(
        None,
        description=(
            "Case-insensitive substring filter over subnet cidr + description. "
            "Applied before pagination, and reflected in subnets_total."
        ),
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get a specific scope with its subnets, optionally filtered by findings.

    v2.85.0 — pagination on the subnets array.  6000+ subnet projects
    were 5+ MB payloads and seconds of inline serialization; passing
    ``subnets_limit`` chunks the response and the frontend appends
    subsequent pages.
    """
    scope = db.query(Scope).filter(Scope.id == scope_id, Scope.project_id == project.id).first()
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")

    return _serialize_scope_with_subnets(
        db, scope, with_findings_only, subnets_skip, subnets_limit, subnets_search
    )
@router.delete(
    "/{scope_id}",
    response_model=MessageResponse,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Insufficient permissions — analyst role required"},
        404: {"description": "Scope not found"},
    },
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Delete scope (analyst)",
)
def delete_scope(
    scope_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Delete a scope and all its subnets. Requires analyst role."""
    scope = db.query(Scope).filter(Scope.id == scope_id, Scope.project_id == project.id).first()
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")

    db.delete(scope)
    db.commit()

    return {"message": "Scope deleted successfully"}

@router.get(
    "/{scope_id}/host-mappings",
    response_model=Paginated[HostSubnetMappingSchema],
)
def get_scope_host_mappings(
    scope_id: int,
    # v2.86.8 — paginate + add subnet_id filter.  Pre-fix this returned
    # every mapping across every subnet of the scope on every page entry,
    # which was the heaviest cost on ScopeDetail.tsx for projects with
    # ~thousands of mapped hosts.  Back-compat: callers that omit
    # subnet_id + limit still get every row in scope (up to the le=2000
    # cap).  The frontend should pass subnet_id when the user opens a
    # specific subnet's details panel.
    subnet_id: Optional[int] = Query(
        None,
        description=(
            "Filter mappings to a single subnet (v2.86.8).  Use when the "
            "UI is rendering one subnet's host list and doesn't need the "
            "rest."
        ),
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(2000, ge=1, le=2000),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get host-subnet mappings for a specific scope, optionally
    restricted to one subnet.  Paginated."""
    scope = db.query(Scope).filter(Scope.id == scope_id, Scope.project_id == project.id).first()
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")

    q = db.query(HostSubnetMapping).join(Subnet).filter(Subnet.scope_id == scope_id)
    if subnet_id is not None:
        q = q.filter(HostSubnetMapping.subnet_id == subnet_id)

    # v2.86.13 — envelope shape.  Total comes from the same filtered
    # query the page returns rows from, so "Showing N of T" math is
    # consistent with the result list.
    total = q.with_entities(func.count(HostSubnetMapping.id)).scalar() or 0
    mappings = q.order_by(HostSubnetMapping.id.asc()).offset(skip).limit(limit).all()
    return Paginated[HostSubnetMappingSchema].build(
        items=mappings, total=total, skip=skip, limit=limit,
    )

@router.post(
    "/correlate-all",
    response_model=CorrelateResponse,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Insufficient permissions — analyst role required"},
    },
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Correlate hosts to subnets (analyst)",
)
def correlate_all_hosts(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Manually correlate all existing hosts to subnets. Requires analyst role."""
    correlation_service = SubnetCorrelationService(db)
    mappings_created = correlation_service.correlate_all_hosts_to_subnets(project_id=project.id)

    return {
        "message": f"Successfully created {mappings_created} host-subnet mappings",
        "mappings_created": mappings_created
    }


# ---------------------------------------------------------------------------
# Manual scope editing (v2.9.1)
# ---------------------------------------------------------------------------
#
# These endpoints let analysts edit scope metadata + add/edit/delete
# individual subnets without the file-upload path.  All CIDR inputs are
# validated with ``ipaddress.ip_network(strict=False)`` before hitting
# the DB, so garbage entries are rejected at the API layer.  After any
# subnet insert or CIDR change we re-run the SubnetCorrelationService
# so host-subnet mappings are consistent with the new state.

def _validate_cidr(cidr: str) -> str:
    """Return the normalized CIDR or raise HTTPException(400)."""
    import ipaddress
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=False)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid CIDR {cidr!r}: {exc}",
        )
    return str(net)
@router.post(
    "/{scope_id}/subnets",
    response_model=List[SubnetSchema],
    status_code=201,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Add one or more subnets to a scope (analyst)",
)
def add_subnets(
    scope_id: int,
    body: SubnetBatchCreate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Manually add subnets to an existing scope.

    Duplicates (same CIDR within the same scope) are rejected with 400.
    After insert the subnet-correlation service runs so any existing
    hosts map into the new subnets automatically.
    """
    scope = db.query(Scope).filter(Scope.id == scope_id, Scope.project_id == project.id).first()
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")

    # Normalize + validate up front so a bad entry doesn't partially insert.
    normalized: List[tuple] = []
    for item in body.subnets:
        cidr = _validate_cidr(item.cidr)
        normalized.append((cidr, item.description, item.site))

    # Duplicate check inside the payload and against existing DB rows.
    seen: set = set()
    existing_cidrs = {
        row.cidr
        for row in db.query(Subnet).filter(Subnet.scope_id == scope_id).all()
    }
    for cidr, _, _ in normalized:
        if cidr in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate CIDR in request: {cidr}")
        if cidr in existing_cidrs:
            raise HTTPException(
                status_code=400,
                detail=f"CIDR {cidr} already exists in this scope",
            )
        seen.add(cidr)

    created: List[Subnet] = []
    site_cache: dict = {}
    for cidr, description, site in normalized:
        site_obj = _get_or_create_site(db, project.id, site, None, site_cache) if site else None
        row = Subnet(
            cidr=cidr, description=description, site=(site or None),
            site_id=(site_obj.id if site_obj else None), scope_id=scope_id,
        )
        db.add(row)
        created.append(row)
    db.commit()
    for row in created:
        db.refresh(row)

    # Correlate only the newly added subnets so hosts already in the database
    # get mapped to them — O(hosts × new subnets), touching only these subnets'
    # mapping rows instead of rewriting the whole project's mapping table.
    svc = SubnetCorrelationService(db)
    for row in created:
        svc.correlate_subnet(row.id)

    return created


@router.patch(
    "/{scope_id}/subnets/{subnet_id}",
    response_model=SubnetSchema,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Edit a subnet (analyst)",
)
def update_subnet(
    scope_id: int,
    subnet_id: int,
    body: SubnetUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Edit a subnet's CIDR and/or description.

    If the CIDR changes, the subnet-correlation service is re-run so
    host mappings reflect the new network.  Old mappings that no longer
    match are removed automatically by the correlation service.
    """
    scope = db.query(Scope).filter(Scope.id == scope_id, Scope.project_id == project.id).first()
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")
    subnet = (
        db.query(Subnet)
        .filter(Subnet.id == subnet_id, Subnet.scope_id == scope_id)
        .first()
    )
    if not subnet:
        raise HTTPException(status_code=404, detail="Subnet not found")

    cidr_changed = False
    if body.cidr is not None and body.cidr != subnet.cidr:
        new_cidr = _validate_cidr(body.cidr)
        collision = (
            db.query(Subnet)
            .filter(
                Subnet.scope_id == scope_id,
                Subnet.cidr == new_cidr,
                Subnet.id != subnet_id,
            )
            .first()
        )
        if collision:
            raise HTTPException(
                status_code=400,
                detail=f"CIDR {new_cidr} already exists in this scope",
            )
        subnet.cidr = new_cidr
        cidr_changed = True
    if body.description is not None:
        subnet.description = body.description
    if body.site is not None:
        # Empty string clears the site; a value sets it (keeping site_id synced).
        subnet.site = body.site or None
        site_obj = _get_or_create_site(db, project.id, body.site, None) if body.site else None
        subnet.site_id = site_obj.id if site_obj else None

    db.commit()
    db.refresh(subnet)

    if cidr_changed:
        # Recompute only this subnet's mappings for its new CIDR — replaces the
        # old whole-project re-correlate. correlate_subnet does the scoped
        # delete + insert (a CIDR change can drop hosts that no longer match).
        SubnetCorrelationService(db).correlate_subnet(subnet_id)

    return subnet


@router.delete(
    "/{scope_id}/subnets/{subnet_id}",
    response_model=MessageResponse,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Delete a subnet from a scope (analyst)",
)
def delete_subnet(
    scope_id: int,
    subnet_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Remove a single subnet from a scope. FK cascades drop mappings."""
    scope = db.query(Scope).filter(Scope.id == scope_id, Scope.project_id == project.id).first()
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")
    subnet = (
        db.query(Subnet)
        .filter(Subnet.id == subnet_id, Subnet.scope_id == scope_id)
        .first()
    )
    if not subnet:
        raise HTTPException(status_code=404, detail="Subnet not found")
    db.delete(subnet)
    db.commit()
    return {"message": "Subnet deleted successfully"}


# ---------------------------------------------------------------------------
# Domain scope (v2.322.0)
# ---------------------------------------------------------------------------
#
# Names in scope, alongside subnets.  Exact-name membership and "include
# descendants" are separate flags; name scope never confers subnet scope on
# the addresses names resolve to (see ScopeDomain docstring).

def _scope_domain_rows(db: Session, project_id: int, domains: List[ScopeDomain]) -> List[ScopeDomainRow]:
    """Serialize with a per-entry count of the project's concrete names it
    covers — the operator's check that a declaration actually bites.  Counts
    are set-based (two grouped queries for the page), never one per row."""
    counts = dns_name_service.scope_domain_name_counts(db, project_id, domains)
    return [
        ScopeDomainRow(
            id=d.id, scope_id=d.scope_id, domain=d.domain,
            include_subdomains=bool(d.include_subdomains), description=d.description,
            created_at=d.created_at, name_count=counts.get(d.id, 0),
        )
        for d in domains
    ]


def _scope_domain_page(db: Session, project_id: int, scope_id: int, skip: int, limit: int) -> ScopeDomainPage:
    q = db.query(ScopeDomain).filter(ScopeDomain.scope_id == scope_id)
    total = q.with_entities(func.count(ScopeDomain.id)).scalar() or 0
    rows = q.order_by(ScopeDomain.domain.asc()).offset(skip).limit(limit).all()
    page = ScopeDomainPage.build(_scope_domain_rows(db, project_id, rows), total, skip, limit)
    # Deduplicated across entries — the per-row counts are not.
    page.names_in_scope_total = dns_name_service.scope_domains_covered_names_total(db, project_id)
    return page


def _load_scope_or_404(db: Session, scope_id: int, project_id: int) -> Scope:
    scope = db.query(Scope).filter(Scope.id == scope_id, Scope.project_id == project_id).first()
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")
    return scope


@router.get(
    "/{scope_id}/domains",
    response_model=ScopeDomainPage,
    summary="List the domains declared in scope (paged)",
)
def list_scope_domains(
    scope_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Paged: a bulk import with declare-scope can create thousands of
    entries, and the per-entry name counts are computed for the page only."""
    scope = _load_scope_or_404(db, scope_id, project.id)
    return _scope_domain_page(db, project.id, scope.id, skip, limit)


@router.post(
    "/{scope_id}/domains",
    response_model=ScopeDomainBatchResponse,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Declare domains in scope (analyst)",
)
def add_scope_domains(
    scope_id: int,
    body: ScopeDomainBatchCreate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """Idempotent: an existing entry widens (exact → include_subdomains) but
    never narrows.  ``*.example.com`` is accepted and stored as
    ``example.com`` with include_subdomains."""
    scope = _load_scope_or_404(db, scope_id, project.id)
    added, updated, invalid = dns_name_service.upsert_scope_domains(
        db, scope,
        [(d.domain, d.include_subdomains, d.description) for d in body.domains],
        created_by_id=current_user.id,
    )
    db.commit()
    # First page only — the caller re-lists to page through a large set.
    page = _scope_domain_page(db, project.id, scope.id, 0, 100)
    return ScopeDomainBatchResponse(
        added=added, updated=updated, invalid=invalid,
        domains=page.items, total=page.total,
        names_in_scope_total=page.names_in_scope_total,
    )


@router.delete(
    "/{scope_id}/domains/{domain_id}",
    response_model=MessageResponse,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Remove a domain from scope (analyst)",
)
def delete_scope_domain(
    scope_id: int,
    domain_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    scope = _load_scope_or_404(db, scope_id, project.id)
    row = (
        db.query(ScopeDomain)
        .filter(ScopeDomain.id == domain_id, ScopeDomain.scope_id == scope.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Scope domain not found")
    db.delete(row)
    db.commit()
    return {"message": "Domain removed from scope"}


# ---------------------------------------------------------------------------
# Agentic reconnaissance — start a recon session against a scope
# ---------------------------------------------------------------------------
# v2.11.0 — replaces the old /test-plans/generate-recon endpoint.
# Recon now populates host data via the ingestion pipeline instead of
# creating test plan entries.  See agent_prompt_service for the new
# prompt and agent_api.py for the /agent/recon/* endpoints the agent
# will call with the minted key.

class StartReconRequest(BaseModel):
    notes: Optional[str] = None
    # v2.58.0 — per-key TTL for the recon session's agent key.
    # None = deployment default; values above AGENT_KEY_MAX_TTL_HOURS
    # are clamped server-side.  Use for engagements expected to run
    # longer than 24h so the agent doesn't hit a mid-flight expiry.
    ttl_hours: Optional[int] = Field(None, ge=1)


class StartReconResponse(BaseModel):
    recon_session_id: int
    scope_id: int
    scope_name: str
    subnets: List[str]
    agent_id: int
    api_key: str  # plaintext, shown once
    instructions: str
    # v2.65.0 — surface the resolved TTL so the dialog can render
    # the actual expiry without hardcoding a value that drifts when
    # AGENT_KEY_TTL_HOURS is overridden in .env.
    key_ttl_hours: int
    # v2.279.0 — per-client MCP setup, the same shape assist emits.  Recon has
    # had MCP tools since 2.278.0; without this the operator was told about a
    # curl recipe and left to work out the client config themselves.  The hints
    # carry the sandbox flags, because recon runs scanners on their machine.
    mcp_clients: List[dict] = []
    mcp_url: str = ""


# ---------------------------------------------------------------------------
# Recon-session lifecycle helpers.  Agent resolution and key minting live in
# ``agent_session_service`` (v2.337.0); the per-recon copies are gone (v2.338.0).
# ---------------------------------------------------------------------------

def _load_active_integrations(db, *, user, project):
    """Return the user's decrypted active scanner-integration credentials
    for this project, for inlining into the recon prompt (credentialed
    scanners — Nessus, OpenVAS, Nuclei).  Plaintext inlining is
    authorized by the user who created the integration."""
    from app.services.integration_service import IntegrationService, decrypt_integration
    int_svc = IntegrationService(db)
    return [
        decrypt_integration(r)
        for r in int_svc.list_for_user(user.id, project_id=project.id)
        if r.is_active
    ]


@router.post(
    "/{scope_id}/recon/start",
    response_model=StartReconResponse,
    status_code=201,
    summary="Start an agentic reconnaissance session against a scope",
)
def start_recon_session(
    scope_id: int,
    body: StartReconRequest,
    request: Request,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    """Create a ReconSession and mint a scope-bound agent API key.

    The key grants access to the ``/agent/recon/*`` endpoints only —
    test plan endpoints reject scope-bound keys with 403.  Returns
    instructions the user can paste to their terminal agent; the
    plaintext API key is shown exactly once.

    Replaces the deprecated ``POST /test-plans/generate-recon`` flow
    that misdirected agents into building test plans before the host
    database was even populated.
    """
    scope = (
        db.query(Scope)
        .filter(Scope.id == scope_id, Scope.project_id == project.id)
        .first()
    )
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")

    subnet_cidrs = [
        row[0] for row in db.query(Subnet.cidr).filter(Subnet.scope_id == scope.id).all()
    ]
    if not subnet_cidrs:
        raise HTTPException(
            status_code=400,
            detail="Scope has no subnets registered — upload a subnet file first.",
        )

    # v2.337.0 — "Start Agentic Recon" now mints a unified PROJECT session and
    # opens a reconnaissance run on this scope. The session can go on to plan
    # and execute with the same key; this button just pre-selects the scope.
    from app.services.agent_session_service import (
        create_agent_session, resolve_project_agent, mint_session_key,
        open_recon_phase,
    )
    from app.services.agent_prompt_service import build_session_instructions

    agent = resolve_project_agent(db, project_id=project.id, user=current_user)
    session = create_agent_session(
        db, project_id=project.id, agent_id=agent.id,
        started_by_id=current_user.id, purpose=body.notes,
    )
    recon_session = open_recon_phase(db, session=session, scope=scope, notes=body.notes)
    raw_key = mint_session_key(db, agent=agent, session=session)
    integrations_decrypted = _load_active_integrations(db, user=current_user, project=project)
    instructions = build_session_instructions(
        request=request,
        session_id=session.id,
        project_id=project.id,
        project_name=project.name,
        purpose=body.notes,
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
        integrations=integrations_decrypted,
    )
    db.commit()
    db.refresh(recon_session)

    mcp_url = f"{resolve_base_url(request)}/mcp"
    return StartReconResponse(
        recon_session_id=recon_session.id,
        scope_id=scope.id,
        scope_name=scope.name,
        subnets=subnet_cidrs,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
        key_ttl_hours=resolve_ttl_hours(None),
        mcp_url=mcp_url,
        mcp_clients=build_mcp_clients(
            mcp_url, raw_key,
            expected={
                "project_name": project.name,
                "session_label": f"agent session #{session.id}",
            },
        ),
    )


@router.post(
    "/{scope_id}/recon/sessions/{session_id}/resume",
    response_model=StartReconResponse,
    status_code=201,
    summary="Resume an interrupted reconnaissance session (v2.47.0)",
)
def resume_recon_session(
    scope_id: int,
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
):
    """Re-mint a recon API key for an existing, still-active recon session.

    When an operator's host crashes mid-recon, the ``ReconSession`` row
    stays ``active`` but the agent process and its API key are gone.
    Calling ``/recon/start`` again would create a *parallel* session,
    fragmenting the rolling host/scan counts and session attribution.
    This resumes the SAME session: a fresh session-bound key is minted
    and the instructions are rebuilt with a resume notice.  Ingestion is
    idempotent, so prior uploads are intact and deduped.

    Valid only for an ``active`` session — a terminal session
    (``completed`` / ``failed`` / ``abandoned``) returns 409; start a
    new one instead.  A resume checkpoint is appended to the session
    notes for the human-review trail.
    """
    from app.db.models_agent import ReconSession, ReconSessionStatus
    from datetime import datetime, timezone

    scope = (
        db.query(Scope)
        .filter(Scope.id == scope_id, Scope.project_id == project.id)
        .first()
    )
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")

    recon_session = (
        db.query(ReconSession)
        .filter(
            ReconSession.id == session_id,
            ReconSession.scope_id == scope.id,
            ReconSession.project_id == project.id,
        )
        .first()
    )
    if not recon_session:
        raise HTTPException(
            status_code=404, detail="Recon session not found for this scope"
        )
    if recon_session.status != ReconSessionStatus.ACTIVE.value:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot resume a recon session in "
                f"'{recon_session.status}' status — start a new one instead."
            ),
        )

    subnet_cidrs = [
        row[0] for row in db.query(Subnet.cidr).filter(Subnet.scope_id == scope.id).all()
    ]

    # v2.337.0 — resume mints a fresh session and re-opens the run under it so
    # a single live key continues the same recon run (its uploads are intact
    # and deduped).  v2.338.0 — the session it replaces is then ENDED, which is
    # what revokes the crashed agent's key; minting on the new session never
    # touched it, so resume used to leave two live credentials.
    from app.services.agent_session_service import (
        create_agent_session, resolve_project_agent, mint_session_key,
        supersede_agent_session,
    )
    from app.services.agent_prompt_service import build_session_instructions

    agent = resolve_project_agent(
        db, project_id=project.id, user=current_user, prefer_agent_id=recon_session.agent_id
    )
    session = create_agent_session(
        db, project_id=project.id, agent_id=agent.id, started_by_id=current_user.id,
        purpose=f"Resume recon on scope {scope.id}",
    )
    previous_session_id = recon_session.agent_session_id
    recon_session.agent_id = agent.id
    recon_session.agent_session_id = session.id
    db.flush()
    # After the re-point: the old session's remaining open work is closed,
    # this run is no longer part of it.
    supersede_agent_session(
        db, previous_session_id, successor=session, ended_by=current_user,
    )
    raw_key = mint_session_key(db, agent=agent, session=session)
    integrations_decrypted = _load_active_integrations(db, user=current_user, project=project)
    instructions = build_session_instructions(
        request=request,
        session_id=session.id,
        project_id=project.id,
        project_name=project.name,
        purpose=f"Resume recon on scope {scope.name}",
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
        integrations=integrations_decrypted,
        resumed=True,
    )

    resume_note = (
        f"[{datetime.now(timezone.utc).isoformat()}] Recon run resumed "
        f"by {current_user.full_name or current_user.username} "
        f"— fresh session #{session.id} + key; prior uploads preserved."
    )
    recon_session.notes = (
        f"{recon_session.notes}\n{resume_note}" if recon_session.notes else resume_note
    )[-8192:]

    db.commit()
    db.refresh(recon_session)

    mcp_url = f"{resolve_base_url(request)}/mcp"
    return StartReconResponse(
        recon_session_id=recon_session.id,
        scope_id=scope.id,
        scope_name=scope.name,
        subnets=subnet_cidrs,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
        key_ttl_hours=resolve_ttl_hours(None),
        mcp_url=mcp_url,
        mcp_clients=build_mcp_clients(
            mcp_url, raw_key,
            expected={
                "project_name": project.name,
                "session_label": f"agent session #{session.id}",
            },
        ),
    )
