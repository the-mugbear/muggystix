"""Named-asset inventory (v2.322.0).

    GET    /projects/{id}/names                 list (paged, filtered)
    GET    /projects/{id}/names/summary         counts for the page header
    POST   /projects/{id}/names/import          operator-supplied FQDN list
    GET    /projects/{id}/names/by-host/{hid}   names bound to one address
    GET    /projects/{id}/names/{name_id}       detail: addresses + evidence
    DELETE /projects/{id}/names/{name_id}       remove a name (analyst)

A name is an identity, a host is an address; the two link only through
observations (``dns_records``).  Everything address-shaped in these responses
is DERIVED per request — "currently resolves to" is the latest A/AAAA batch,
never a stored pointer — so nothing here can go stale when DNS moves.  Nothing
here resolves anything: every observation came from an upload.

Permission model mirrors scopes: any project member reads; Analyst+ imports
and deletes.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, require_project_role
from app.api.v1.endpoints.auth import get_current_user
from app.api.v1.endpoints.scopes import get_or_create_default_scope
from app.db import models
from app.db.models import DNS_ADDRESS_VALUED_TYPES, DNS_OBS_IMPORT, DNS_RESOLVING_TYPES
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.schemas.dns_names import (
    HostNameBinding,
    HostNamesResponse,
    NameAddress,
    NameDetail,
    NameImportRequest,
    NameImportResponse,
    NameObservation,
    NameRow,
    NamesSummary,
    SiblingName,
)
from app.schemas.pagination import Paginated
from app.services import dns_name_service as svc
from app.services.host_query_common import escape_like

logger = logging.getLogger(__name__)

router = APIRouter()

_STATES = ("all", "unresolved", "resolved", "in_scope", "out_of_scope", "wildcard", "shared")


def _apply_state_filter(q, state: str, project_id: int):
    n = models.DNSName
    if state == "unresolved":
        return q.filter(~svc.resolving_exists_condition())
    if state == "resolved":
        return q.filter(svc.resolving_exists_condition())
    if state == "in_scope":
        return q.filter(svc.name_in_scope_condition(project_id))
    if state == "out_of_scope":
        return q.filter(~svc.name_in_scope_condition(project_id))
    if state == "wildcard":
        return q.filter(n.kind == "wildcard")
    if state == "shared":
        return q.filter(svc.shared_address_condition(project_id))
    return q


def _rows_to_schema(
    db: Session, project_id: int, rows: List[tuple],
) -> List[NameRow]:
    """``rows`` are ``(DNSName, in_scope)`` tuples."""
    names = [r[0] for r in rows]
    states = svc.address_state_for_names(db, project_id, [n.id for n in names])
    all_ips = {ip for st in states.values() for ip in st.current}
    host_ids = svc.hosts_for_addresses(db, project_id, all_ips)
    shared = svc.names_per_address(db, project_id, all_ips)
    out: List[NameRow] = []
    for name, in_scope in rows:
        st = states[name.id]
        current = [
            NameAddress(
                **entry,
                host_id=host_ids.get(ip),
                shared_with=max(shared.get(ip, 1) - 1, 0),
            )
            for ip, entry in sorted(st.current.items())
        ]
        out.append(
            NameRow(
                id=name.id,
                fqdn=name.fqdn,
                kind=name.kind,
                in_scope=bool(in_scope),
                first_seen=name.first_seen,
                last_seen=name.last_seen,
                current_addresses=current,
                previous_address_count=len(st.previous),
                evidence=dict(sorted(st.evidence.items())),
                imported=DNS_OBS_IMPORT in st.evidence,
                resolved=bool(st.current),
            )
        )
    return out


@router.get("/", response_model=Paginated[NameRow], summary="List the project's named assets")
def list_names(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(None, description="Case-insensitive substring on the FQDN"),
    state: str = Query("all", description="all | unresolved | resolved | in_scope | out_of_scope | wildcard | shared"),
    sort: str = Query("fqdn", pattern="^(fqdn|last_seen|first_seen)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    if state not in _STATES:
        raise HTTPException(status_code=400, detail=f"state must be one of {', '.join(_STATES)}")
    n = models.DNSName
    in_scope = svc.name_in_scope_condition(project.id).label("in_scope")
    q = db.query(n, in_scope).filter(n.project_id == project.id)
    if search and search.strip():
        q = q.filter(n.fqdn.ilike(f"%{escape_like(search.strip().lower())}%", escape="\\"))
    q = _apply_state_filter(q, state, project.id)
    total = q.with_entities(func.count(n.id)).order_by(None).scalar() or 0
    col = {"fqdn": n.fqdn, "last_seen": n.last_seen, "first_seen": n.first_seen}[sort]
    q = q.order_by(col.desc().nullslast() if order == "desc" else col.asc().nullsfirst(), n.id.asc())
    rows = q.offset(skip).limit(limit).all()
    return Paginated.build(_rows_to_schema(db, project.id, rows), total, skip, limit)


@router.get("/summary", response_model=NamesSummary, summary="Counts for the names inventory header")
def names_summary(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    n = models.DNSName
    base = db.query(func.count(n.id)).filter(n.project_id == project.id)
    total = base.scalar() or 0
    resolved = base.filter(svc.resolving_exists_condition()).scalar() or 0
    in_scope = base.filter(svc.name_in_scope_condition(project.id)).scalar() or 0
    wildcards = base.filter(n.kind == "wildcard").scalar() or 0
    r = models.DNSRecord
    shared_addresses = (
        db.query(func.count())
        .select_from(
            db.query(r.value)
            .filter(r.project_id == project.id, r.record_type.in_(DNS_RESOLVING_TYPES))
            .group_by(r.value)
            .having(func.count(func.distinct(r.name_id)) > 1)
            .subquery()
        )
        .scalar() or 0
    )
    return NamesSummary(
        total=total, unresolved=max(total - resolved, 0), resolved=resolved,
        in_scope=in_scope, wildcards=wildcards, shared_addresses=shared_addresses,
    )


@router.post(
    "/import",
    response_model=NameImportResponse,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Import an operator-supplied FQDN list (no resolution, no hosts created)",
)
def import_names(
    body: NameImportRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
):
    """Each name becomes a DNSName plus one IMPORT observation; re-importing
    the same list changes nothing.  ``declare_scope`` is a separate, explicit
    decision that also adds the names to the project's domain scope."""
    scope = get_or_create_default_scope(db, project.id, user_id=current_user.id) if body.declare_scope else None
    try:
        stats = svc.import_names(
            db,
            project_id=project.id,
            raw_names=body.names,
            created_by_id=current_user.id,
            declare_scope=body.declare_scope,
            include_subdomains=body.include_subdomains,
            scope=scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return NameImportResponse(**stats)


@router.get("/by-host/{host_id}", response_model=HostNamesResponse, summary="Names bound to one address")
def names_for_host(
    host_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    host = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == project.id)
        .first()
    )
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    r, n = models.DNSRecord, models.DNSName
    in_scope = svc.name_in_scope_condition(project.id).label("in_scope")
    # is_current uses the ONE binding rule (dns_name_service
    # .current_binding_condition) so this view and host coverage agree: a
    # name that moved away from this address is "previously resolved here",
    # never "current".
    is_current = svc.current_binding_condition(r).label("is_current")
    rows = (
        db.query(n, in_scope, r.record_type, is_current, func.max(func.coalesce(r.observed_at, r.created_at)))
        .join(r, r.name_id == n.id)
        .filter(
            r.project_id == project.id,
            r.value == host.ip_address,
            r.record_type.in_(DNS_ADDRESS_VALUED_TYPES),
        )
        .group_by(n.id, r.record_type, is_current)
        .all()
    )
    grouped: Dict[int, dict] = {}
    for name, scoped, rtype, cur, last in rows:
        g = grouped.setdefault(
            name.id,
            {"name": name, "in_scope": bool(scoped), "types": set(), "last": None,
             "current": False, "historical": False},
        )
        g["types"].add(rtype)
        if rtype in DNS_RESOLVING_TYPES:
            if cur:
                g["current"] = True
            else:
                g["historical"] = True
        if last is not None and (g["last"] is None or svc._cmp_ts(last, g["last"]) > 0):
            g["last"] = last
    current: List[HostNameBinding] = []
    previous: List[HostNameBinding] = []
    other: List[HostNameBinding] = []
    for g in sorted(grouped.values(), key=lambda x: x["name"].fqdn):
        binding = HostNameBinding(
            name_id=g["name"].id, fqdn=g["name"].fqdn, kind=g["name"].kind,
            in_scope=g["in_scope"], record_types=sorted(g["types"]), last_observed=g["last"],
        )
        if g["current"]:
            current.append(binding)
        elif g["historical"]:
            previous.append(binding)
        else:
            other.append(binding)
    return HostNamesResponse(
        host_id=host.id,
        current=current,
        previous=previous,
        other=other,
        in_scope_via_names=any(b.in_scope and b.kind == "fqdn" for b in current),
    )


@router.get("/{name_id}", response_model=NameDetail, summary="One name: addresses, evidence, siblings")
def get_name(
    name_id: int,
    observations_limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    n = models.DNSName
    row = (
        db.query(n, svc.name_in_scope_condition(project.id).label("in_scope"))
        .filter(n.id == name_id, n.project_id == project.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Name not found")
    name, in_scope = row
    base = _rows_to_schema(db, project.id, [(name, in_scope)])[0]

    st = svc.address_state_for_names(db, project.id, [name.id])[name.id]
    prev_ips = set(st.previous)
    host_ids = svc.hosts_for_addresses(db, project.id, prev_ips)
    shared = svc.names_per_address(db, project.id, prev_ips)
    previous = [
        NameAddress(**entry, host_id=host_ids.get(ip), shared_with=max(shared.get(ip, 1) - 1, 0))
        for ip, entry in sorted(st.previous.items())
    ]

    r = models.DNSRecord
    obs_total = db.query(func.count(r.id)).filter(r.name_id == name.id).scalar() or 0
    obs_rows = (
        db.query(r, models.Scan.tool_name, models.Scan.filename)
        .outerjoin(models.Scan, models.Scan.id == r.scan_id)
        .filter(r.name_id == name.id)
        .order_by(func.coalesce(r.observed_at, r.created_at).desc(), r.id.desc())
        .limit(observations_limit)
        .all()
    )
    obs_ips = {o.value for o, _, _ in obs_rows if o.record_type in DNS_ADDRESS_VALUED_TYPES}
    obs_hosts = svc.hosts_for_addresses(db, project.id, obs_ips)
    observations = [
        NameObservation(
            id=o.id, record_type=o.record_type, value=o.value, domain=o.domain, ttl=o.ttl,
            resolver_name=o.resolver_name, scan_id=o.scan_id, scan_tool=tool, scan_filename=fname,
            observed_at=o.observed_at or o.created_at,
            host_id=obs_hosts.get(o.value) if o.record_type in DNS_ADDRESS_VALUED_TYPES else None,
        )
        for o, tool, fname in obs_rows
    ]

    siblings: List[SiblingName] = []
    if st.current:
        sib_rows = (
            db.query(n.id, n.fqdn, r.value)
            .join(r, r.name_id == n.id)
            .filter(
                r.project_id == project.id,
                r.record_type.in_(DNS_RESOLVING_TYPES),
                r.value.in_(list(st.current)),
                n.id != name.id,
            )
            .distinct()
            .order_by(n.fqdn.asc())
            .limit(200)
            .all()
        )
        siblings = [SiblingName(id=i, fqdn=f, ip_address=v) for i, f, v in sib_rows]

    return NameDetail(
        **base.model_dump(),
        previous_addresses=previous,
        observations=observations,
        observations_total=obs_total,
        sibling_names=siblings,
    )


@router.delete(
    "/{name_id}",
    status_code=204,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Remove a name and its observations",
)
def delete_name(
    name_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Deletes the name row; its observations cascade.  Hosts are untouched —
    an address is never removed because a name was.

    v2.325.0 — a name that plan entries or finding-hosts still reference is
    NOT deletable (409).  Those rows are unique per (…, host, name) and
    ``SET NULL`` on delete would collapse a named endpoint onto the unnamed
    row for the same host — a constraint violation at best, silent loss of a
    reviewed association at worst.  Detach the references first (remove the
    entry / detach the endpoint from the finding); web-interface and
    scanner rows are plain evidence pointers and simply lose the link.
    """
    from app.db.models_agent import TestPlanEntry
    from app.db.models_findings import FindingHost

    name = (
        db.query(models.DNSName)
        .filter(models.DNSName.id == name_id, models.DNSName.project_id == project.id)
        .first()
    )
    if not name:
        raise HTTPException(status_code=404, detail="Name not found")
    entry_refs = db.query(func.count(TestPlanEntry.id)).filter(TestPlanEntry.name_id == name.id).scalar() or 0
    finding_refs = db.query(func.count(FindingHost.id)).filter(FindingHost.name_id == name.id).scalar() or 0
    if entry_refs or finding_refs:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{name.fqdn} is still referenced by {entry_refs} plan entr{'y' if entry_refs == 1 else 'ies'} "
                f"and {finding_refs} finding endpoint{'' if finding_refs == 1 else 's'}. Detach those first; "
                "a named endpoint is never merged into the unnamed record for its host."
            ),
        )
    db.delete(name)
    db.commit()
    return Response(status_code=204)
