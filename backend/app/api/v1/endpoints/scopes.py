import logging
import os
import tempfile
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Form, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased
from pydantic import BaseModel, Field
from app.db import models
from app.db.session import get_db
from app.db.models import Scope, Subnet, HostSubnetMapping, SubnetLabel, SubnetLabelAssignment
from app.services.host_query_common import escape_like
from app.api.v1.endpoints.auth import get_current_user, require_role
from app.db.models_auth import User, UserRole
from app.api.deps import get_current_project, require_project_role
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
from app.services.agent_key_ttl import resolve_expires_at, resolve_ttl_hours
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

@router.post("/upload-subnets", response_model=SubnetFileUploadResponse, dependencies=[Depends(require_project_role(ProjectRole.ANALYST))])
async def upload_subnet_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Upload a subnet file and append its entries to the project's scope.

    Per v2.9.4, a project has exactly one conceptual scope.  Every
    uploaded file's CIDRs (and single IPs, which are accepted via
    ``ipaddress.ip_network(strict=False)``) are appended to that
    scope.  Duplicate entries already present in the scope are
    silently skipped so re-uploading the same file is idempotent.
    """
    # Audit finding C3: the previous implementation read the entire
    # upload into memory via ``await file.read()`` with no size check,
    # so an authenticated analyst could OOM a worker with a 2GB file.
    # Cap at 2MB (fits ~50K CIDRs comfortably) and reject oversize
    # before allocating.  Keep the cap in sync with the frontend
    # upload dialog copy on the Scopes page.
    MAX_SUBNET_FILE_BYTES = 2 * 1024 * 1024  # 2 MB
    MAX_SUBNETS_PER_UPLOAD = 10_000

    allowed_extensions = ['.txt', '.csv']
    if not any(file.filename.lower().endswith(ext) for ext in allowed_extensions):
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Supported types: {', '.join(allowed_extensions)}"
        )

    content = await file.read()
    if len(content) > MAX_SUBNET_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File too large ({len(content):,} bytes). "
                f"Maximum allowed: {MAX_SUBNET_FILE_BYTES:,} bytes "
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

    # .csv → row-per-subnet with optional space-delimited labels in column 2;
    # .txt → flat CIDR list (no labels).  Both normalize identically.
    is_csv = file.filename.lower().endswith('.csv')
    try:
        parser = SubnetParser(db)
        if is_csv:
            entries = parser.parse_subnet_label_csv(file_content)
        else:
            entries = [(c, []) for c in parser.parse_cidr_list(file_content)]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if len(entries) > MAX_SUBNETS_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File contains {len(entries):,} subnet entries; "
                f"maximum per upload is {MAX_SUBNETS_PER_UPLOAD:,}. "
                f"Split the file into smaller uploads or use the manual "
                f"Add Subnet flow for individual entries."
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
    for cidr, _labels in entries:
        if cidr not in existing_subnets:
            sub = Subnet(cidr=cidr, scope_id=scope.id)
            db.add(sub)
            db.flush()  # need sub.id for label assignments
            existing_subnets[cidr] = sub
            added += 1

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

    affected_ids = [existing_subnets[c].id for c, _ in entries]
    existing_assignments = set()
    if affected_ids:
        for sid, lid in (
            db.query(SubnetLabelAssignment.subnet_id, SubnetLabelAssignment.label_id)
            .filter(SubnetLabelAssignment.subnet_id.in_(affected_ids))
            .all()
        ):
            existing_assignments.add((sid, lid))

    labels_applied = 0
    for cidr, label_names in entries:
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

    db.commit()

    correlation_service = SubnetCorrelationService(db)
    correlation_service.invalidate_subnet_cache()
    correlated_hosts = None
    try:
        correlated_hosts = correlation_service.correlate_all_hosts_to_subnets(project_id=project.id)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Subnet correlation after upload failed: %s", exc)

    skipped = len(entries) - added
    message = f"Added {added} subnet(s) to the project scope"
    if skipped > 0:
        message += f" ({skipped} duplicate{'s' if skipped != 1 else ''} skipped)"
    if labels_applied > 0:
        message += f"; applied {labels_applied} label assignment{'s' if labels_applied != 1 else ''}"
    if correlated_hosts is not None:
        message += f"; correlated {correlated_hosts} host-subnet relationships"

    return SubnetFileUploadResponse(
        message=message,
        scope_id=scope.id,
        subnets_added=added,
        filename=file.filename
    )

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

    return {
        "id": scope.id,
        "name": scope.name,
        "description": scope.description,
        "created_at": scope.created_at,
        "updated_at": scope.updated_at,
        "subnets": subnet_rows,
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

    out_of_scope_count = max(total_hosts - scoped_hosts, 0)
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
        total_hosts=total_hosts,
        scoped_hosts=scoped_hosts,
        out_of_scope_hosts=out_of_scope_count,
        coverage_percentage=coverage_percentage,
        has_scope_configuration=total_subnets > 0,
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

@router.post(
    "/",
    response_model=ScopeSchema,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Insufficient permissions — analyst role required"},
    },
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Create scope (analyst)",
)
def create_scope(
    scope: ScopeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Create a new empty scope. Requires analyst role."""
    # Check if scope name already exists within this project
    existing_scope = db.query(Scope).filter(Scope.name == scope.name, Scope.project_id == project.id).first()
    if existing_scope:
        raise HTTPException(
            status_code=400,
            detail=f"Scope with name '{scope.name}' already exists"
        )

    db_scope = Scope(**scope.dict(), uploaded_by_id=current_user.id, project_id=project.id)
    db.add(db_scope)
    db.commit()
    db.refresh(db_scope)

    return db_scope

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


@router.patch(
    "/{scope_id}",
    response_model=ScopeSchema,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Edit scope metadata (analyst)",
)
def update_scope(
    scope_id: int,
    body: ScopeUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Edit a scope's name and/or description."""
    scope = db.query(Scope).filter(Scope.id == scope_id, Scope.project_id == project.id).first()
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found")

    if body.name is not None and body.name != scope.name:
        collision = (
            db.query(Scope)
            .filter(
                Scope.name == body.name,
                Scope.project_id == project.id,
                Scope.id != scope_id,
            )
            .first()
        )
        if collision:
            raise HTTPException(
                status_code=400,
                detail=f"Scope with name {body.name!r} already exists in this project",
            )
        scope.name = body.name
    if body.description is not None:
        scope.description = body.description

    db.commit()
    db.refresh(scope)
    return scope


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
        normalized.append((cidr, item.description))

    # Duplicate check inside the payload and against existing DB rows.
    seen: set = set()
    existing_cidrs = {
        row.cidr
        for row in db.query(Subnet).filter(Subnet.scope_id == scope_id).all()
    }
    for cidr, _ in normalized:
        if cidr in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate CIDR in request: {cidr}")
        if cidr in existing_cidrs:
            raise HTTPException(
                status_code=400,
                detail=f"CIDR {cidr} already exists in this scope",
            )
        seen.add(cidr)

    created: List[Subnet] = []
    for cidr, description in normalized:
        row = Subnet(cidr=cidr, description=description, scope_id=scope_id)
        db.add(row)
        created.append(row)
    db.commit()
    for row in created:
        db.refresh(row)

    # Re-correlate so hosts already in the database get mapped to the
    # new subnets.  This matches the behaviour of file-based upload.
    SubnetCorrelationService(db).correlate_all_hosts_to_subnets(project_id=project.id)

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

    db.commit()
    db.refresh(subnet)

    if cidr_changed:
        # Drop stale mappings on the edited subnet; correlator recomputes
        # them from scratch for this subnet's new CIDR.
        db.query(HostSubnetMapping).filter(HostSubnetMapping.subnet_id == subnet_id).delete(
            synchronize_session=False
        )
        db.commit()
        SubnetCorrelationService(db).correlate_all_hosts_to_subnets(project_id=project.id)

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


# ---------------------------------------------------------------------------
# Recon-session lifecycle helpers — shared by recon start + resume so the
# agent-resolution, integration-loading and key-minting plumbing lives in
# one place.
# ---------------------------------------------------------------------------

def _resolve_recon_agent(db, *, project, user, prefer_agent_id=None):
    """Resolve the agent for a recon session.

    Prefers ``prefer_agent_id`` (the session's original agent, on
    resume), then the user's project agent, auto-provisioning one if
    neither exists.  Reactivates a deactivated agent.
    """
    from app.db.models_agent import Agent
    agent = None
    if prefer_agent_id is not None:
        agent = db.query(Agent).filter(Agent.id == prefer_agent_id).first()
    if agent is None:
        agent = (
            db.query(Agent)
            .filter(Agent.project_id == project.id, Agent.owner_id == user.id)
            .first()
        )
    if agent is not None:
        if not agent.is_active:
            agent.is_active = True
        return agent
    agent = Agent(
        name=f"{user.username}-agent",
        project_id=project.id,
        owner_id=user.id,
        description="Auto-provisioned for agentic reconnaissance",
    )
    db.add(agent)
    db.flush()
    return agent


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


def _mint_recon_session_key(
    db,
    *,
    agent,
    scope,
    recon_session,
    name_suffix="",
    ttl_hours: Optional[int] = None,
):
    """Mint a fresh recon-scoped, session-pinned API key; return the
    plaintext key.

    Revokes any prior active key bound to *this* recon session first, so
    a resumed session never has two live keys — without that, the
    crashed agent's key would stay usable and a second agent could write
    into the same recon session.  Keys for *other* recon sessions on the
    same scope are untouched, keeping concurrent recon isolated.
    """
    from app.db.models_auth import APIKey
    from app.core.config import settings as _settings
    import hashlib
    import secrets
    from datetime import datetime, timezone, timedelta

    db.query(APIKey).filter(
        APIKey.recon_session_id == recon_session.id,
        APIKey.is_active.is_(True),
    ).update({"is_active": False}, synchronize_session=False)

    raw_key = f"nm_agent_{secrets.token_urlsafe(32)}"
    db.add(
        APIKey(
            agent_id=agent.id,
            scope_id=scope.id,
            recon_session_id=recon_session.id,
            name=f"recon-session-{recon_session.id}{name_suffix}",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            key_prefix=raw_key[:14],
            expires_at=resolve_expires_at(ttl_hours),
        )
    )
    return raw_key


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

    # Resolve the agent, create the recon session, mint a session-pinned
    # key.  See the recon-session lifecycle helpers above.
    from app.db.models_agent import ReconSession, ReconSessionStatus
    agent = _resolve_recon_agent(db, project=project, user=current_user)

    # Create the recon session first so the API key can pin to it.
    # v2.45.0 — the key is bound to THIS session, not just the scope;
    # see APIKey.recon_session_id for the concurrent-recon rationale.
    recon_session = ReconSession(
        project_id=project.id,
        scope_id=scope.id,
        agent_id=agent.id,
        started_by_id=current_user.id,
        status=ReconSessionStatus.ACTIVE.value,
        notes=body.notes,
    )
    db.add(recon_session)
    db.flush()

    raw_key = _mint_recon_session_key(
        db, agent=agent, scope=scope, recon_session=recon_session
    )
    integrations_decrypted = _load_active_integrations(
        db, user=current_user, project=project
    )

    from app.services.agent_prompt_service import build_recon_ingest_instructions
    instructions = build_recon_ingest_instructions(
        request=request,
        recon_session_id=recon_session.id,
        scope_id=scope.id,
        scope_name=scope.name,
        subnets=subnet_cidrs,
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
        integrations=integrations_decrypted,
        project_slug=project.slug,
    )

    db.commit()
    db.refresh(recon_session)

    return StartReconResponse(
        recon_session_id=recon_session.id,
        scope_id=scope.id,
        scope_name=scope.name,
        subnets=subnet_cidrs,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
        key_ttl_hours=resolve_ttl_hours(None),
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

    # Reuse the session's original agent and mint a fresh session-pinned
    # key.  Minting revokes the prior (orphaned) key — load-bearing so a
    # second agent cannot write into this resumed recon session.
    agent = _resolve_recon_agent(
        db, project=project, user=current_user, prefer_agent_id=recon_session.agent_id
    )
    recon_session.agent_id = agent.id
    raw_key = _mint_recon_session_key(
        db, agent=agent, scope=scope, recon_session=recon_session, name_suffix="-resume"
    )
    integrations_decrypted = _load_active_integrations(
        db, user=current_user, project=project
    )

    from app.services.agent_prompt_service import build_recon_ingest_instructions
    instructions = build_recon_ingest_instructions(
        request=request,
        recon_session_id=recon_session.id,
        scope_id=scope.id,
        scope_name=scope.name,
        subnets=subnet_cidrs,
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
        integrations=integrations_decrypted,
        resumed=True,
        project_slug=project.slug,
    )

    # Checkpoint note for the human-review trail — 8 KiB cap, newest kept.
    resume_note = (
        f"[{datetime.now(timezone.utc).isoformat()}] Recon session resumed "
        f"by {current_user.full_name or current_user.username} "
        f"— fresh API key minted; prior uploads preserved."
    )
    recon_session.notes = (
        f"{recon_session.notes}\n{resume_note}"
        if recon_session.notes
        else resume_note
    )[-8192:]

    db.commit()
    db.refresh(recon_session)

    return StartReconResponse(
        recon_session_id=recon_session.id,
        scope_id=scope.id,
        scope_name=scope.name,
        subnets=subnet_cidrs,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
        key_ttl_hours=resolve_ttl_hours(None),
    )
