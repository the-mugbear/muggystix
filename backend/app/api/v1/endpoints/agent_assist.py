"""
Agent API — interactive assist workflow (v2.64.0).

Read-only, project-scoped surface for "ask questions about hosts"
agents.  Designed to support the senior-tester use case where the
operator wants to query their project — "which hosts expose FTP?",
"summarize my critical findings", "what did the last recon turn up?"
— without minting a plan key and triggering plan-approval ceremony.

Since v2.337.0 a key binds to one project-scoped ``AgentSession`` and these
endpoints are gated like every agent route: the router-level
``enforce_agent_operator_access`` and the operator's project role (the
per-workflow ``require_assist_scope`` guard is gone).

Scope of v1 (this file): read-only.  No execution authority, no
plan creation, no follow mutation.  Future work (bulk-follow, scan-
from-filter) tracked in CHANGELOG and may add WRITE endpoints
behind their own approval/confirmation surface.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased, joinedload, selectinload, Query as SAQuery

from app.db.session import disable_statement_timeout, get_db
from app.db import models
from app.db.models_agent import (
    Agent,
    AgentSession,
)
from app.db.models_project import Project, ProjectMembership
from app.db.models_auth import User
from app.api.deps import check_agent_rate_limit

from app.api.v1.endpoints.agent_schemas import (
    AssistFinding,
    AssistFindingsResponse,
    AssistNameRow,
    AssistNamesResponse,
    ScopeDomainBrief,
    HostBrief,
    HostBriefPage,
    HostDetail,
    PortBrief,
    ScanBrief,
    ScopeBrief,
    VulnCounts,
)
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, severity_rank
from app.api.v1.endpoints.agent_common import (
    PORTS_PARAM_HELP,
    SERVICES_PARAM_HELP,
    apply_agent_host_filters,
    batch_host_enrichment,
    load_agent_session,
)
from app.services import dns_name_service, host_detail_service
from app.services.attribution_correlation import attributions_for_host
from app.services.host_assessment_service import host_assessment
from app.services.host_query import (
    HOST_SORT_FIELDS,
    WEAKNESS_LABELS,
    apply_host_sorting,
    host_weakness_flags,
)
from app.services.host_serialization import (
    _vuln_coverage,
    exploit_count_maps,
    issue_coverage_map,
    note_load_options,
    serialize_attribution,
    serialize_cert_facts,
    serialize_host_base,
)
from app.services.scope_coverage import host_scope_membership
from app.services.host_query_common import escape_like
from app.services.note_attachment_service import require_readable_file
from app.services.agent_prompt_history import PROMPT_VERSION
from app.services.posture_service import compute_posture
from app.services.scan_inventory_filters import apply_scan_inventory_filters
from app.services.systemic_insight_service import compute_systemic_insights
from app.services.subnet_insight_service import compute_subnet_insights

router = APIRouter()


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------

def _load_assist_session(db: Session, request: Request) -> AgentSession:
    """The caller's unified agent session (v2.337.0).

    The ``/agent/assist/*`` reads are project-wide and available to every
    session — a query is a query.  This resolves the session the key belongs
    to; handlers read ``session.project_id`` off it exactly as before.  The
    name is kept so the many call sites need no edit.
    """
    return load_agent_session(db, request)


# ---------------------------------------------------------------------------
# Context — project overview
# ---------------------------------------------------------------------------

@router.get(
    "/assist/context",
    summary="Project context — host/scan/scope summary the assist agent grounds queries in",
)
def get_assist_context(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Single endpoint giving the agent enough project-level
    grounding to answer ad-hoc questions without N+1 chatter:
    project metadata, host count, scope list, recent scan summary,
    recent recon session summary.

    Sized to fit comfortably in a typical agent context window
    (counts and headlines, not raw row dumps).  When the agent
    needs detail it follows up with /assist/hosts or /assist/scopes.
    """
    session = _load_assist_session(db, request)
    project = db.query(Project).filter(Project.id == session.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Host + port counts
    host_count = (
        db.query(func.count(models.Host.id))
        .filter(models.Host.project_id == project.id)
        .scalar()
        or 0
    )
    up_count = (
        db.query(func.count(models.Host.id))
        .filter(models.Host.project_id == project.id, models.Host.state == "up")
        .scalar()
        or 0
    )
    open_port_count = (
        db.query(func.count(models.Port.id))
        .join(models.Host, models.Port.host_id == models.Host.id)
        .filter(models.Host.project_id == project.id, models.Port.state == "open")
        .scalar()
        or 0
    )

    # Recent scans (5)
    recent_scans = (
        db.query(models.Scan)
        .filter(models.Scan.project_id == project.id)
        .order_by(models.Scan.created_at.desc())
        .limit(5)
        .all()
    )

    # Scope list (capped at 50 — projects with many scopes get a
    # follow-up call to /assist/scopes for the full list)
    scopes = (
        db.query(models.Scope)
        .filter(models.Scope.project_id == project.id)
        .order_by(models.Scope.name)
        .limit(50)
        .all()
    )
    scope_count_total = (
        db.query(func.count(models.Scope.id))
        .filter(models.Scope.project_id == project.id)
        .scalar()
        or 0
    )
    # Agent feedback (v1.44.0): totals is documented as the authoritative count
    # source but omitted scan_count, forcing scan-count questions onto a second
    # call. Include it here alongside the other totals.
    scan_count_total = (
        db.query(func.count(models.Scan.id))
        .filter(models.Scan.project_id == project.id)
        .scalar()
        or 0
    )

    # v2.330.0 — name scope.  Three COUNTs: how many names the project knows,
    # how many a declared domain covers, and how many of THOSE have never
    # resolved.  The last one is the actionable figure: an in-scope name with
    # no A/AAAA answer is work the operator still owes (resolve it, or drop it
    # from scope) — see /assist/names?in_scope=true&resolved=false.
    domain_count_total = (
        db.query(func.count(models.ScopeDomain.id))
        .join(models.Scope, models.Scope.id == models.ScopeDomain.scope_id)
        .filter(models.Scope.project_id == project.id)
        .scalar()
        or 0
    )
    _n = models.DNSName
    _names_base = db.query(func.count(_n.id)).filter(_n.project_id == project.id)
    names_total = _names_base.scalar() or 0
    names_in_scope = (
        _names_base.filter(dns_name_service.name_in_scope_condition(project.id)).scalar() or 0
        if domain_count_total else 0
    )
    names_in_scope_unresolved = (
        _names_base.filter(
            dns_name_service.name_in_scope_condition(project.id),
            ~dns_name_service.resolving_exists_condition(),
        ).scalar() or 0
        if names_in_scope else 0
    )

    # Who is on the engagement, with their project role — the Project
    # settings page's member list.  One join; a project has tens of members.
    members = [
        {
            "user_id": uid,
            "username": username,
            "full_name": full_name,
            "role": role,
        }
        for uid, username, full_name, role in (
            db.query(User.id, User.username, User.full_name, ProjectMembership.role)
            .join(ProjectMembership, ProjectMembership.user_id == User.id)
            .filter(ProjectMembership.project_id == project.id)
            .order_by(User.username)
            .all()
        )
    ]

    return {
        "prompt_version": PROMPT_VERSION,
        "session": {
            "id": session.id,
            "purpose": session.purpose,
            "started_at": session.started_at.isoformat() if session.started_at else None,
        },
        "project": {
            "id": project.id,
            "name": project.name,
            "slug": project.slug,
            "description": project.description,
            "status": project.status,
            # The engagement window as the Project settings page states it
            # (null when not set) — the dates a report's period is taken from.
            "start_date": project.start_date.isoformat() if project.start_date else None,
            "end_date": project.end_date.isoformat() if project.end_date else None,
        },
        "members": members,
        "totals": {
            "host_count": host_count,
            "up_host_count": up_count,
            "open_port_count": open_port_count,
            "scope_count": scope_count_total,
            "scan_count": scan_count_total,
            # Declared domain-scope entries across the project's scopes.
            "domain_count": domain_count_total,
        },
        # Name scope is independent of subnet scope: an in-scope name does not
        # put the address it resolves to in scope.  ``in_scope_unresolved`` is
        # the actionable number — names approved for testing that have never
        # resolved in any upload.
        "names": {
            "total": names_total,
            "in_scope": names_in_scope,
            "in_scope_unresolved": names_in_scope_unresolved,
        },
        "scopes": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
            }
            for s in scopes
        ],
        "scopes_truncated": scope_count_total > len(scopes),
        "recent_scans": [
            {
                "id": s.id,
                "filename": s.filename,
                "tool_name": s.tool_name,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in recent_scans
        ],
    }


# ---------------------------------------------------------------------------
# Hosts — list + detail
# ---------------------------------------------------------------------------

def _build_assist_host_query(
    db: Session,
    session: AgentSession,
    *,
    state: Optional[str],
    ports: Optional[str],
    services: Optional[str],
    subnets: Optional[str],
    has_critical_vulns: Optional[bool],
    has_high_vulns: Optional[bool],
    search: Optional[str],
    q: Optional[str],
) -> SAQuery:
    """Build the filtered, project-scoped host query shared by the paged list
    and the NDJSON stream. Both surfaces MUST filter identically, so the discrete
    params + the boolean DSL live here once. Raises HTTPException(400) on a
    malformed DSL query.
    """
    query = db.query(models.Host).filter(models.Host.project_id == session.project_id)
    query = apply_agent_host_filters(
        query,
        db,
        project_id=session.project_id,
        state=state,
        ports=ports,
        services=services,
        subnets=subnets,
        has_critical_vulns=has_critical_vulns,
        has_high_vulns=has_high_vulns,
        search=search,
    )
    if q:
        # Boolean DSL — same parser/evaluator as the human Hosts page, bound to
        # the session operator so follow:/assigned: are answerable. Lazy import
        # keeps the module-load graph acyclic; a malformed query is a clean 400.
        from app.services.host_query_dsl import BuildCtx, DSLError, evaluate, parse_query
        operator = session.started_by
        if operator is None:
            raise HTTPException(
                status_code=400,
                detail="Assist session has no operator bound; cannot evaluate follow:/assigned: predicates.",
            )
        try:
            query = query.filter(
                evaluate(parse_query(q), BuildCtx(db, operator, session.project_id))
            )
        except DSLError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid query: {exc}")
    return query


def _operator_follow_map(db: Session, host_ids, operator_id) -> dict:
    """``host_id -> the session operator's follow status`` ('watching' /
    'in_review' / 'reviewed'). Absent = the operator doesn't follow the host
    (equivalent to ``follow:none``). Surfaced so an assist agent can check a
    human's review state before writing follow, instead of running three DSL
    queries per host (agent feedback, v1.44.0)."""
    if not host_ids or operator_id is None:
        return {}
    rows = (
        db.query(models.HostFollow.host_id, models.HostFollow.status)
        .filter(
            models.HostFollow.host_id.in_(host_ids),
            models.HostFollow.user_id == operator_id,
        )
        .all()
    )
    return {
        host_id: (status.value if hasattr(status, "value") else str(status))
        for host_id, status in rows
    }


def _host_to_brief_dict(
    h: models.Host, port_counts: dict, vuln_map: dict, follow_map: dict = None,
    exploit_maps: tuple = ({}, {}),
) -> dict:
    """Serialize one host to the HostBrief-shaped dict used by the NDJSON stream."""
    vc = vuln_map.get(h.id, {})
    exploits, critical_exploits = exploit_maps
    return {
        "id": h.id,
        "ip_address": h.ip_address,
        "hostname": h.hostname,
        "state": h.state,
        "os_name": h.os_name,
        "os_family": h.os_family,
        "first_seen": h.first_seen.isoformat() if h.first_seen else None,
        "last_seen": h.last_seen.isoformat() if h.last_seen else None,
        "open_port_count": port_counts.get(h.id, 0),
        "vuln_summary": {
            "critical": vc.get("critical", 0),
            "high": vc.get("high", 0),
            "medium": vc.get("medium", 0),
            "low": vc.get("low", 0),
        }
        if vc
        else None,
        "exploitable_count": exploits.get(h.id, 0),
        "critical_exploitable_count": critical_exploits.get(h.id, 0),
        "follow": (follow_map or {}).get(h.id),
    }


def _iter_assist_hosts_ndjson(db: Session, query: SAQuery, operator_id=None):
    """Yield every matching host as one JSON object per line, paged so a
    project with thousands of hosts streams in bounded memory instead of
    materialising the whole ORM result set (mirrors the recon download valve).
    """
    _PAGE = 500
    offset = 0
    ordered = query.order_by(models.Host.ip_address)
    while True:
        hosts = ordered.offset(offset).limit(_PAGE).all()
        if not hosts:
            break
        host_ids = [h.id for h in hosts]
        port_counts, vuln_map = batch_host_enrichment(db, host_ids)
        follow_map = _operator_follow_map(db, host_ids, operator_id)
        exploit_maps = exploit_count_maps(db, host_ids)
        for h in hosts:
            yield json.dumps(_host_to_brief_dict(h, port_counts, vuln_map, follow_map, exploit_maps)) + "\n"
        if len(hosts) < _PAGE:
            break
        offset += _PAGE
        # Detach the page so the session doesn't accumulate every host.
        db.expunge_all()


class AssistHostCount(BaseModel):
    """Answer to a "how many hosts …?" question."""
    count: int
    # Echoed so the agent can quote the question it actually asked when it
    # reports the number — and so a wrong answer is traceable to a wrong query.
    query: Optional[str] = None


@router.get(
    "/assist/hosts/count",
    response_model=AssistHostCount,
    summary="Count hosts matching a filter — without paging them",
)
def count_assist_hosts(
    request: Request,
    state: Optional[str] = Query(None),
    ports: Optional[str] = Query(None, description=PORTS_PARAM_HELP),
    services: Optional[str] = Query(None, description=SERVICES_PARAM_HELP),
    subnets: Optional[str] = Query(None, description="Comma-separated CIDR blocks"),
    has_critical_vulns: Optional[bool] = Query(None),
    has_high_vulns: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Search IP, hostname, or OS"),
    q: Optional[str] = Query(None, description="Boolean query DSL — see /assist/hosts."),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """How many hosts match — the whole answer to a counting question.

    v2.291.0.  ``/assist/hosts`` returns a bare list with no total, so "how many
    hosts have critical findings and no assignee?" could only be answered by
    paging to exhaustion.  That is expensive, and its failure mode is the worst
    possible one for a question whose entire answer is a number: an agent that
    stops at the first page reports a confident, wrong count.  A COUNT(*) makes
    the question one call and the answer exact.

    Shares ``_build_assist_host_query`` with the list endpoint, so the filters,
    the DSL, and the session's row scope cannot drift between "which hosts" and
    "how many hosts" — two answers to the same question disagreeing is precisely
    what a separate query here would eventually produce.
    """
    session = _load_assist_session(db, request)
    query = _build_assist_host_query(
        db, session,
        state=state, ports=ports, services=services, subnets=subnets,
        has_critical_vulns=has_critical_vulns, has_high_vulns=has_high_vulns,
        search=search, q=q,
    )
    return AssistHostCount(
        count=query.with_entities(func.count(models.Host.id.distinct())).scalar() or 0,
        query=q,
    )


@router.get(
    "/assist/hosts",
    response_model=HostBriefPage,
    summary="List hosts — same filter shape as the host inventory page; {items, total, has_more}",
)
def list_assist_hosts(
    request: Request,
    state: Optional[str] = Query(None),
    ports: Optional[str] = Query(None, description=PORTS_PARAM_HELP),
    services: Optional[str] = Query(None, description=SERVICES_PARAM_HELP),
    subnets: Optional[str] = Query(None, description="Comma-separated CIDR blocks"),
    has_critical_vulns: Optional[bool] = Query(None),
    has_high_vulns: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Search IP, hostname, or OS"),
    q: Optional[str] = Query(
        None,
        description=(
            "Boolean query DSL — the SAME vocabulary as the Hosts page "
            "(e.g. port, os, service, subnet, cve, check, has:, follow:, "
            "assigned:; the full field list is in the agent guide's assist "
            "slice, and GET /agent/assist/vocabulary gives this project's "
            "tag / label / site / username values). "
            "Combine with AND / OR / NOT and parentheses; comma = OR within a "
            "field, a repeated field = AND. ANDs with the discrete filters "
            "above. follow: and assigned: resolve against the operator who "
            "started this session — e.g. "
            "'follow:in_review' = hosts you have in review, 'assigned:me'. "
            "A malformed query returns 400."
        ),
    ),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    sort_by: str = Query(
        "ip_address", pattern=f"^({'|'.join(HOST_SORT_FIELDS)})$",
        description=(
            "The Hosts page's sort keys. ip_address (default, by address); "
            "critical_vulns / high_vulns / exploitable_vulns for 'worst first'."
        ),
    ),
    sort_order: str = Query("asc", pattern="^(asc|desc)$", description="asc or desc (use desc for 'worst first')."),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Project-scoped host list with the same filter vocabulary as the
    human Hosts page.  Returns HostBrief (id, ip, hostname, state, OS,
    open-port count, vuln summary) — single round-trip surface for
    "which hosts match $criteria?" questions.

    Two filter surfaces, ANDed together:
    - the discrete params (``state``/``ports``/``services``/…), and
    - ``q``, the full boolean query DSL.  ``q`` is what lets an assist
      agent express the rich, operator-relative questions — "hosts I have
      in review" (``follow:in_review``), "assigned to me" (``assigned:me``),
      "Log4Shell-exposed" (``cve:CVE-2021-44228 OR vuln:\\"log4j\\"``) —
      that the discrete params can't.  It runs the identical engine the
      Hosts page uses; ``follow:``/``assigned:`` resolve against the
      session's operator (``started_by``).  This stays read-only: the DSL
      only *filters*, it never mutates follow/assignment state.

    No scope sub-filtering (assist sessions are project-wide), so the
    recon-only ``scoped_host_ids_subq`` path is skipped.

    v2.440.0 (diag 4) — returns ``{items, total, has_more, limit, offset}``:
    the bare list's length was read as "how many hosts", and a 500-row
    default page answered a question about ~900 hosts with 500.  ``total`` is
    the same COUNT as ``/assist/hosts/count``.
    """
    session = _load_assist_session(db, request)
    query = _build_assist_host_query(
        db, session,
        state=state, ports=ports, services=services, subnets=subnets,
        has_critical_vulns=has_critical_vulns, has_high_vulns=has_high_vulns,
        search=search, q=q,
    )
    # Counted before sorting: the sort may join the vulnerability rollup.
    total = query.with_entities(func.count(models.Host.id.distinct())).scalar() or 0
    # v2.429.1 (MCP acceptance run 2) — the Hosts page's own sort, so "worst
    # first" is a parameter rather than a local re-sort of every page.
    hosts = apply_host_sorting(query, sort_by, sort_order).offset(offset).limit(limit).all()

    def page(items):
        return HostBriefPage(
            items=items, total=total, has_more=offset + len(items) < total,
            limit=limit, offset=offset,
        )

    if not hosts:
        return page([])
    host_ids = [h.id for h in hosts]
    port_counts, vuln_map = batch_host_enrichment(db, host_ids)
    follow_map = _operator_follow_map(db, host_ids, session.started_by_id)
    exploits, critical_exploits = exploit_count_maps(db, host_ids)
    result = []
    for h in hosts:
        vc = vuln_map.get(h.id, {})
        result.append(
            HostBrief(
                exploitable_count=exploits.get(h.id, 0),
                critical_exploitable_count=critical_exploits.get(h.id, 0),
                id=h.id,
                ip_address=h.ip_address,
                hostname=h.hostname,
                state=h.state,
                os_name=h.os_name,
                os_family=h.os_family,
                first_seen=h.first_seen,
                last_seen=h.last_seen,
                open_port_count=port_counts.get(h.id, 0),
                vuln_summary=VulnCounts(
                    critical=vc.get("critical", 0),
                    high=vc.get("high", 0),
                    medium=vc.get("medium", 0),
                    low=vc.get("low", 0),
                )
                if vc
                else None,
                follow=follow_map.get(h.id),
            )
        )
    return page(result)


@router.get(
    "/assist/hosts.ndjson",
    summary="Stream ALL matching hosts as newline-delimited JSON (download to disk)",
    response_class=StreamingResponse,
)
def download_assist_hosts_ndjson(
    request: Request,
    state: Optional[str] = Query(None),
    ports: Optional[str] = Query(None, description=PORTS_PARAM_HELP),
    services: Optional[str] = Query(None, description=SERVICES_PARAM_HELP),
    subnets: Optional[str] = Query(None, description="Comma-separated CIDR blocks"),
    has_critical_vulns: Optional[bool] = Query(None),
    has_high_vulns: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Search IP, hostname, or OS"),
    q: Optional[str] = Query(None, description="Boolean query DSL — same vocabulary as /assist/hosts."),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The complete host set — uncapped, one JSON object per line — for when the
    answer doesn't fit a context window.

    Use this instead of paging ``/assist/hosts`` when the project has thousands
    of hosts: redirect it to a file and process it locally so coverage stays
    complete without the payload ever being read into the model:

        curl -s -H "X-API-Key: $KEY" .../assist/hosts.ndjson -o hosts.jsonl
        jq -c 'select(.open_port_count > 0 and .os_family == "Windows")' hosts.jsonl

    Same fields, same IP ordering, and same filter vocabulary as
    ``GET /assist/hosts`` — the identical dataset, delivered so it can be
    processed without being read whole. Server memory is bounded (rows are
    paged as they stream).
    """
    # A streamed export of the whole project: exempt from the API statement
    # timeout (review 2026-10-01 R23), which is for interactive requests.
    disable_statement_timeout(db)
    session = _load_assist_session(db, request)
    query = _build_assist_host_query(
        db, session,
        state=state, ports=ports, services=services, subnets=subnets,
        has_critical_vulns=has_critical_vulns, has_high_vulns=has_high_vulns,
        search=search, q=q,
    )
    return StreamingResponse(
        _iter_assist_hosts_ndjson(db, query, operator_id=session.started_by_id),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f"attachment; filename=assist-project-{session.project_id}-hosts.jsonl"
        },
    )


class AssistWebInterface(BaseModel):
    """A web service observed on this host, and the screenshot of it if one
    was captured.

    v2.297.0.  Web interfaces were reachable through the `has:web` DSL filter
    but their content — the page title, the server banner, the EyeWitness
    screenshot — was not exposed to assist at all, so a write-up could say a
    host serves HTTP and nothing about what it serves.
    """
    id: int
    url: str
    # v2.323.0 — the named endpoint (vhost) this interface answers as; null
    # when the URL targeted the bare address.
    fqdn: Optional[str] = None
    port: Optional[int] = None
    status_code: Optional[int] = None
    title: Optional[str] = None
    server_header: Optional[str] = None
    technologies: List[str] = []
    # v2.343.2 — the list above is capped at _TECH_CAP; say so when it is.
    technologies_truncated: bool = False
    #: Present only when EyeWitness captured a PNG.  Same contract as note
    #: attachments: a path to save to disk; ``assist_get_image`` shows it inline.
    screenshot_download_path: Optional[str] = None
    # v2.428.5 — the certificate and TLS facts BlueStick stores typed on the
    # interface (MCP acceptance feedback #12: "cannot answer TLS state from
    # HTTPS URLs").  Null = the tool that saw it did not report it — not "fine".
    # An expired certificate is the catalog check tls_cert_expired (recorded at
    # import), not a flag computed against today.
    cert_not_after: Optional[datetime] = None
    cert_self_signed: Optional[bool] = None
    cert_subject_org: Optional[str] = None
    cert_issuer_org: Optional[str] = None
    tls_weak_protocol: Optional[bool] = Field(None, description=(
        "True when SSLv2/SSLv3/TLS 1.0/1.1 was offered; False when only strong "
        "protocols were seen; null when the tool did not enumerate protocols."
    ))
    # v2.429.1 (MCP acceptance run 2) — what the host inspector's web panel
    # reads straight from the tool's TLS record (``WebInterfacesCard``'s
    # summarizeTls: the same keys, in the same order).  The typed columns
    # above can be empty where the record is not, and the page showed an
    # issuer and SANs the agent could not see.
    tls_version: Optional[str] = None
    cert_issuer: Optional[str] = Field(None, description="issuer CN, else organisation, else DN, as the tool reported it")
    cert_subject_cn: Optional[str] = None
    cert_sans: List[str] = Field(default_factory=list, description="Subject alternative names (first 20)")
    cert_san_total: int = 0
    # v2.433.0 (agent feedback #24) — which observation this is.  The table
    # keeps one row per scan, so a re-scanned URL appears once per scan; the
    # inspector shows the latest per (tool, URL) and so must a write-up.
    source: Optional[str] = Field(None, description="The tool that observed it (eyewitness, httpx, nikto…)")
    scan_id: Optional[int] = None
    observed_at: Optional[datetime] = Field(None, description=(
        "When it was observed: the scan's own time when the tool recorded one "
        "(observed_at_basis 'scan'), else the import time ('import')."
    ))
    observed_at_basis: str = "import"
    imported_at: Optional[datetime] = None
    is_latest: bool = Field(True, description=(
        "True for the newest observation of this URL by this tool; false for an "
        "earlier scan's row — history, not current state."
    ))


class AssistScript(BaseModel):
    """One NSE script result (port- or host-level), output bounded."""
    script_id: str
    output: Optional[str] = None
    output_truncated: bool = False


class AssistPortDetail(PortBrief):
    """A port on assist host detail — ``PortBrief`` plus the NSE script output
    the inspector shows under it (v2.428.0)."""
    service_extrainfo: Optional[str] = None
    scripts: List[AssistScript] = []
    scripts_truncated: bool = False


class AssistHostDetail(HostDetail):
    """Assist's host detail — what the host inspector shows (v2.428.0).

    A subclass rather than fields on the shared schema: ``HostDetail`` is also
    the recon/plan browse payload, and those workflows have no use for
    screenshot download paths or review state.  Every field below comes from
    the code the inspector's ``GET /hosts/{id}`` uses (``host_detail_service``,
    ``host_serialization``, ``scope_coverage``, ``host_assessment_service``,
    ``host_query.host_weakness_flags``), so the two cannot state different facts.
    """
    ports: List[AssistPortDetail] = []
    web_interfaces: List[AssistWebInterface] = []
    # v2.343.2 (review) — ``web_interfaces`` is capped at _WEB_INTERFACE_CAP;
    # without these an analyst read the sample as the complete record.
    web_interfaces_total: int = 0
    web_interfaces_truncated: bool = False
    # --- v2.428.0: the inspector's facts ------------------------------------
    state_reason: Optional[str] = None
    os_generation: Optional[str] = None
    os_type: Optional[str] = None
    os_vendor: Optional[str] = None
    os_accuracy: Optional[int] = None
    mac_address: Optional[str] = None
    mac_vendor: Optional[str] = None
    netbios_name: Optional[str] = None
    smb_signing: Optional[str] = Field(None, description="SMB signing: disabled / enabled / required, or null when no scan said.")
    tags: List[Dict[str, Any]] = Field(default_factory=list, description="Tags on the host: {id, name, color}.")
    assignees: List[Dict[str, Any]] = Field(default_factory=list, description="Who the host is assigned to: {user_id, name, assigned_at}.")
    scope_membership: Dict[str, Any] = Field(default_factory=dict, description=(
        "Which scope entries cover the host: coverage subnet/name/none, project_has_scope, "
        "the covering subnets (site, labels) and in-scope names that resolve to it."
    ))
    assessment: Dict[str, Any] = Field(default_factory=dict, description=(
        "Per assessment domain (observed, vulnerabilities, web/TLS, SMB/AD, tested): "
        "when it was assessed, or not assessed / not applicable. vuln_scan_credentialed says "
        "whether a vulnerability scan authenticated to the host: yes / no / not_stated "
        "(null when not assessed) — a clean result from a scan that did not log in is "
        "weaker evidence; it does not change vuln_assessed."
    ))
    weakness_flags: List[str] = Field(default_factory=list, description="The has: weakness flags this host matches (smb_unsigned, weak_tls, eol_os…).")
    weakness_labels: Dict[str, str] = Field(default_factory=dict, description="Human label per weakness flag.")
    cert_orgs: List[Dict[str, Any]] = Field(default_factory=list, description="Certificate subject organisations seen on its web services: {org, issuer, url}.")
    cert_status: List[Dict[str, Any]] = Field(default_factory=list, description="Certificate expiry / self-signed per URL: {url, not_after, self_signed, subject_org}.")
    attributions: List[Dict[str, Any]] = Field(default_factory=list, description="Network provenance (RDAP/prefix lists): cidr, org, ASN, country, cloud provider.")
    host_scripts: List[AssistScript] = []
    host_scripts_truncated: bool = False
    conflict_count: int = Field(0, description="Recorded disagreements between scans on this host's fields.")
    conflicts: List[Dict[str, Any]] = Field(default_factory=list, description=(
        "Newest disagreements first: field, previous/new value and scan filename, current_value."
    ))
    conflicts_truncated: bool = False
    note_count: int = Field(0, description="Notes on the host (read them with the host notes tool).")
    finding_count: int = Field(0, description="Active findings with this host as a live endpoint.")


#: Per host. Enough to characterise what a host serves without turning a host
#: lookup into a page dump; `assist_list_hosts` with `has:web` finds the rest.
_WEB_INTERFACE_CAP = 10
#: NSE script output on host detail (v2.428.0).  Verbose scripts (ssl-enum-
#: ciphers, http-headers) run to kilobytes each; a host with dozens of ports
#: would otherwise turn one lookup into a page dump.  Per-script, per-owner
#: and whole-response bounds, each stated by a ``*_truncated`` flag.
_SCRIPT_OUTPUT_CAP = 1500
_SCRIPTS_PER_OWNER = 10
_SCRIPT_OUTPUT_BUDGET = 40_000
#: Conflicts listed on host detail (host-level and port-level each);
#: ``conflict_count`` is the whole number.
_CONFLICT_CAP = 20


class _ScriptBudget:
    """Characters of script output left for this response."""
    def __init__(self) -> None:
        self.left = _SCRIPT_OUTPUT_BUDGET
        self.exhausted = False


def _assist_script(s, budget: _ScriptBudget) -> AssistScript:
    out = s.output or ""
    cap = min(_SCRIPT_OUTPUT_CAP, max(budget.left, 0))
    if len(out) > cap and cap < _SCRIPT_OUTPUT_CAP:
        budget.exhausted = True
    text = out[:cap]
    budget.left -= len(text)
    return AssistScript(
        script_id=s.script_id,
        output=text or None,
        output_truncated=len(out) > len(text),
    )


def _assist_port_detail(p, budget: _ScriptBudget) -> AssistPortDetail:
    scripts = sorted(p.scripts or [], key=lambda s: s.script_id or "")
    shown = [_assist_script(s, budget) for s in scripts[:_SCRIPTS_PER_OWNER]]
    return AssistPortDetail(
        id=p.id,
        port_number=p.port_number,
        protocol=p.protocol,
        state=p.state,
        service_name=p.service_name,
        service_product=p.service_product,
        service_version=p.service_version,
        service_extrainfo=p.service_extrainfo,
        scripts=shown,
        scripts_truncated=len(scripts) > len(shown) or any(s.output_truncated for s in shown),
    )
#: Technology lists come from Wappalyzer and can run long on a CMS.
_TECH_CAP = 12
_SAN_CAP = 20


def _tls_str(tls: dict, *keys: str) -> Optional[str]:
    """The first non-empty string among ``keys`` — the web panel's tlsStr."""
    for k in keys:
        v = tls.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _serialize_web_interface(w, observed=None, latest: Optional[set] = None) -> AssistWebInterface:
    """One web interface as assist reports it — shared by the capped list on
    host detail and the paged list below, so the two never disagree.
    ``observed`` / ``latest`` come from services/web_interface_observation."""
    obs = observed.get(w.id) if observed else None
    tls = w.tls_info if isinstance(w.tls_info, dict) else {}
    sans = tls.get("subject_an") or tls.get("subject_alt_names")
    sans = [str(s) for s in sans] if isinstance(sans, list) else []
    return AssistWebInterface(
        tls_version=_tls_str(tls, "tls_version", "version"),
        cert_issuer=_tls_str(tls, "issuer_cn", "issuer_org", "issuer_dn"),
        cert_subject_cn=_tls_str(tls, "subject_cn"),
        cert_sans=sans[:_SAN_CAP],
        cert_san_total=len(sans),
        id=w.id,
        url=w.url,
        fqdn=w.name.fqdn if w.name else None,
        port=w.port,
        status_code=w.status_code,
        title=w.title,
        server_header=w.server_header,
        technologies=[str(t) for t in (w.technologies or [])][:_TECH_CAP],
        technologies_truncated=len(w.technologies or []) > _TECH_CAP,
        screenshot_download_path=(
            f"/api/v1/agent/assist/web-interfaces/{w.id}/screenshot"
            if w.screenshot_path else None
        ),
        cert_not_after=w.cert_not_after,
        cert_self_signed=w.cert_self_signed,
        cert_subject_org=w.cert_subject_org,
        cert_issuer_org=w.cert_issuer_org,
        tls_weak_protocol=w.tls_weak_protocol,
        source=w.source,
        scan_id=w.scan_id,
        observed_at=obs.observed_at if obs else w.first_seen,
        observed_at_basis=obs.basis if obs else "import",
        imported_at=w.first_seen,
        is_latest=(w.id in latest) if latest is not None else True,
    )


class AssistWebInterfacesPage(BaseModel):
    """v2.343.3 (review) — the continuation for host detail's capped
    ``web_interfaces``.  Disclosing the cap (``web_interfaces_truncated``) was
    half the fix; without a way to page past it, the omitted interfaces and
    their screenshot references were still unreachable over MCP."""
    items: List[AssistWebInterface] = []
    total: int = 0
    has_more: bool = False
    limit: int = 50
    offset: int = 0


@router.get(
    "/assist/hosts/{host_id}/web-interfaces",
    response_model=AssistWebInterfacesPage,
    summary="Every web interface observed on one host, as a page",
)
def list_assist_host_web_interfaces(
    request: Request,
    host_id: int = Path(..., gt=0),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The complete web-interface record for a host, paged.

    Same ordering as the capped list on host detail (screenshotted first, then
    by port), with ``id`` as the tiebreaker so pages are stable.  ``total`` and
    ``has_more`` say whether this page is the whole record.
    """
    session = _load_assist_session(db, request)
    host = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == session.project_id)
        .first()
    )
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found in this project")
    scoped = db.query(models.WebInterface).filter(
        models.WebInterface.host_id == host.id,
        models.WebInterface.project_id == session.project_id,
    )
    total = scoped.with_entities(func.count(models.WebInterface.id)).scalar() or 0
    rows = (
        scoped.order_by(
            models.WebInterface.screenshot_path.is_(None),
            models.WebInterface.port.asc(),
            models.WebInterface.id.asc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    from app.services.web_interface_observation import latest_ids_for, observations
    observed, latest = observations(db, rows), latest_ids_for(db, host.id, rows)
    return AssistWebInterfacesPage(
        items=[_serialize_web_interface(w, observed, latest) for w in rows],
        total=int(total),
        has_more=offset + len(rows) < total,
        limit=limit,
        offset=offset,
    )


class AssistAccessResult(BaseModel):
    """One NetExec / SMBMap result on a host, as stored (v2.418.0): what the
    parser read from the tool's line (login outcome, local admin, SMBv1,
    shares) beside the line itself, so a reader can check one against the
    other."""
    id: int
    scan_id: int
    tool: str
    protocol: str
    port: Optional[int] = None
    auth_success: Optional[bool] = None
    username: Optional[str] = None
    local_admin: Optional[bool] = None
    smbv1: Optional[bool] = None
    writable_share: Optional[bool] = None
    shares: Optional[Any] = None
    raw_output: Optional[str] = None
    raw_output_truncated: bool = False


class AssistAccessPage(BaseModel):
    items: List[AssistAccessResult] = []
    total: int = 0
    has_more: bool = False
    limit: int = 50
    offset: int = 0


@router.get(
    "/assist/hosts/{host_id}/access",
    response_model=AssistAccessPage,
    summary="NetExec / SMBMap results on one host, as a page",
)
def list_assist_host_access(
    request: Request,
    host_id: int = Path(..., gt=0),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Every NetExec / SMBMap result recorded on the host — one row per
    (scan, protocol, port, account) — with the tool's own line.  The
    interpreted fields and the line side by side are what a parse audit
    compares (v2.418.0)."""
    from app.db.models_confidence import NETEXEC_RAW_OUTPUT_LIMIT, NetexecResult

    session = _load_assist_session(db, request)
    host = (
        db.query(models.Host.id)
        .filter(models.Host.id == host_id, models.Host.project_id == session.project_id)
        .first()
    )
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found in this project")
    scoped = db.query(NetexecResult).filter(NetexecResult.host_id == host_id)
    total = scoped.with_entities(func.count(NetexecResult.id)).scalar() or 0
    rows = scoped.order_by(NetexecResult.protocol, NetexecResult.port, NetexecResult.id).offset(offset).limit(limit).all()
    return AssistAccessPage(
        items=[
            AssistAccessResult(
                id=r.id, scan_id=r.scan_id, tool=r.tool or "netexec", protocol=r.protocol, port=r.port,
                auth_success=r.auth_success, username=r.username, local_admin=r.local_admin,
                smbv1=r.smbv1, writable_share=r.writable_share, shares=r.shares,
                raw_output=r.raw_output,
                raw_output_truncated=len(r.raw_output or "") >= NETEXEC_RAW_OUTPUT_LIMIT,
            )
            for r in rows
        ],
        total=int(total), has_more=offset + len(rows) < total, limit=limit, offset=offset,
    )


class AssistUninterpretedImport(BaseModel):
    job_id: int
    filename: str
    tool_name: Optional[str] = None
    format: Optional[str] = None
    created_at: Optional[datetime] = None
    total: int = 0
    distinct: int = 0
    shapes: List[Dict[str, Any]] = []


class AssistUninterpretedPage(BaseModel):
    """v2.418.0 — the imports whose parser did not interpret every line, with
    those lines as REDACTED shapes (values replaced by <IP>, <HOST>, <VALUE>…).
    Not an ingestion issue: the data that was read is in the project.  It is
    what a parse audit starts from."""
    items: List[AssistUninterpretedImport] = []
    total: int = 0
    has_more: bool = False


@router.get(
    "/assist/uninterpreted-lines",
    response_model=AssistUninterpretedPage,
    summary="Lines imports did not interpret, as redacted shapes",
)
def list_assist_uninterpreted_lines(
    request: Request,
    job_id: Optional[int] = Query(None, gt=0),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    session = _load_assist_session(db, request)
    scoped = db.query(models.IngestionJob).filter(
        models.IngestionJob.project_id == session.project_id,
        models.IngestionJob.uninterpreted_lines.isnot(None),
    )
    if job_id is not None:
        # v2.429.1 (MCP acceptance run 2) — an unknown job, or another
        # project's, answered the same empty page as "every line was read".
        exists = db.query(models.IngestionJob.id).filter(
            models.IngestionJob.id == job_id,
            models.IngestionJob.project_id == session.project_id,
        ).first()
        if exists is None:
            raise HTTPException(
                status_code=404,
                detail=f"No import job {job_id} in this project (a scan id is not a job id — "
                       "assist_list_scans gives each scan's ingestion_job_id).",
            )
        scoped = scoped.filter(models.IngestionJob.id == job_id)
    total = scoped.with_entities(func.count(models.IngestionJob.id)).scalar() or 0
    rows = scoped.order_by(models.IngestionJob.created_at.desc(), models.IngestionJob.id.desc()) \
        .offset(offset).limit(limit).all()
    return AssistUninterpretedPage(
        items=[
            AssistUninterpretedImport(
                job_id=j.id, filename=j.original_filename or j.filename, tool_name=j.tool_name,
                format=j.final_file_type, created_at=j.created_at,
                total=int((j.uninterpreted_lines or {}).get("total") or 0),
                distinct=int((j.uninterpreted_lines or {}).get("distinct") or 0),
                shapes=list((j.uninterpreted_lines or {}).get("shapes") or []),
            )
            for j in rows
        ],
        total=int(total), has_more=offset + len(rows) < total,
    )


@router.get(
    "/assist/hosts/by-ip/{ip}",
    response_model=AssistHostDetail,
    summary="Host detail by address — the same answer as /assist/hosts/{host_id}",
)
def get_assist_host_by_ip(
    request: Request,
    ip: str = Path(..., min_length=2, max_length=64),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """v2.429.1 (MCP acceptance run 2) — "what's on 10.0.0.5?" took a list
    call to find the id first.  One host per address per project
    (``uq_project_ip``), so the address names exactly one row."""
    session = _load_assist_session(db, request)
    host_id = db.query(models.Host.id).filter(
        models.Host.project_id == session.project_id,
        models.Host.ip_address == ip.strip(),
    ).scalar()
    if host_id is None:
        raise HTTPException(status_code=404, detail=f"No host with address {ip} in this project")
    return get_assist_host(request, host_id=host_id, agent=agent, db=db)


@router.get(
    "/assist/hosts/{host_id}",
    response_model=AssistHostDetail,
    summary="Host detail with open ports and the web services it exposes",
)
def get_assist_host(
    request: Request,
    host_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    session = _load_assist_session(db, request)
    host = (
        db.query(models.Host)
        .options(
            selectinload(models.Host.ports).selectinload(models.Port.scripts),
            selectinload(models.Host.host_scripts),
            selectinload(models.Host.tag_assignments).selectinload(models.HostTagAssignment.tag),
        )
        .filter(
            models.Host.id == host_id,
            models.Host.project_id == session.project_id,
        )
        .first()
    )
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    budget = _ScriptBudget()
    port_details = [
        _assist_port_detail(p, budget)
        for p in sorted(host.ports, key=lambda p: (p.port_number, p.protocol or ""))
    ]
    open_count = sum(1 for p in host.ports if p.state == "open")
    _, vuln_map = batch_host_enrichment(db, [host.id])
    vc = vuln_map.get(host.id, {})
    follow_map = _operator_follow_map(db, [host.id], session.started_by_id)

    web_rows = (
        db.query(models.WebInterface)
        .filter(
            models.WebInterface.host_id == host.id,
            models.WebInterface.project_id == session.project_id,
        )
        # Screenshotted interfaces first — those are the ones a report can
        # show — then by port for a stable order.
        .order_by(
            models.WebInterface.screenshot_path.is_(None),
            models.WebInterface.port.asc(),
        )
        .limit(_WEB_INTERFACE_CAP)
        .all()
    )
    web_total = (
        db.query(func.count(models.WebInterface.id))
        .filter(
            models.WebInterface.host_id == host.id,
            models.WebInterface.project_id == session.project_id,
        )
        .scalar()
    ) or 0
    from app.services.web_interface_observation import latest_ids_for, observations
    web_observed, web_latest = observations(db, web_rows), latest_ids_for(db, host.id, web_rows)
    web_interfaces = [_serialize_web_interface(w, web_observed, web_latest) for w in web_rows]

    # v2.428.0 — the inspector's facts, from the code GET /hosts/{id} uses.
    base = serialize_host_base(host, None, note_count=0)
    operator = db.query(User).filter(User.id == session.started_by_id).first()
    weakness_flags = (
        host_weakness_flags(db, operator, session.project_id, [host.id]).get(host.id, [])
        if operator is not None else []
    )
    conflicts = host_detail_service.host_conflict_history(
        db, host, host_limit=_CONFLICT_CAP, port_limit=_CONFLICT_CAP,
    )
    conflict_count = host_detail_service.host_conflict_counts(db, [host.id]).get(host.id, 0)
    note_count = (
        db.query(func.count(models.Annotation.id))
        .filter(models.Annotation.host_id == host.id)
        .scalar()
    ) or 0
    host_scripts = [
        _assist_script(s, budget)
        for s in sorted(host.host_scripts, key=lambda s: s.script_id or "")[:_SCRIPTS_PER_OWNER]
    ]
    exploit_counts = exploit_count_maps(db, [host.id])

    return AssistHostDetail(
        names=dns_name_service.observed_names_at_address(db, host.project_id, host.ip_address),
        state_reason=base["state_reason"],
        os_generation=base["os_generation"],
        os_type=base["os_type"],
        os_vendor=base["os_vendor"],
        os_accuracy=base["os_accuracy"],
        mac_address=base["mac_address"],
        mac_vendor=base["mac_vendor"],
        netbios_name=base["netbios_name"],
        smb_signing=base["smb_signing"],
        tags=base["tags"],
        assignees=host_detail_service.host_assignees(db, host.id),
        scope_membership=host_scope_membership(db, host),
        assessment=host_assessment(db, host),
        weakness_flags=weakness_flags,
        weakness_labels={f: WEAKNESS_LABELS[f] for f in weakness_flags},
        **serialize_cert_facts(host_detail_service.cert_web_interfaces(db, host.id)),
        attributions=[serialize_attribution(a) for a in attributions_for_host(db, host.id)],
        host_scripts=host_scripts,
        host_scripts_truncated=len(host.host_scripts) > len(host_scripts) or budget.exhausted,
        conflict_count=int(conflict_count),
        conflicts=conflicts,
        conflicts_truncated=int(conflict_count) > sum(1 for c in conflicts if c["object_type"] == "host"),
        note_count=int(note_count),
        finding_count=int(host_detail_service.active_finding_counts(db, [host.id]).get(host.id, 0)),
        # v2.434.1 (acceptance run H1) — the detail left these at their
        # default 0 while the list filled them, so a drill-down contradicted
        # the row that led to it.  One helper for both.
        exploitable_count=exploit_counts[0].get(host.id, 0),
        critical_exploitable_count=exploit_counts[1].get(host.id, 0),
        id=host.id,
        ip_address=host.ip_address,
        hostname=host.hostname,
        state=host.state,
        os_name=host.os_name,
        os_family=host.os_family,
        first_seen=host.first_seen,
        last_seen=host.last_seen,
        open_port_count=open_count,
        vuln_summary=VulnCounts(
            critical=vc.get("critical", 0),
            high=vc.get("high", 0),
            medium=vc.get("medium", 0),
            low=vc.get("low", 0),
        )
        if vc
        else None,
        follow=follow_map.get(host.id),
        ports=port_details,
        web_interfaces=web_interfaces,
        web_interfaces_total=int(web_total),
        web_interfaces_truncated=int(web_total) > len(web_interfaces),
    )


_SEVERITY_RANK = severity_rank(Vulnerability.severity)
# Keep evidence/description bounded so a single finding can't blow the response.
_EVIDENCE_CAP = 2000
_DESC_CAP = 2000


@router.get(
    "/assist/hosts/{host_id}/findings",
    response_model=AssistFindingsResponse,
    summary="Read a host's individual findings (CVE/plugin, port, evidence, remediation)",
)
def get_assist_host_findings(
    request: Request,
    host_id: int = Path(..., gt=0),
    severity: Optional[str] = Query(
        None,
        description="Comma-separated severities to include (critical/high/medium/low/info). Default: all.",
    ),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The finding-level read the host DTO's ``vuln_summary`` only counts —
    added on agent feedback (v1.45.0) so an assist agent can produce an
    evidence-rich report instead of citing bare counts and deferring to the UI.
    Read-only; ordered worst-severity first, then CVSS; paginated with
    ``total``/``has_more`` so coverage can be reported without guessing."""
    session = _load_assist_session(db, request)
    # Project scope: the host must belong to this session's project.
    host_ok = (
        db.query(models.Host.id)
        .filter(models.Host.id == host_id, models.Host.project_id == session.project_id)
        .first()
    )
    if not host_ok:
        raise HTTPException(status_code=404, detail="Host not found")

    q = db.query(Vulnerability).filter(Vulnerability.host_id == host_id)
    if severity:
        wanted_values = {s.strip().lower() for s in severity.split(",") if s.strip()}
        # Compare against enum members (the column is a PG enum; lower() on it
        # errors). Unknown severity strings simply match nothing.
        wanted = [m for m in VulnerabilitySeverity if m.value in wanted_values]
        q = q.filter(Vulnerability.severity.in_(wanted)) if wanted else q.filter(False)
    total = q.count()
    rows = (
        q.options(selectinload(Vulnerability.promoted_findings))
        .order_by(_SEVERITY_RANK, func.coalesce(Vulnerability.cvss_score, 0).desc(), Vulnerability.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    # Which finding covers each row and how it stands ON THIS HOST — the host
    # inspector's own rule (``issue_coverage_map`` / ``_vuln_coverage``), so an
    # agent and the page cannot disagree (agent feedback #27, 2026-10-02: the
    # rows carried no finding state, so "is this judged here?" had no answer).
    coverage = issue_coverage_map(db, session.project_id, rows, host_id=host_id)

    # One join-free port lookup for the rows' port_ids → number/service.
    port_ids = {v.port_id for v in rows if v.port_id is not None}
    port_map = {}
    if port_ids:
        for pid, num, svc in (
            db.query(models.Port.id, models.Port.port_number, models.Port.service_name)
            .filter(models.Port.id.in_(port_ids))
            .all()
        ):
            port_map[pid] = (num, svc)

    def _sev(v) -> str:
        s = v.severity
        return s.value if hasattr(s, "value") else str(s)

    def _src(v) -> str:
        s = v.source
        return s.value if hasattr(s, "value") else str(s)

    findings = []
    for v in rows:
        num, svc = port_map.get(v.port_id, (None, None))
        findings.append(AssistFinding(
            id=v.id,
            severity=_sev(v),
            title=v.title,
            cve_id=v.cve_id,
            plugin_id=v.plugin_id,
            cvss_score=v.cvss_score,
            source=_src(v),
            exploitable=bool(v.exploitable),
            port_number=num,
            service_name=svc,
            description=(v.description or None) and v.description[:_DESC_CAP],
            solution=(v.solution or None) and v.solution[:_DESC_CAP],
            evidence=(v.plugin_output or None) and v.plugin_output[:_EVIDENCE_CAP],
            check_id=v.check_id,
            **{
                key: value for key, value in _vuln_coverage(v, coverage).items()
                if key != "finding_match"
            },
        ))
    return AssistFindingsResponse(
        host_id=host_id,
        total=total,
        has_more=offset + len(rows) < total,
        findings=findings,
    )


@router.get(
    "/assist/report-context.ndjson",
    summary="Stream the complete per-host report dossier (NDJSON, download to disk)",
    response_class=StreamingResponse,
)
def download_assist_report_context(
    request: Request,
    state: Optional[str] = Query(None),
    ports: Optional[str] = Query(None, description=PORTS_PARAM_HELP),
    services: Optional[str] = Query(None, description=SERVICES_PARAM_HELP),
    subnets: Optional[str] = Query(None, description="Comma-separated CIDR blocks"),
    has_critical_vulns: Optional[bool] = Query(None),
    has_high_vulns: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Search IP, hostname, or OS"),
    q: Optional[str] = Query(None, description="Boolean query DSL — same vocabulary as /assist/hosts."),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The data source for agent-driven report generation, at scale.

    Streams the COMPLETE per-host report dossier for every matching host, one
    JSON object per line, **uncapped** — the same correlated record the
    server-side report builds: identity, ports (transport + service), findings
    (severity / CVE / plugin / affected port / evidence / remediation), notes,
    scan discoveries, canonical + execution findings, provenance, tags, and the
    operator's review state. Same discrete filters + ``q`` DSL as
    ``/assist/hosts``.

    Safe on a tens-of-thousands-host project: the server hydrates only one chunk
    at a time (peak memory ~one chunk), so there is no host cap. Redirect it to
    a file and populate your report template from that file — do NOT read the
    stream whole into context: ``curl -s -H 'X-API-Key: <key>'
    '<base>/agent/assist/report-context.ndjson' -o report-context.jsonl``.
    """
    from app.services.report_generator import ReportGenerator  # heavy stack — lazy

    # Streamed, uncapped: exempt from the API statement timeout (R23).
    disable_statement_timeout(db)
    session = _load_assist_session(db, request)
    operator = (
        db.query(User).filter(User.id == session.started_by_id).first()
        if session.started_by_id else None
    )
    if operator is None:
        # The dossier's review state is operator-relative; without a bound
        # operator there's nobody to resolve it against (mirrors the follow:/
        # assigned: DSL guard).
        raise HTTPException(
            status_code=400,
            detail="Assist session has no bound operator; cannot build report context.",
        )

    query = _build_assist_host_query(
        db, session,
        state=state, ports=ports, services=services, subnets=subnets,
        has_critical_vulns=has_critical_vulns, has_high_vulns=has_high_vulns,
        search=search, q=q,
    )
    host_id_query = query.with_entities(models.Host.id)
    generator = ReportGenerator(db, current_user=operator, project_id=session.project_id)

    def _stream():
        for record in generator.iter_host_records(host_id_query):
            yield json.dumps(record, default=str) + "\n"

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": (
                f"attachment; filename=assist-project-{session.project_id}-report-context.jsonl"
            ),
        },
    )


# ---------------------------------------------------------------------------
# Scopes — list
# ---------------------------------------------------------------------------

class AssistProjectFinding(BaseModel):
    """One finding, as an analyst's question needs it — not the full UI row."""
    id: int
    title: str
    severity: str
    status: str
    source: str
    owner_username: Optional[str] = None
    # A finding can span hosts; the count is what "how big is this" turns on,
    # and the sample lets the agent name a host without a second call.
    # ``host_count`` is distinct addresses; ``endpoint_count`` is affected
    # rows, which can exceed it when named endpoints share an IP (v2.343.2).
    host_count: int = 0
    endpoint_count: int = 0
    hosts: List[str] = []
    hosts_truncated: bool = False


class AssistFindingsPage(BaseModel):
    total: int
    severity_counts: dict
    findings: List[AssistProjectFinding]


@router.get(
    "/assist/findings",
    response_model=AssistFindingsPage,
    summary="Findings across the project — the spine, not one host's slice",
)
def list_assist_findings(
    request: Request,
    status: Optional[str] = Query(None, description="open / confirmed / false_positive / accepted_risk / remediated / retest; 'all' (or omit) for every status."),
    severity: Optional[str] = Query(None, description="critical / high / medium / low / info; 'all' (or omit) for every severity."),
    source: Optional[str] = Query(None, description="note / scanner / execution / manual; 'all' (or omit) for every source."),
    host_id: Optional[int] = Query(None, description="Only findings affecting this host."),
    unowned: bool = Query(False, description="Only findings with no owner — the work-allocation question."),
    owner: Optional[str] = Query(None, description="Username of the owner (or 'me')."),
    search: Optional[str] = Query(None, max_length=200, description="Substring match on the title."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Project-wide findings, with the totals an analyst is actually asking for.

    v2.292.0.  Assist could only see findings one host at a time
    (``/assist/hosts/{id}/findings``), so "what are the critical findings on
    this engagement?" — the question the Findings page exists to answer — meant
    walking every host and reassembling the spine client-side.  Findings are
    deliberately host-spanning in this schema (one finding, many hosts), so that
    reassembly was also wrong: the same finding on twelve hosts read as twelve
    findings.

    ``severity_counts`` respects every filter except severity, so an agent can
    report the breakdown within the scope it asked about without a second call.
    """
    session = _load_assist_session(db, request)
    from app.services.finding_service import FindingService

    # v2.343.2 (review) — the parameter description advertised 'all', but the
    # value went to FindingService as a literal status, so "all findings"
    # returned none with empty severity counts: a confident wrong answer.
    status = _unfiltered(status)
    severity = _unfiltered(severity)
    source = _unfiltered(source)

    owner_id = None
    if owner:
        if owner.lower() == "me":
            owner_id = session.started_by_id
        else:
            row = db.query(User.id).filter(func.lower(User.username) == owner.lower()).first()
            if row is None:
                raise HTTPException(status_code=400, detail=f"No user named {owner!r}")
            owner_id = row[0]

    svc = FindingService(db)
    filters = dict(
        project_id=session.project_id, status=status, source=source,
        host_id=host_id, unowned=unowned, owner_id=owner_id, search=search,
    )
    # ``Finding.hosts`` is plain lazy (review 2026-10-01 C2); this route reads
    # every endpoint of every row for its distinct-address count, so it names
    # the load.
    rows, total = svc.list_findings(
        **filters, severity=severity, limit=limit, offset=offset, with_endpoints=True,
    )
    counts = svc.severity_counts(**filters)

    findings = []
    for f in rows:
        endpoint_rows = [fh for fh in (f.hosts or []) if fh.host]
        # v2.343.2 — a finding row per named endpoint (two vhosts on one IP)
        # is two endpoints on ONE host; count hosts as distinct addresses.
        host_ips = sorted({fh.host.ip_address for fh in endpoint_rows})
        findings.append(
            AssistProjectFinding(
                id=f.id,
                title=f.title,
                severity=f.severity,
                status=f.status,
                source=f.source,
                owner_username=f.owner.username if f.owner else None,
                host_count=len(host_ips),
                endpoint_count=len(endpoint_rows),
                # Capped: a finding on 400 hosts should not spend the agent's
                # context proving it. The count above is the answer; the sample
                # is for naming one.
                hosts=host_ips[:10],
                hosts_truncated=len(host_ips) > 10,
            )
        )
    return AssistFindingsPage(total=total, severity_counts=counts, findings=findings)


def _unfiltered(value: Optional[str]) -> Optional[str]:
    """``None`` for the spellings that mean "no filter" — omitted, empty,
    ``all``, ``any`` — so they never reach a query as a literal value."""
    if value is None:
        return None
    if value.strip().lower() in ("", "all", "any", "*"):
        return None
    return value


class AssistAttachment(BaseModel):
    """An image attached to a note — a reference, never the bytes.

    ``download_path`` is relative; join it to the BlueStick base URL the
    session already uses and fetch it with the same ``X-API-Key``.  The bytes
    stay out of the tool result deliberately: a base64 screenshot costs
    thousands of tokens, and a report needs the file on disk beside it anyway.
    """
    id: int
    filename: str
    content_type: str
    size_bytes: int
    download_path: str
    uploaded_by: Optional[str] = None
    created_at: Optional[datetime] = None
    # Whether the image is marked to appear in the client report (opt-in).
    include_in_report: bool = False
    # What the image shows, as its author wrote it — the figure caption in the
    # client report.  None: the report prints the file name.
    caption: Optional[str] = None


def _assist_attachment(a, uploader_names: Dict[int, str]) -> AssistAttachment:
    """One attachment reference — shared by notes and finding evidence."""
    return AssistAttachment(
        id=a.id,
        filename=a.filename,
        content_type=a.content_type,
        size_bytes=a.size_bytes,
        download_path=f"/api/v1/agent/assist/attachments/{a.id}",
        uploaded_by=uploader_names.get(a.uploaded_by_id),
        created_at=a.created_at,
        include_in_report=bool(getattr(a, "include_in_report", False)),
        caption=getattr(a, "caption", None),
    )


def _uploader_names(db: Session, attachments) -> Dict[int, str]:
    ids = {a.uploaded_by_id for a in attachments if a.uploaded_by_id}
    if not ids:
        return {}
    return dict(db.query(User.id, User.username).filter(User.id.in_(ids)).all())


class AssistNote(BaseModel):
    """A note as the host inspector and the Collaboration feed show it
    (v2.428.0: threads, labels, attachments and the promoted finding —
    built from ``host_serialization._serialize_note``, the UI's serializer)."""
    id: int
    body: str
    author: Optional[str] = None
    author_name: Optional[str] = None
    # Agent-authored notes are stamped as such; a reader deserves to know
    # whether a colleague wrote this or an earlier agent did.
    actor_type: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Threads: a reply names its parent and its thread root; a root has
    # parent_id null.  Read a thread by grouping on thread_root_id.
    parent_id: Optional[int] = None
    thread_root_id: Optional[int] = None
    note_type: Optional[str] = None
    pinned: bool = False
    # The finding this thread was promoted to, when it was.
    finding_id: Optional[int] = None
    attachments: List[AssistAttachment] = []


def _assist_notes(db: Session, notes) -> List[AssistNote]:
    """Serialize notes loaded with ``note_load_options()`` — one extra query
    for the attachment uploaders, whatever the page size."""
    from app.services.host_serialization import _serialize_note

    uploaders = _uploader_names(db, [a for n in notes for a in (n.attachments or [])])
    out = []
    for n in notes:
        ui = _serialize_note(n)
        out.append(AssistNote(
            id=n.id,
            body=n.body,
            author=n.author.username if n.author else None,
            author_name=ui.author_name,
            actor_type=n.actor_type,
            created_at=n.created_at,
            updated_at=n.updated_at,
            parent_id=n.parent_id,
            thread_root_id=n.thread_root_id,
            note_type=n.note_type,
            pinned=bool(n.pinned),
            finding_id=ui.finding_id,
            attachments=[_assist_attachment(a, uploaders) for a in (n.attachments or [])],
        ))
    return out


class AssistNotesPage(BaseModel):
    """v2.343.2 (review) — the notes on a host, as a page that says whether it
    is the whole record.  The bare list this replaced was capped at ``limit``
    with no total and no way to continue, so a host with 51 notes read as a
    host with 50 — and an analyst had no way to know."""
    items: List[AssistNote] = []
    total: int = 0
    has_more: bool = False
    limit: int = 50
    offset: int = 0


@router.get(
    "/assist/hosts/{host_id}/notes",
    response_model=AssistNotesPage,
    summary="The team's notes on one host (paged, newest first)",
)
def list_assist_host_notes(
    request: Request,
    host_id: int = Path(..., gt=0),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """What people have already said about this host.

    v2.292.0.  Assist could *write* notes and not read them — an asymmetry that
    made the obvious question ("what do we already know about 10.0.0.5?")
    unanswerable, and let an agent add a note duplicating one written an hour
    earlier by someone else.
    """
    session = _load_assist_session(db, request)
    host = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == session.project_id)
        .first()
    )
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found in this project")

    total = (
        db.query(func.count(models.Annotation.id))
        .filter(models.Annotation.host_id == host_id)
        .scalar()
    ) or 0
    rows = (
        db.query(models.Annotation)
        .options(*note_load_options())
        .filter(models.Annotation.host_id == host_id)
        .order_by(models.Annotation.created_at.desc(), models.Annotation.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return AssistNotesPage(
        items=_assist_notes(db, rows),
        total=int(total),
        has_more=offset + len(rows) < int(total),
        limit=limit,
        offset=offset,
    )


class AssistVocabulary(BaseModel):
    """The values this project's `q=` predicates actually accept."""
    tags: List[str] = []
    labels: List[str] = []
    sites: List[str] = []
    scopes: List[str] = []
    usernames: List[str] = []
    finding_statuses: List[str] = []
    severities: List[str] = []


@router.get(
    "/assist/vocabulary",
    response_model=AssistVocabulary,
    summary="The tag / label / site / user values this project uses",
)
def assist_vocabulary(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """What to put after `tag:`, `label:`, `site:`, `assigned:` in a query.

    v2.292.0.  The DSL accepts these predicates, and an agent had no way to
    learn the values — so it guessed. A guessed tag doesn't error, it returns
    zero hosts, and "no hosts are tagged production" is a confidently wrong
    answer to a question that was really "what are the tags called here?".
    """
    session = _load_assist_session(db, request)
    pid = session.project_id

    def _names(model, column, **filters):
        q = db.query(column).filter_by(**filters) if filters else db.query(column)
        return sorted({v for (v,) in q.all() if v})

    tags = _names(models.HostTag, models.HostTag.name, project_id=pid)
    sites = _names(models.Site, models.Site.name, project_id=pid)
    scopes = _names(models.Scope, models.Scope.name, project_id=pid)
    labels = sorted({
        v for (v,) in db.query(models.SubnetLabel.name)
        .filter(models.SubnetLabel.project_id == pid).all() if v
    })
    # Who an `assigned:<username>` query can actually name: project members,
    # PLUS anyone currently holding an assignment here. A global admin needs no
    # membership row to be assigned a host, so members-only would omit exactly
    # the person whose work the analyst is asking about.
    member_names = db.query(User.username).join(
        ProjectMembership, ProjectMembership.user_id == User.id
    ).filter(ProjectMembership.project_id == pid)
    assignee_names = (
        db.query(User.username)
        .join(models.HostFollow, models.HostFollow.user_id == User.id)
        .join(models.Host, models.Host.id == models.HostFollow.host_id)
        .filter(
            models.Host.project_id == pid,
            models.HostFollow.assigned_at.isnot(None),
        )
    )
    usernames = sorted({u for (u,) in member_names.all() if u}
                       | {u for (u,) in assignee_names.all() if u})
    # v2.343.2 (review) — derived from the canonical enums.  The hand-written
    # list advertised `triaged` / `closed`, which no finding has ever carried,
    # and omitted `accepted_risk` / `retest`, which do exist: a filter on the
    # former silently returned nothing, and the latter were undiscoverable.
    from app.db.models_findings import FindingSeverity, FindingStatus
    return AssistVocabulary(
        tags=tags, labels=labels, sites=sites, scopes=scopes, usernames=usernames,
        finding_statuses=[s.value for s in FindingStatus],
        severities=[s.value for s in FindingSeverity],
    )


@router.get(
    "/assist/coverage",
    summary="How much of this project has actually been assessed",
)
def assist_coverage(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Per-domain assessment coverage — the confidence half of any answer.

    v2.292.0.  Every other assist surface reports what WAS found; this one
    reports how much was looked at, which is what stops "no critical findings"
    being read as "no critical exposure". The report templates ask for it by
    name in their scope-and-confidence section.
    """
    session = _load_assist_session(db, request)
    from app.services.evidence_service import compute_evidence_coverage

    return compute_evidence_coverage(db, session.project_id)



class AssistSegment(BaseModel):
    """One subnet, with the numbers that decide where to look next.

    v2.297.0 — the exposure / neglect / hygiene blocks come straight from
    ``compute_subnet_insights``; see the endpoint for why this stopped being
    hand-rolled.
    """
    cidr: str
    description: Optional[str] = None
    scope_name: Optional[str] = None
    site: Optional[str] = None
    labels: List[str] = []
    criticality_tier: Optional[int] = None
    host_count: int = 0
    #: A scoped range where nothing was ever discovered — a coverage signal,
    #: not a clean subnet.  Distinct from "scanned and found nothing".
    no_coverage: bool = False
    exposure: Dict[str, Any] = {}
    neglect: Dict[str, Any] = {}
    hygiene: Dict[str, Any] = {}
    recommended_action: Dict[str, str] = {}


class AssistSegmentsResponse(BaseModel):
    """Worst-first subnets plus the project-wide totals they roll up into."""
    adopted: bool
    #: Subnets in the project.  Compare with ``len(subnets)`` — the page is
    #: capped, and a truncated list must not read as the whole estate.
    total: int = 0
    limit: int = 0
    offset: int = 0
    totals: Dict[str, Any] = {}
    subnets: List[AssistSegment] = []
    reason: Optional[str] = None


@router.get(
    "/assist/segments",
    response_model=AssistSegmentsResponse,
    summary="Per-subnet rollup — where the problems are concentrated",
)
def list_assist_segments(
    request: Request,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """"Which part of the network is worst?" — and what is wrong with it.

    v2.293.0 introduced this as a hand-rolled rollup.  v2.297.0 replaced the
    body with ``compute_subnet_insights`` — the service that already backs the
    Subnet Insights page — rather than adding a second per-subnet tool beside
    it.  Two tools answering "which segment is worst" with different numbers is
    worse than one, and the hand-rolled version was the one that was wrong:

    * It counted raw ``Vulnerability`` rows.  Every other surface — posture,
      the Subnet Insights page, the reports — counts **active Findings**, the
      triaged spine.  So the agent quoted a number no page would ever show.
    * It read ``HostSubnetMapping`` directly, and a host maps to *every*
      containing subnet.  Scope a /16 and a /24 inside it — an ordinary way to
      scope an engagement — and every host in the /24 was counted twice.
      ``compute_subnet_insights`` resolves each host to its most-specific
      subnet, so the per-subnet counts sum to the project total.
    * It fired a query per subnet for hosts, another for severities and another
      for assignment, then sorted only the arbitrary first ``limit`` subnets it
      happened to load.  The sort is now over the whole estate, and the page is
      taken after it.

    What the payload carries per subnet:

    * ``exposure`` — active findings by severity, and a tier-weighted score
      (a finding in a tier-1 segment outranks the same finding in a lab).
    * ``neglect`` — unowned findings, unreviewed hosts, median host age,
      stale-host count.  Exposure says how bad it is; neglect says whether
      anyone is looking.
    * ``hygiene`` — EOL OS, certificate problems, weak/guest auth, risky
      services with the ports behind them.  This is the "this subnet is
      out of date" material, per subnet.
    * ``recommended_action`` — the loudest component turned into a next step.

    ``no_coverage=true`` marks a scoped range where nothing was ever
    discovered.  That is a **scanning gap**, not a clean subnet; reporting it
    as "no issues" inverts its meaning.

    ``adopted=false`` means the project has no scoped subnets, so there is
    nothing to segment by — "not assessable", not "no problems".  ``total`` is
    the subnet count; when it exceeds the page you are seeing the worst ones,
    not all of them.
    """
    session = _load_assist_session(db, request)
    pid = session.project_id

    insights = compute_subnet_insights(db, pid, limit=limit, offset=offset)
    if not insights.get("adopted"):
        return AssistSegmentsResponse(
            adopted=False, total=0, limit=limit, offset=offset,
            reason=(
                "No scoped subnets for this project, so there is nothing to "
                "segment by. Define a scope with subnets to enable it."
            ),
        )

    page = insights.get("subnets", [])
    # Description and labels aren't part of the insight record, and both are
    # how an operator refers to a segment ("the DMZ") — and `label:` is a DSL
    # predicate the agent can pivot on.  One batched query for the page rather
    # than widening the shared service for assist's benefit.
    subnet_ids = [row["subnet_id"] for row in page]
    descriptions: Dict[int, Optional[str]] = {}
    labels: Dict[int, List[str]] = {}
    if subnet_ids:
        for sid, desc in (
            db.query(models.Subnet.id, models.Subnet.description)
            .filter(models.Subnet.id.in_(subnet_ids)).all()
        ):
            descriptions[sid] = desc
        for sid, name in (
            db.query(models.SubnetLabelAssignment.subnet_id, models.SubnetLabel.name)
            .join(
                models.SubnetLabel,
                models.SubnetLabel.id == models.SubnetLabelAssignment.label_id,
            )
            .filter(models.SubnetLabelAssignment.subnet_id.in_(subnet_ids))
            .all()
        ):
            if name:
                labels.setdefault(sid, []).append(name)

    subnets = [
        AssistSegment(
            cidr=row["cidr"],
            description=descriptions.get(row["subnet_id"]),
            scope_name=row.get("scope_name"),
            site=row.get("site"),
            labels=sorted(labels.get(row["subnet_id"], [])),
            criticality_tier=row.get("criticality_tier"),
            host_count=row.get("host_count", 0),
            no_coverage=row.get("no_coverage", False),
            exposure=row.get("exposure", {}),
            neglect=row.get("neglect", {}),
            # eol_os_detail is the per-host list behind the EOL count. Dropped:
            # "which hosts are EOL in this subnet" is a `q=` filter the agent
            # already has, and carrying up to 10 host records per subnet per
            # page is context spent on data it can ask for when it needs it.
            hygiene={k: v for k, v in row.get("hygiene", {}).items()
                     if k != "eol_os_detail"},
            recommended_action=row.get("recommended_action", {}),
        )
        for row in page
    ]
    return AssistSegmentsResponse(
        adopted=True,
        total=insights.get("total", 0),
        limit=limit,
        offset=offset,
        totals=insights.get("totals", {}),
        subnets=subnets,
    )


class AssistRecentNote(AssistNote):
    host_id: Optional[int] = None
    host_ip: Optional[str] = None
    # v2.428.0 — what the note is ON.  A note has exactly one target (host,
    # port, finding, scan, scope, test plan or the project); before this only
    # host notes said where they were, and a finding comment read as a note
    # about nothing.  {kind, id, label}.
    target: Optional[Dict[str, Any]] = None


@router.get(
    "/assist/notes",
    response_model=List[AssistRecentNote],
    summary="Recent notes across the project — what the team has been doing",
)
def list_assist_recent_notes(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    author: Optional[str] = Query(None, description="Username, or 'me'."),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """"What has the team been working on?" — newest first.

    v2.293.0.  Per-host notes answered "what do we know about THIS host"; this
    answers the question an analyst asks when they pick the engagement back up,
    which is about the work rather than about one asset.
    """
    session = _load_assist_session(db, request)
    pid = session.project_id
    # v2.313.0 — this filtered `Annotation.project_id == pid` and returned an
    # empty list for every project since it shipped.
    #
    # `project_id` is not a scope column: it is one of seven MUTUALLY EXCLUSIVE
    # targets (`ck_annotations_exactly_one_target`), so a note with it set is a
    # note on the *project itself*, and a host note necessarily has it NULL.
    # Filtering on it selected the one target nothing writes — 33 of 33 notes in
    # the deployment were host-targeted. The endpoint answered "the team has
    # done nothing" while `assist_get_host_notes` returned the same notes fine,
    # which is how an agent found it.
    #
    # A note's project is whatever its target belongs to, so reach it through
    # each target. Port notes go one hop further, via their host. All outer
    # joins: a note matches on exactly one branch and is NULL on the rest.
    #
    # Every target is covered on purpose. Only host notes exist today, so
    # a narrower fix would pass its tests and quietly reintroduce the same class
    # of gap the first time someone annotates a scan.
    from app.db.models_findings import Finding

    port_host = aliased(models.Host)
    q = (
        db.query(
            models.Annotation, models.Host.ip_address,
            Finding.title, models.Scan.filename, models.Scope.name,
            models.Port.port_number, port_host.ip_address,
        )
        .options(*note_load_options())
        .outerjoin(models.Host, models.Annotation.host_id == models.Host.id)
        .outerjoin(Finding, models.Annotation.finding_id == Finding.id)
        .outerjoin(models.Scan, models.Annotation.scan_id == models.Scan.id)
        .outerjoin(models.Scope, models.Annotation.scope_id == models.Scope.id)
        .outerjoin(models.Port, models.Annotation.port_id == models.Port.id)
        .outerjoin(port_host, models.Port.host_id == port_host.id)
        .filter(or_(
            models.Annotation.project_id == pid,
            models.Host.project_id == pid,
            Finding.project_id == pid,
            models.Scan.project_id == pid,
            models.Scope.project_id == pid,
            port_host.project_id == pid,
        ))
    )
    if author:
        if author.lower() == "me":
            q = q.filter(models.Annotation.user_id == session.started_by_id)
        else:
            row = db.query(User.id).filter(func.lower(User.username) == author.lower()).first()
            if row is None:
                raise HTTPException(status_code=400, detail=f"No user named {author!r}")
            q = q.filter(models.Annotation.user_id == row[0])

    rows = q.order_by(models.Annotation.created_at.desc()).limit(limit).all()
    serialized = _assist_notes(db, [r[0] for r in rows])
    out = []
    for note, (a, ip, f_title, scan_name, scope_name, port_no, port_ip) in zip(serialized, rows):
        if a.host_id:
            target = {"kind": "host", "id": a.host_id, "label": ip}
        elif a.port_id:
            target = {"kind": "port", "id": a.port_id, "label": f"{port_ip}:{port_no}" if port_ip else None}
        elif a.finding_id:
            target = {"kind": "finding", "id": a.finding_id, "label": f_title}
        elif a.scan_id:
            target = {"kind": "scan", "id": a.scan_id, "label": scan_name}
        elif a.scope_id:
            target = {"kind": "scope", "id": a.scope_id, "label": scope_name}
        else:
            target = {"kind": "project", "id": a.project_id, "label": None}
        out.append(AssistRecentNote(
            **note.model_dump(), host_id=a.host_id, host_ip=ip, target=target,
        ))
    return out


@router.get(
    "/assist/scopes",
    response_model=List[ScopeBrief],
    summary="List project scopes with their subnet CIDRs and declared domains",
)
def list_assist_scopes(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """List scopes; per-scope subnet CIDRs included.  Capped at the
    first 100 subnets per scope so a very-large scope's CIDR list
    doesn't blow the agent context window.  The cap is now explicit:
    each ScopeBrief carries ``subnet_total`` (the true count) and
    ``subnets_truncated``, so an assist agent can tell a 100-CIDR scope
    from a 1000-CIDR one and surface "list truncated" to the operator.
    An assist key is rejected on every /agent/recon/* endpoint, so full
    CIDR enumeration is NOT reachable from this workflow — complete
    enumeration requires a recon session.

    v2.330.0 — each scope also carries its declared ``domains`` (same 100
    cap, ``domain_total`` / ``domains_truncated``) and
    ``names_in_scope_total`` (distinct inventory names any entry covers).
    Name scope is independent of subnet scope: an in-scope name does not
    make the address it resolves to subnet-in-scope, and an in-scope
    subnet does not make names in scope.
    """
    session = _load_assist_session(db, request)
    scopes = (
        db.query(models.Scope)
        .filter(models.Scope.project_id == session.project_id)
        .order_by(models.Scope.name)
        .all()
    )
    if not scopes:
        return []
    scope_ids = [s.id for s in scopes]
    # Per-scope subnet CIDR lists (cap each at 100 — see docstring).
    subnet_rows = (
        db.query(models.Subnet.scope_id, models.Subnet.cidr)
        .filter(models.Subnet.scope_id.in_(scope_ids))
        .order_by(models.Subnet.scope_id, models.Subnet.cidr)
        .all()
    )
    _SUBNET_CAP = 100
    cidrs_by_scope: dict[int, list[str]] = {}
    total_by_scope: dict[int, int] = {}
    for scope_id, cidr in subnet_rows:
        total_by_scope[scope_id] = total_by_scope.get(scope_id, 0) + 1
        bucket = cidrs_by_scope.setdefault(scope_id, [])
        if len(bucket) < _SUBNET_CAP:
            bucket.append(cidr)
    # Per-scope domain lists, capped the same way.
    domain_rows = (
        db.query(models.ScopeDomain.scope_id, models.ScopeDomain.domain, models.ScopeDomain.include_subdomains)
        .filter(models.ScopeDomain.scope_id.in_(scope_ids))
        .order_by(models.ScopeDomain.scope_id, models.ScopeDomain.domain)
        .all()
    )
    domains_by_scope: dict[int, list[ScopeDomainBrief]] = {}
    domain_total_by_scope: dict[int, int] = {}
    for scope_id, domain, include_sub in domain_rows:
        domain_total_by_scope[scope_id] = domain_total_by_scope.get(scope_id, 0) + 1
        bucket = domains_by_scope.setdefault(scope_id, [])
        if len(bucket) < _SUBNET_CAP:
            bucket.append(ScopeDomainBrief(domain=domain, include_subdomains=bool(include_sub)))
    names_in_scope_total = (
        dns_name_service.scope_domains_covered_names_total(db, session.project_id) if domain_rows else 0
    )
    return [
        ScopeBrief(
            id=s.id,
            name=s.name,
            description=s.description,
            subnets=cidrs_by_scope.get(s.id, []),
            subnet_total=total_by_scope.get(s.id, 0),
            subnets_truncated=total_by_scope.get(s.id, 0) > _SUBNET_CAP,
            domains=domains_by_scope.get(s.id, []),
            domain_total=domain_total_by_scope.get(s.id, 0),
            domains_truncated=domain_total_by_scope.get(s.id, 0) > _SUBNET_CAP,
            names_in_scope_total=names_in_scope_total,
        )
        for s in scopes
    ]


# ---------------------------------------------------------------------------
# Names — the named-asset inventory (read-only, v2.330.0)
# ---------------------------------------------------------------------------

_ASSIST_NAME_IP_CAP = 10


@router.get(
    "/assist/names",
    response_model=AssistNamesResponse,
    summary="List the project's named assets (FQDNs) with scope and current addresses",
)
def list_assist_names(
    request: Request,
    q: Optional[str] = Query(None, description="Case-insensitive substring on the FQDN"),
    in_scope: Optional[bool] = Query(None, description="Only names a declared domain covers (true) or does not (false)"),
    resolved: Optional[bool] = Query(None, description="Only names with (true) / without (false) a current A/AAAA answer"),
    host_id: Optional[int] = Query(None, description="Only names that currently resolve to this host's address"),
    kind: Optional[str] = Query(None, pattern="^(fqdn|wildcard)$", description="fqdn | wildcard"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The names inventory, the same predicates the Names page uses
    (``name_in_scope_condition``, ``resolving_exists_condition``,
    ``current_binding_condition``), so this view can never disagree with it.

    How to act on it: ``in_scope=true&resolved=false`` is the queue — names
    approved for testing that no upload has ever resolved (chase them with
    dnsx/amass output, or drop them from scope).  A name whose
    ``current_ips`` is shared with other names (a load balancer / vhost)
    must be tested BY NAME — the address alone reaches a different site.
    A name in scope does not put its address in subnet scope.
    """
    session = _load_assist_session(db, request)
    pid = session.project_id
    n = models.DNSName
    in_scope_col = dns_name_service.name_in_scope_condition(pid).label("in_scope")
    query = db.query(n, in_scope_col).filter(n.project_id == pid)
    if q and q.strip():
        query = query.filter(n.fqdn.ilike(f"%{escape_like(q.strip().lower())}%", escape="\\"))
    if in_scope is True:
        query = query.filter(dns_name_service.name_in_scope_condition(pid))
    elif in_scope is False:
        query = query.filter(~dns_name_service.name_in_scope_condition(pid))
    if resolved is True:
        query = query.filter(dns_name_service.resolving_exists_condition())
    elif resolved is False:
        query = query.filter(~dns_name_service.resolving_exists_condition())
    if kind:
        query = query.filter(n.kind == kind)
    if host_id is not None:
        host = (
            db.query(models.Host.ip_address)
            .filter(models.Host.id == host_id, models.Host.project_id == pid)
            .first()
        )
        if host is None:
            raise HTTPException(status_code=404, detail="Host not found in this project")
        r = models.DNSRecord
        query = query.filter(
            db.query(r.id)
            .filter(
                r.name_id == n.id,
                r.project_id == pid,
                r.value == host.ip_address,
                dns_name_service.current_binding_condition(r),
            )
            .exists()
        )
    total = query.with_entities(func.count(n.id)).order_by(None).scalar() or 0
    rows = query.order_by(n.fqdn.asc(), n.id.asc()).offset(offset).limit(limit).all()
    states = dns_name_service.address_state_for_names(db, pid, [name.id for name, _ in rows])
    items = []
    for name, is_in_scope in rows:
        st = states[name.id]
        ips = sorted(st.current)
        items.append(
            AssistNameRow(
                id=name.id,
                fqdn=name.fqdn,
                kind=name.kind,
                in_scope=bool(is_in_scope),
                current_ips=ips[:_ASSIST_NAME_IP_CAP],
                current_ip_total=len(ips),
                last_seen=name.last_seen,
                sources=sorted(st.evidence),
            )
        )
    return AssistNamesResponse(
        items=items, total=total, offset=offset, limit=limit,
        returned=len(items), has_more=offset + len(items) < total,
    )


# ---------------------------------------------------------------------------
# Scans — list (read-only)
# ---------------------------------------------------------------------------

@router.get(
    "/assist/scans",
    response_model=List[ScanBrief],
    summary="List scans in this project (most recent first)",
)
def list_assist_scans(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    # v2.434.2 (acceptance run R4) — no way past the newest 500 before.  A
    # page shorter than ``limit`` is the last one.
    offset: int = Query(0, ge=0, description="Skip this many (newest first); page until a page is shorter than limit."),
    tool: Optional[str] = Query(
        None, max_length=100,
        description="Only this tool's scans (nmap, nessus, netexec…) — the Scans page's tool chips",
    ),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    # The Scans page's own filter (v2.429.1, MCP acceptance run 2: "the last
    # two nmap scans" meant reading every scan).
    session = _load_assist_session(db, request)
    scans = (
        apply_scan_inventory_filters(
            db.query(models.Scan).filter(models.Scan.project_id == session.project_id),
            search=None, tool=tool, created_after=None,
        )
        # id breaks a tie: scans imported together share created_at, and a
        # page boundary between them would repeat or skip one.
        # scan_info (nmap's scanned port list) is on every row: one load for
        # the page, never one per scan.
        .options(selectinload(models.Scan.scan_info))
        .order_by(models.Scan.created_at.desc(), models.Scan.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    # The import behind each scan — one grouped query (a re-processed file has
    # several jobs; the newest is the one that produced this scan row).
    jobs = dict(
        db.query(models.IngestionJob.scan_id, func.max(models.IngestionJob.id))
        .filter(models.IngestionJob.scan_id.in_([s.id for s in scans]))
        .group_by(models.IngestionJob.scan_id).all()
    ) if scans else {}
    out = []
    for s in scans:
        row = ScanBrief.model_validate(s)
        row.ingestion_job_id = jobs.get(s.id)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Self — own session info
# ---------------------------------------------------------------------------

@router.get(
    "/assist/session",
    summary="Get the current assist session's metadata",
)
def get_assist_session_self(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Tiny self-introspection endpoint so the agent can confirm
    which session it's bound to + the operator's stated purpose.
    Useful for the agent's opening message ("I see you're asking
    about $purpose; here's what I can see in $project_name…")."""
    session = _load_assist_session(db, request)
    project_name = (
        db.query(Project.name)
        .filter(Project.id == session.project_id)
        .scalar()
    )
    # Agent feedback (v1.44.0): the agent could only discover its write grants
    # by *attempting* a write, and had no operator-relative context. It still
    # gets the operator — v2.309.0 removed the capability list, because a key's
    # authority is now simply its operator's project role.
    operator_id = getattr(request.state, "key_operator_id", None)
    operator = None
    if operator_id is not None:
        operator_name = (
            db.query(User.username).filter(User.id == operator_id).scalar()
        )
        operator = {"id": operator_id, "username": operator_name}
    return {
        "id": session.id,
        "project_id": session.project_id,
        "project_name": project_name,
        "purpose": session.purpose,
        "status": session.status,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "last_activity_at": session.last_activity_at.isoformat()
        if session.last_activity_at
        else None,
        # v2.309.0 — `capabilities` / `capability_constraint` removed. What this
        # key may write is the operator's project role, so `operator` is the
        # answer to "what may I do here" and there is no second list to consult.
        "operator": operator,
    }


# ---------------------------------------------------------------------------
# Posture + patterns — the two analysis reads (v2.294.0)
#
# Both wrap services that already compute these for the Posture hub rather
# than re-deriving them here.  That is the point: an agent that recomputed
# "how exposed is this project" from raw counts would quote different numbers
# than the page a manager is looking at, and the disagreement would surface as
# the agent being wrong.  One computation, two presentations.
#
# The split between them is state vs analysis, and it is why there is no
# separate `attention` tool: compute_posture already folds
# compute_project_attention and compute_site_attention in, so a third endpoint
# would be the same numbers under a third name.
# ---------------------------------------------------------------------------

@router.get(
    "/assist/posture",
    summary="The project's security condition — the headline, and why",
)
def get_assist_posture(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """"Where is this project?" in one call.

    v2.294.0.  Assist could count things but had no read on the project's
    overall condition, so "how are we doing?" — the first question of any
    session, and the one a report's executive summary answers — had to be
    assembled from a dozen counts and a judgment the agent invented.

    Deliberately trimmed against what the UI receives:

    * ``heatmap`` (the condition-family x site grid) is dropped — it is a
      display artefact, and rendering it as JSON spends context to describe a
      picture the agent cannot show anyone.
    * the full ``systemic`` block (every condition and blind spot) is dropped
      in favour of the counts in ``headline.systemic``; the analysis itself
      lives on ``/assist/patterns``, which also carries the segment comparison
      that this payload has never included.

    ``label`` is one of ``action_required`` / ``needs_assessment`` /
    ``insufficient_evidence`` / ``no_urgent_signals``.  The third is the one
    worth reading carefully: it means the estate has not been assessed enough
    to judge, which is NOT the same as the estate being clean, and an answer
    that reports it as "no issues found" is wrong.
    """
    session = _load_assist_session(db, request)
    p = compute_posture(db, session.project_id)
    return {
        "label": p["label"],
        "conclusion": p["conclusion"],
        "reasons": p["reasons"],
        "headline": p["headline"],
        "evidence": p["evidence"],
        "priorities": p["priorities"],
        "disposition": p["disposition"],
        "sites": p["sites"],
    }


@router.get(
    "/assist/patterns",
    summary="Cross-sectional analysis — what this estate has a problem with",
)
def get_assist_patterns(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """"Is this systemic, or is it one box?" — and "which segment is worse?".

    v2.294.0.  This is the analysis that turns an inventory into an
    assessment, and assist could not reach any of it.  Everything here is
    ``compute_systemic_insights``, which already backs the Patterns page:

    * ``blind_spots`` — conditions that span the estate, worst-first.  These
      are the "the inventory is all on an out-of-date OS" claims, and each
      carries the spread evidence that justifies stating it that broadly.
    * ``segment_outliers`` — subnets whose issue density is an outlier against
      the estate median.  This is the "hosts on this subnet look worse than
      the rest" claim, with the ratio behind it.  ``times_median`` is null when
      the estate has no non-zero baseline to compare against (a mostly-clean
      project), in which case the outlier was picked on absolute density —
      say so rather than quoting a multiple that does not exist.
    * ``conditions`` — every condition with its spread and classification
      (``isolated`` / ``recurring`` / ``estate_wide``).
    * ``family_summary`` — per pattern-family rollup carrying a root-cause
      hypothesis and a program-level control, which is what a recommendation
      section should be built from rather than a list of hosts.
    * ``diagnostic_profiles`` — per-subnet condition sets and root cause.

    **This is comparison across the estate, not change over time.**  Nothing
    here says "worse than last week"; the analysis is deliberately
    cross-sectional because an engagement runs weeks, not quarters.  Do not
    describe these as trends.

    ``adopted=False`` means the project has no scoped subnets, so the analysis
    cannot run at all — report that as "not assessable", never as "no patterns
    found".
    """
    session = _load_assist_session(db, request)
    ins = compute_systemic_insights(db, session.project_id)
    if not ins.get("adopted"):
        return {
            "adopted": False,
            "reason": (
                "No scoped subnets for this project, so cross-sectional "
                "analysis has nothing to segment by. Define a scope with "
                "subnets to enable it."
            ),
        }
    return {
        "adopted": True,
        "estate": ins.get("estate", {}),
        "blind_spots": ins.get("blind_spots", []),
        "segment_outliers": ins.get("segment_outliers", []),
        "conditions": ins.get("conditions", []),
        "family_summary": ins.get("family_summary", []),
        "diagnostic_profiles": ins.get("diagnostic_profiles", []),
        # family_matrix is omitted on purpose — it is the UI's heatmap grid.
    }


# ---------------------------------------------------------------------------
# Ingestion issues (v2.297.0)
#
# Why this is a tool and not a page link: every other assist tool answers a
# question about the data.  This one answers "should I trust that the data is
# all here?".  Without it, an empty result is indistinguishable from an upload
# that never parsed, and the agent reports the first with no way to suspect the
# second — the single most confident-sounding wrong answer this surface can
# produce.
# ---------------------------------------------------------------------------

class AssistIngestionIssue(BaseModel):
    """One upload that failed, is still in flight, or landed incomplete."""
    #: ``failed`` (the worker gave up), ``degraded`` (parsed, but rows were
    #: dropped) or ``parse_error`` (a recorded failure with no job row).
    kind: str
    filename: str
    tool_name: Optional[str] = None
    file_type: Optional[str] = None
    #: What to tell the operator — the user-facing parse message where one
    #: exists, otherwise the worker's error.
    message: Optional[str] = None
    #: Rows the parser dropped while still reporting success.  Non-zero means
    #: this file is IN the project but thinner than the file on disk.
    skipped_count: int = 0
    parser_warnings: Optional[str] = None
    job_id: Optional[int] = None
    parse_error_id: Optional[int] = None
    created_at: Optional[datetime] = None


class AssistIngestionIssues(BaseModel):
    queued: int = 0
    processing: int = 0
    failed: int = 0
    degraded: int = 0
    #: Unresolved parse errors with NO surviving job row — the ones that would
    #: otherwise go unreported, since a failed job already carries its error.
    #: Deliberately NOT the project's total: `failed` above accounts for the
    #: rest, and adding both would double-count the same failure.
    #:
    #: v2.313.0 — an agent reading this next to `assist_get_coverage`
    #: reasonably concluded the two disagreed: coverage counts EVERY unresolved
    #: parse error under the same words, so a project with 7 failed uploads
    #: reads as "7 unresolved" there and "0 unresolved + 7 failed" here. Both
    #: were right and neither said so. The total is now reported alongside, so
    #: the decomposition is visible instead of inferred.
    unresolved_parse_errors: int = Field(0, description=(
        "Unresolved parse errors with NO import job row — NOT the project's total "
        "(a failed job already counts under `failed`). For 'how many unresolved "
        "parse errors?' read unresolved_parse_errors_total."
    ))
    #: Every unresolved parse error in the project, however it is reached —
    #: the number `assist_get_coverage` reports as `parse_errors_unresolved`.
    #: `unresolved_parse_errors` is the subset of these with no job row.
    unresolved_parse_errors_total: int = Field(0, description=(
        "Every unresolved parse error in the project — the number assist_get_coverage "
        "reports as parse_errors_unresolved."
    ))
    #: True when anything at all is wrong or pending — the one field to check
    #: before concluding "there is no data for that".
    has_issues: bool = False
    issues: List[AssistIngestionIssue] = []
    total_issues: int = 0


@router.get(
    "/assist/ingestion-issues",
    response_model=AssistIngestionIssues,
    summary="Uploads that failed, stalled, or landed incomplete",
)
def list_assist_ingestion_issues(
    request: Request,
    limit: int = Query(25, ge=1, le=100),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """"Is the data actually all here?"

    v2.297.0.  Check this before reporting an absence.  "No web servers in that
    range" and "the httpx upload failed to parse" produce the identical empty
    result from every other tool on this surface, and only one of them is a
    finding about the network.

    Three kinds, deliberately distinguished:

    * ``failed`` — the worker gave up on the file. Nothing from it is in the
      project.
    * ``degraded`` — the file parsed and its data IS in the project, but the
      parser dropped rows (``skipped_count``). Counts drawn from it are
      undercounts, and this is invisible everywhere else: the job says
      "completed".
    * ``parse_error`` — a recorded parse failure with no surviving job row.

    ``queued`` / ``processing`` are uploads still in flight. They are not
    errors, but they mean the picture is incomplete *right now*, which is the
    same practical warning.

    If ``has_issues`` is false, an empty result elsewhere is a real absence and
    can be reported as one. If it is true, say what is missing before drawing a
    conclusion from what is present.
    """
    session = _load_assist_session(db, request)
    pid = session.project_id

    status_counts = dict(
        db.query(models.IngestionJob.status, func.count(models.IngestionJob.id))
        .filter(models.IngestionJob.project_id == pid)
        .group_by(models.IngestionJob.status)
        .all()
    )

    failed_jobs = (
        db.query(models.IngestionJob)
        .filter(
            models.IngestionJob.project_id == pid,
            models.IngestionJob.status == "failed",
        )
        .order_by(models.IngestionJob.created_at.desc())
        .all()
    )
    degraded_jobs = (
        db.query(models.IngestionJob)
        .filter(
            models.IngestionJob.project_id == pid,
            models.IngestionJob.status == "completed",
            models.IngestionJob.skipped_count > 0,
        )
        .order_by(models.IngestionJob.created_at.desc())
        .all()
    )

    # A failed job usually carries a parse_error_id, so listing both tables
    # unfiltered would report the same upload twice under two names.  Fold the
    # parse error's user-facing message into the job row and list only the
    # parse errors no job points at.
    claimed_error_ids = {
        j.parse_error_id for j in failed_jobs if j.parse_error_id is not None
    }
    error_messages: Dict[int, str] = {}
    if claimed_error_ids:
        error_messages = {
            eid: (msg or "")
            for eid, msg in db.query(models.ParseError.id, models.ParseError.user_message)
            .filter(models.ParseError.id.in_(claimed_error_ids)).all()
        }

    unresolved_query = (
        db.query(models.ParseError)
        .filter(
            models.ParseError.project_id == pid,
            models.ParseError.status == "unresolved",
        )
    )
    # Same predicate assist_get_coverage / compute_evidence_coverage uses, so
    # the two tools can be read side by side (see AssistIngestionIssues).
    unresolved_total = unresolved_query.count()
    orphan_query = unresolved_query
    if claimed_error_ids:
        orphan_query = orphan_query.filter(
            ~models.ParseError.id.in_(claimed_error_ids)
        )
    orphan_errors = orphan_query.order_by(models.ParseError.created_at.desc()).all()

    issues: List[AssistIngestionIssue] = []
    for j in failed_jobs:
        issues.append(AssistIngestionIssue(
            kind="failed",
            filename=j.original_filename or j.filename,
            tool_name=j.tool_name,
            message=(
                error_messages.get(j.parse_error_id)
                or j.error_message
                or j.last_error
            ),
            job_id=j.id,
            parse_error_id=j.parse_error_id,
            created_at=j.created_at,
        ))
    for j in degraded_jobs:
        issues.append(AssistIngestionIssue(
            kind="degraded",
            filename=j.original_filename or j.filename,
            tool_name=j.tool_name,
            message=(
                f"Parsed, but {j.skipped_count} row(s) were dropped — data "
                "from this file is incomplete."
            ),
            skipped_count=j.skipped_count or 0,
            parser_warnings=j.parser_warnings,
            job_id=j.id,
            created_at=j.created_at,
        ))
    for e in orphan_errors:
        issues.append(AssistIngestionIssue(
            kind="parse_error",
            filename=e.filename,
            file_type=e.file_type,
            message=e.user_message or e.error_message,
            parse_error_id=e.id,
            created_at=e.created_at,
        ))

    # Newest first, with undated rows last rather than first — reverse-sorting
    # a "is None" flag would float them to the top, which is the opposite of
    # what "most recent" means to a reader.
    _oldest = datetime.min.replace(tzinfo=timezone.utc)
    issues.sort(key=lambda i: i.created_at or _oldest, reverse=True)
    queued = int(status_counts.get("queued", 0) or 0)
    processing = int(status_counts.get("processing", 0) or 0)
    return AssistIngestionIssues(
        queued=queued,
        processing=processing,
        failed=len(failed_jobs),
        degraded=len(degraded_jobs),
        unresolved_parse_errors=len(orphan_errors),
        unresolved_parse_errors_total=unresolved_total,
        has_issues=bool(issues) or queued > 0 or processing > 0,
        # total_issues is the unpaginated count; `issues` is capped, so a
        # truncated list must not read as the complete set.
        total_issues=len(issues),
        issues=issues[:limit],
    )


# ---------------------------------------------------------------------------
# Finding detail — the evidence behind one finding (v2.294.0)
# ---------------------------------------------------------------------------

class AssistFindingNote(BaseModel):
    id: int
    body: str
    note_type: Optional[str] = None
    author: Optional[str] = None
    # v2.343.2 — 'user' or 'agent': whether a person asserted this or an
    # earlier agent did, which a write-up citing it has to say.
    actor_type: Optional[str] = None
    created_at: Optional[datetime] = None
    # Threads, as on host notes: a reply names its parent and its thread root;
    # a root has parent_id null (agent feedback #23 — without these a finding's
    # comment thread read as a flat list).
    parent_id: Optional[int] = None
    thread_root_id: Optional[int] = None
    attachments: List[AssistAttachment] = []


class AssistFindingHost(BaseModel):
    #: The endpoint row's own id — what ``propose_endpoint_status`` and
    #: ``record_evidence`` take as ``finding_host_id``.  It was not returned,
    #: so an agent could read an endpoint and had no way to name it (agent
    #: feedback #26, 2026-10-02).
    finding_host_id: int
    host_id: int
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
    # v2.343.2 — the named endpoint this row is about, when the finding was
    # recorded against a name rather than the bare address.  Two vhosts on
    # one IP are two rows with the same host_id and different name_id/fqdn.
    name_id: Optional[int] = None
    fqdn: Optional[str] = None
    host_status: str


class AssistFindingDetail(BaseModel):
    id: int
    title: str
    severity: str
    status: str
    source: str
    owner_username: Optional[str] = None
    created_by_username: Optional[str] = None
    created_at: Optional[datetime] = None
    # Distinct affected addresses.  ``endpoint_count`` is affected rows, which
    # exceeds it when named endpoints share an IP (v2.343.2).
    host_count: int = 0
    endpoint_count: int = 0
    hosts: List[AssistFindingHost] = []
    hosts_truncated: bool = False
    # The note that justified promotion (note-sourced findings only).
    evidence_note: Optional[AssistFindingNote] = None
    # v2.343.2 — the replies on that note's thread, oldest first.  A
    # qualification ("only on the staging vhost") or a screenshot posted as a
    # reply is part of the evidence; before this the detail carried the root
    # alone and those replies — and their attachments — were silently absent.
    evidence_thread: List[AssistFindingNote] = []
    # The finding's own discussion thread.
    comments: List[AssistFindingNote] = []
    # Provenance for scanner- and execution-sourced findings.  At most
    # ``_SCANNER_EVIDENCE_CAP`` rows (review 2026-10-01 C2); the total says
    # how many scanner rows evidence the finding.
    scanner_evidence: List[dict] = []
    scanner_evidence_total: int = 0
    scanner_evidence_truncated: bool = False
    # v2.442.0 — the agent evidence records that bear on this finding (what
    # was run, and what came back).  It was ``execution_evidence``: one
    # test-plan execution result.
    evidence_records: List[dict] = []
    # --- v2.428.0: what the finding page shows beyond the evidence ---------
    updated_at: Optional[datetime] = None
    # Per-endpoint state counts (open / remediated / retest / false_positive):
    # the finding's status is the issue's, an endpoint's is its own.
    endpoint_status_counts: Dict[str, int] = {}
    # What the client report says about the issue (Markdown), exactly as the
    # finding page's report-text editor holds it.
    report_text: Dict[str, Any] = {}
    # The finding's images as the client report sees them (the finding page's
    # list, from the same service): ``id``, ``caption``, ``filename``,
    # ``in_report`` (ticked for the report), ``printable`` (a format the
    # report prints), ``placed_in`` (the report-text fields whose Markdown
    # places it with ``![caption](evidence:<id>)``) and ``download_path``.  A
    # ticked image no field places prints under Evidence.  When proposing a
    # rewrite of a field, keep the references it holds unless the image should
    # move back under Evidence; reference only ids listed here.
    images: List[Dict[str, Any]] = []
    # The disposition trail, newest first: who changed the status, when,
    # from what to what, and the justification they gave.
    status_history: List[Dict[str, Any]] = []


_FINDING_HOST_CAP = 100
#: Scanner rows listed on a finding's detail; ``scanner_evidence_total`` is all of them.
_SCANNER_EVIDENCE_CAP = 100
#: A finding's status changes are few; the cap only stops a pathological one.
_STATUS_HISTORY_CAP = 100


def _serialize_finding_note(note, attachments_by_note, uploaders=None) -> "AssistFindingNote":
    return AssistFindingNote(
        id=note.id,
        body=note.body,
        note_type=note.note_type,
        author=note.author.username if note.author else None,
        actor_type=note.actor_type,
        created_at=note.created_at,
        parent_id=note.parent_id,
        thread_root_id=note.thread_root_id,
        attachments=[
            _assist_attachment(a, uploaders or {})
            for a in attachments_by_note.get(note.id, [])
        ],
    )


@router.get(
    "/assist/findings/{finding_id}",
    response_model=AssistFindingDetail,
    summary="One finding with its evidence — the note, the thread, the screenshots",
)
def get_assist_finding(
    request: Request,
    finding_id: int = Path(..., ge=1),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The material a write-up cites, for one finding.

    v2.294.0.  ``/assist/findings`` lists what exists; this is what is behind
    one of them.  A promoted finding carries an ``evidence_annotation_id`` —
    the note a human wrote to justify promoting it — plus its own comment
    thread, and screenshots hang off both as note attachments.  Until now none
    of that was reachable from assist, so an agent asked to write findings up
    had the titles and the severities and none of the evidence.

    Attachments come back as references (filename, type, size, path), not
    bytes.  Fetch each from ``download_path`` with the session's API key.

    ``scanner_evidence`` / ``evidence_records`` carry the other two
    provenances: which scanner rows evidence this finding, and what command a
    tester actually ran.  Report which one a claim rests on — "Nessus reported"
    and "a tester confirmed" are different assertions, and conflating them is
    how a report overstates its own confidence.
    """
    from app.db.models_findings import Finding, FindingHost, FindingVulnerability

    session = _load_assist_session(db, request)
    finding = (
        db.query(Finding)
        .options(joinedload(Finding.owner), joinedload(Finding.created_by))
        .filter(Finding.id == finding_id, Finding.project_id == session.project_id)
        .first()
    )
    if finding is None:
        # Project-scoped 404: a finding in another project must be
        # indistinguishable from one that does not exist.
        raise HTTPException(status_code=404, detail="Finding not found in this project")

    # --- affected hosts ---------------------------------------------------
    # Review 2026-10-01 C2 — this loaded every endpoint as three entities and
    # sliced the list in Python.  The cap is now in SQL, the rows are columns,
    # and the totals come from one grouped count.
    hosts = [
        AssistFindingHost(
            finding_host_id=r.finding_host_id,
            host_id=r.host_id,
            ip_address=r.ip_address,
            hostname=r.hostname,
            name_id=r.name_id,
            fqdn=r.fqdn,
            host_status=r.host_status,
        )
        for r in (
            db.query(
                FindingHost.id.label("finding_host_id"),
                FindingHost.host_id, FindingHost.name_id, FindingHost.host_status,
                models.Host.ip_address, models.Host.hostname, models.DNSName.fqdn,
            )
            .join(models.Host, FindingHost.host_id == models.Host.id)
            .outerjoin(models.DNSName, FindingHost.name_id == models.DNSName.id)
            .filter(FindingHost.finding_id == finding.id)
            .order_by(models.Host.ip_address, models.DNSName.fqdn, FindingHost.id)
            .limit(_FINDING_HOST_CAP)
        )
    ]
    endpoint_status_counts: Dict[str, int] = {
        state: int(n) for state, n in (
            db.query(FindingHost.host_status, func.count(FindingHost.id))
            .filter(FindingHost.finding_id == finding.id)
            .group_by(FindingHost.host_status)
        )
    }
    endpoint_count = sum(endpoint_status_counts.values())
    distinct_host_count = (
        db.query(func.count(func.distinct(FindingHost.host_id)))
        .filter(FindingHost.finding_id == finding.id)
        .scalar()
    ) or 0

    # --- notes: the evidence note (thread root + its replies) and the
    #     finding's own comment thread, fetched together so attachments are
    #     one query rather than one per note.
    note_q = db.query(models.Annotation).options(joinedload(models.Annotation.author))
    comment_notes = (
        note_q.filter(models.Annotation.finding_id == finding.id)
        .order_by(models.Annotation.created_at.asc())
        .all()
    )
    evidence_note = None
    evidence_replies: list = []
    if finding.evidence_annotation_id:
        evidence_note = (
            db.query(models.Annotation)
            .options(joinedload(models.Annotation.author))
            .filter(models.Annotation.id == finding.evidence_annotation_id)
            .first()
        )
        if evidence_note is not None:
            # The rest of the source thread.  ``thread_root_id`` is the
            # canonical link; ``parent_id`` covers a reply written before the
            # root was stamped (roots point at themselves, so exclude it).
            evidence_replies = (
                note_q.filter(
                    or_(
                        models.Annotation.thread_root_id == evidence_note.id,
                        models.Annotation.parent_id == evidence_note.id,
                    ),
                    models.Annotation.id != evidence_note.id,
                )
                .order_by(models.Annotation.created_at.asc(), models.Annotation.id.asc())
                .all()
            )

    note_ids = [n.id for n in comment_notes] + [n.id for n in evidence_replies]
    if evidence_note is not None:
        note_ids.append(evidence_note.id)
    attachments_by_note: dict = {}
    if note_ids:
        for att in (
            db.query(models.NoteAttachment)
            .filter(models.NoteAttachment.annotation_id.in_(note_ids))
            .order_by(models.NoteAttachment.created_at)
            .all()
        ):
            attachments_by_note.setdefault(att.annotation_id, []).append(att)
    uploaders = _uploader_names(db, [a for atts in attachments_by_note.values() for a in atts])

    # --- scanner / execution provenance ----------------------------------
    # Capped, with the total beside it (C2): an issue-wide promotion links one
    # scanner row per affected host, and every one was returned as an entity.
    scanner_evidence: List[dict] = []
    linked = db.query(FindingVulnerability.vuln_id).filter(FindingVulnerability.finding_id == finding.id)
    evidences = Vulnerability.id.in_(linked)
    if finding.vuln_id:
        evidences = or_(evidences, Vulnerability.id == finding.vuln_id)
    scanner_evidence_total = db.query(func.count(Vulnerability.id)).filter(evidences).scalar() or 0
    if scanner_evidence_total:
        for v in (
            db.query(
                Vulnerability.id, Vulnerability.host_id, Vulnerability.source, Vulnerability.plugin_id,
                Vulnerability.title, Vulnerability.severity, Vulnerability.cve_id,
            )
            .filter(evidences)
            .order_by(Vulnerability.id)
            .limit(_SCANNER_EVIDENCE_CAP)
        ):
            scanner_evidence.append({
                "vuln_id": v.id,
                "host_id": v.host_id,
                "source": v.source.value if hasattr(v.source, "value") else v.source,
                "plugin_id": v.plugin_id,
                "title": v.title,
                "severity": v.severity.value if hasattr(v.severity, "value") else v.severity,
                "cve_id": v.cve_id,
            })

    # Evidence records linked to the finding — the same rows the host's Agent
    # evidence section shows (serialize_evidence: preview, never raw output).
    from app.services import agent_evidence_service
    evidence_rows, _ = agent_evidence_service.list_evidence(
        db, session.project_id, finding_id=finding.id, limit=50,
    )
    evidence_records = [agent_evidence_service.serialize_evidence(r) for r in evidence_rows]

    # --- v2.428.0: report text, endpoint states, status history -----------
    from app.db.models_findings import FindingStatusHistory
    from app.services.report_text import report_text_of

    status_history = [
        {
            "from_status": r.from_status,
            "to_status": r.to_status,
            "changed_by": r.changed_by.username if r.changed_by else None,
            "changed_by_name": (r.changed_by.full_name or r.changed_by.username) if r.changed_by else None,
            "summary": r.summary,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in (
            db.query(FindingStatusHistory)
            .options(joinedload(FindingStatusHistory.changed_by))
            .filter(FindingStatusHistory.finding_id == finding.id)
            .order_by(FindingStatusHistory.created_at.desc(), FindingStatusHistory.id.desc())
            .limit(_STATUS_HISTORY_CAP)
            .all()
        )
    ]

    # The images as the report sees them — the finding page's list
    # (``GET /findings/{id}/images``), from the same service.
    from app.services import report_images

    images = [
        {
            "id": img["id"], "caption": img["caption"], "filename": img["filename"],
            "in_report": img["in_report"], "printable": img["printable"],
            "placed_in": img["placed_in"],
            "download_path": f"/api/v1/agent/assist/attachments/{img['id']}",
        }
        for img in report_images.finding_images(db, finding)
    ]

    return AssistFindingDetail(
        updated_at=finding.updated_at,
        endpoint_status_counts=endpoint_status_counts,
        report_text=report_text_of(finding),
        images=images,
        status_history=status_history,
        id=finding.id,
        title=finding.title,
        severity=finding.severity,
        status=finding.status,
        source=finding.source,
        owner_username=finding.owner.username if finding.owner else None,
        created_by_username=finding.created_by.username if finding.created_by else None,
        created_at=finding.created_at,
        host_count=distinct_host_count,
        endpoint_count=endpoint_count,
        hosts=hosts,
        hosts_truncated=endpoint_count > _FINDING_HOST_CAP,
        scanner_evidence_total=scanner_evidence_total,
        scanner_evidence_truncated=scanner_evidence_total > _SCANNER_EVIDENCE_CAP,
        evidence_note=(
            _serialize_finding_note(evidence_note, attachments_by_note, uploaders)
            if evidence_note is not None else None
        ),
        evidence_thread=[_serialize_finding_note(n, attachments_by_note, uploaders) for n in evidence_replies],
        comments=[_serialize_finding_note(n, attachments_by_note, uploaders) for n in comment_notes],
        scanner_evidence=scanner_evidence,
        evidence_records=evidence_records,
    )


@router.get(
    "/assist/attachments/{attachment_id}",
    summary="Download a note image attachment (project-scoped)",
)
def download_assist_attachment(
    request: Request,
    attachment_id: int = Path(..., ge=1),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Serve the bytes behind an attachment reference.

    v2.294.0.  ``assist_get_finding`` hands out the paths (to save beside a
    report); since v2.428.0 the ``assist_get_image`` MCP tool loops back here to
    show one inline, so role, scope and audit stay at this route; this is what those
    paths resolve to for a key-authenticated caller (the operator-facing
    equivalent under ``/projects/...`` requires a JWT, which an agent does not
    have).

    Scoped to the session's project, and path-checked against the attachments
    root so a crafted ``storage_path`` cannot escape it.
    """
    from app.services.note_attachment_service import _attachments_root

    session = _load_assist_session(db, request)
    att = (
        db.query(models.NoteAttachment)
        .filter(
            models.NoteAttachment.id == attachment_id,
            models.NoteAttachment.project_id == session.project_id,
        )
        .first()
    )
    if att is None:
        raise HTTPException(status_code=404, detail="Attachment not found in this project")
    base = _attachments_root()
    try:
        target = (base / att.storage_path).resolve()
        target.relative_to(base.resolve())
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="Attachment path invalid")
    require_readable_file(target, "Attachment")
    return FileResponse(path=str(target), media_type=att.content_type, filename=att.filename)


@router.get(
    "/assist/web-interfaces/{interface_id}/screenshot",
    summary="Download an EyeWitness screenshot of a web interface",
)
def download_assist_web_screenshot(
    request: Request,
    interface_id: int = Path(..., ge=1),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The second screenshot store, given the same treatment as note
    attachments.

    v2.297.0.  Note attachments are evidence an analyst chose to record;
    EyeWitness screenshots are captured automatically at ingest and live in a
    separate store (``web_interfaces.screenshot_path``). A write-up showing
    what an exposed admin panel actually looks like usually wants this one.

    ``assist_get_host`` hands out the paths; ``assist_get_image`` (v2.428.0)
    loops back here to show one inline.

    Project-scoped and path-checked against the screenshot root, mirroring the
    operator-facing route (which requires a JWT an agent does not have).
    """
    from pathlib import Path as FsPath
    from app.core.config import settings

    session = _load_assist_session(db, request)
    row = (
        db.query(models.WebInterface)
        .filter(
            models.WebInterface.id == interface_id,
            models.WebInterface.project_id == session.project_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Web interface not found in this project")
    if not row.screenshot_path:
        raise HTTPException(
            status_code=404,
            detail=(
                "No screenshot captured for this interface — httpx and "
                "CSV-only EyeWitness uploads record the interface without one."
            ),
        )

    base = (FsPath(settings.UPLOAD_DIR) / "web_screenshots").resolve()
    try:
        target = (base / row.screenshot_path).resolve()
        target.relative_to(base)
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="Screenshot path invalid")
    require_readable_file(target, "Screenshot")
    return FileResponse(
        path=str(target),
        media_type="image/png",
        filename=f"web-interface-{row.id}.png",
    )
