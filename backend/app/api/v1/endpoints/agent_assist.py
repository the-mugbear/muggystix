"""
Agent API — interactive assist workflow (v2.64.0).

Read-only, project-scoped surface for "ask questions about hosts"
agents.  Designed to support the senior-tester use case where the
operator wants to query their project — "which hosts expose FTP?",
"summarize my critical findings", "what did the last recon turn up?"
— without minting a plan key and triggering plan-approval ceremony.

All endpoints gate on ``require_assist_scope`` (api_keys.assist_session_id
set).  Plan, recon, and execution keys are rejected here, mirroring
the cleanly-separated workflow boundaries on the other agent
surfaces.

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
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload, Query as SAQuery

from app.db.session import get_db
from app.db import models
from app.db.models_agent import (
    Agent,
    AssistSession,
    AssistSessionStatus,
    ReconSession,
)
from app.db.models_project import Project, ProjectMembership
from app.db.models_auth import User
from app.api.deps import require_assist_scope

from app.api.v1.endpoints.agent_schemas import (
    AssistFinding,
    AssistFindingsResponse,
    EnvironmentProbeRequest,
    EnvironmentProbeResponse,
    EnvironmentSummary,
    HostBrief,
    HostDetail,
    PortBrief,
    ScanBrief,
    ScopeBrief,
    VulnCounts,
)
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.api.v1.endpoints.agent_common import (
    _apply_agent_host_filters,
    _batch_host_enrichment,
)
from app.services.agent_environment_probe_service import apply_environment_probe
from app.services.agent_prompt_history import PROMPT_VERSION
from app.services.posture_service import compute_posture
from app.services.systemic_insight_service import compute_systemic_insights
from app.services.subnet_insight_service import compute_subnet_insights

router = APIRouter()


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------

def _load_assist_session(db: Session, request: Request) -> AssistSession:
    """Resolve the AssistSession for the caller's assist-scoped key.

    Assist keys bind to exactly one session via
    ``api_keys.assist_session_id`` (require_assist_scope already
    enforced not-NULL).  Defence-in-depth: also verify the session
    is still ACTIVE — if a parallel "end session" call landed first,
    we want this request to 401/404 rather than silently serve data
    on what the human thinks is a closed session.
    """
    session_id = getattr(request.state, "scoped_assist_session_id", None)
    if session_id is None:
        raise HTTPException(status_code=403, detail="Assist scope not bound")
    session = db.query(AssistSession).filter(AssistSession.id == session_id).first()
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Assist session not found. The session may have been "
                "deleted server-side; ask the user to start a new one."
            ),
        )
    if session.status != AssistSessionStatus.ACTIVE.value:
        raise HTTPException(
            status_code=410,
            detail=(
                f"Assist session is '{session.status}' — start a new "
                f"session via the BlueStick UI to continue."
            ),
        )
    # Defence-in-depth: the session must belong to the same project as the
    # authenticating key's agent (guards a corrupted/hand-edited api_keys row
    # that paired an agent with another project's session).
    scoped_project = getattr(request.state, "scoped_agent_project_id", None)
    if scoped_project is not None and session.project_id != scoped_project:
        raise HTTPException(status_code=403, detail="Assist session does not belong to this project")
    return session


# ---------------------------------------------------------------------------
# Environment probe
# ---------------------------------------------------------------------------

@router.post(
    "/assist/sessions/{session_id}/environment",
    response_model=EnvironmentProbeResponse,
    summary="Record the operator's environment probe (MANDATORY first step)",
)
def record_assist_environment(
    body: EnvironmentProbeRequest,
    request: Request,
    session_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_assist_scope),
    db: Session = Depends(get_db),
):
    """Persist the agent's environment probe onto the assist session.

    Same shape as the recon/execution probe so the audit story stays
    symmetric across workflows.  For assist, the probe matters less
    than for recon/execution (assist commands are API calls, not
    shell invocations) but is captured for two reasons:

    1. Symmetry — future assist features (bulk follow, scan-from-
       filter) may need it.
    2. Audit completeness — the operator's environment at the time
       of the session is part of the "who/where/what" record.
    """
    session = _load_assist_session(db, request)
    if session.id != session_id:
        # Path param disagrees with the key's binding — refuse.  The
        # agent should hit /sessions/{their_own_id}/environment, not
        # someone else's id.
        raise HTTPException(
            status_code=403,
            detail=(
                "Path session_id does not match this API key's bound "
                "session.  Use the session id returned at start time."
            ),
        )
    apply_environment_probe(
        session=session,
        body=body,
        request=request,
        agent=agent,
        active_statuses=[AssistSessionStatus.ACTIVE.value],
        session_kind="assist",
    )
    db.commit()
    # v2.64.1 — initial v2.64.0 commit omitted session_type +
    # probed_by_user_id + probed_from_ip, which made Pydantic 500 the
    # response AFTER the DB write committed.  The audit log + the
    # `environment_probed: true` field on /assist/context revealed
    # the data had persisted, but the agent saw a confusing 500 and
    # retried (creating a noisy audit trail).  Match recon/execution
    # exactly so the response model validates cleanly.
    return EnvironmentProbeResponse(
        session_id=session.id,
        session_type="assist",
        probed_at=session.environment_probed_at,
        probed_by_user_id=session.environment_probed_by_user_id,
        probed_from_ip=session.environment_probed_from_ip,
        environment=EnvironmentSummary(**(session.environment or {})),
        agent_model=session.generated_by_model,
        agent_tool=session.generated_by_tool,
        agent_prompt_version=session.prompt_version,
    )


# ---------------------------------------------------------------------------
# Context — project overview
# ---------------------------------------------------------------------------

@router.get(
    "/assist/context",
    summary="Project context — host/scan/scope summary the assist agent grounds queries in",
)
def get_assist_context(
    request: Request,
    agent: Agent = Depends(require_assist_scope),
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

    # Recent recon sessions (5)
    recent_recon = (
        db.query(ReconSession)
        .filter(ReconSession.project_id == project.id)
        .order_by(ReconSession.started_at.desc())
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

    return {
        "prompt_version": PROMPT_VERSION,
        "session": {
            "id": session.id,
            "purpose": session.purpose,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "environment_probed": session.environment_probed_at is not None,
        },
        "project": {
            "id": project.id,
            "name": project.name,
            "slug": project.slug,
            "description": project.description,
            "status": project.status,
        },
        "totals": {
            "host_count": host_count,
            "up_host_count": up_count,
            "open_port_count": open_port_count,
            "scope_count": scope_count_total,
            "scan_count": scan_count_total,
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
        "recent_recon_sessions": [
            {
                "id": r.id,
                "scope_id": r.scope_id,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "hosts_discovered": r.hosts_discovered,
            }
            for r in recent_recon
        ],
    }


# ---------------------------------------------------------------------------
# Hosts — list + detail
# ---------------------------------------------------------------------------

def _build_assist_host_query(
    db: Session,
    session: AssistSession,
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
    query = _apply_agent_host_filters(
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
    h: models.Host, port_counts: dict, vuln_map: dict, follow_map: dict = None
) -> dict:
    """Serialize one host to the HostBrief-shaped dict used by the NDJSON stream."""
    vc = vuln_map.get(h.id, {})
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
        port_counts, vuln_map, _, _, _ = _batch_host_enrichment(db, host_ids)
        follow_map = _operator_follow_map(db, host_ids, operator_id)
        for h in hosts:
            yield json.dumps(_host_to_brief_dict(h, port_counts, vuln_map, follow_map)) + "\n"
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
    ports: Optional[str] = Query(None, description="Comma-separated port numbers"),
    services: Optional[str] = Query(None, description="Comma-separated service names"),
    subnets: Optional[str] = Query(None, description="Comma-separated CIDR blocks"),
    has_critical_vulns: Optional[bool] = Query(None),
    has_high_vulns: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Search IP, hostname, or OS"),
    q: Optional[str] = Query(None, description="Boolean query DSL — see /assist/hosts."),
    agent: Agent = Depends(require_assist_scope),
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
    response_model=List[HostBrief],
    summary="List hosts — same filter shape as the host inventory page",
)
def list_assist_hosts(
    request: Request,
    state: Optional[str] = Query(None),
    ports: Optional[str] = Query(None, description="Comma-separated port numbers"),
    services: Optional[str] = Query(None, description="Comma-separated service names"),
    subnets: Optional[str] = Query(None, description="Comma-separated CIDR blocks"),
    has_critical_vulns: Optional[bool] = Query(None),
    has_high_vulns: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Search IP, hostname, or OS"),
    q: Optional[str] = Query(
        None,
        description=(
            "Boolean query DSL — the SAME vocabulary as the Hosts page. "
            "Fields: port, os, service, subnet, tag, label, site, cve, vuln, "
            "header, webtitle, tech, note, scan, has:, follow:, assigned:. "
            "Combine with AND / OR / NOT and parentheses; comma = OR within a "
            "field, a repeated field = AND. ANDs with the discrete filters "
            "above. follow: and assigned: resolve against the operator who "
            "started this (read-only) assist session — e.g. "
            "'follow:in_review' = hosts you have in review, 'assigned:me'. "
            "A malformed query returns 400."
        ),
    ),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(require_assist_scope),
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
    """
    session = _load_assist_session(db, request)
    query = _build_assist_host_query(
        db, session,
        state=state, ports=ports, services=services, subnets=subnets,
        has_critical_vulns=has_critical_vulns, has_high_vulns=has_high_vulns,
        search=search, q=q,
    )
    hosts = query.order_by(models.Host.ip_address).offset(offset).limit(limit).all()
    if not hosts:
        return []
    host_ids = [h.id for h in hosts]
    port_counts, vuln_map, _, _, _ = _batch_host_enrichment(db, host_ids)
    follow_map = _operator_follow_map(db, host_ids, session.started_by_id)
    result = []
    for h in hosts:
        vc = vuln_map.get(h.id, {})
        result.append(
            HostBrief(
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
    return result


@router.get(
    "/assist/hosts.ndjson",
    summary="Stream ALL matching hosts as newline-delimited JSON (download to disk)",
    response_class=StreamingResponse,
)
def download_assist_hosts_ndjson(
    request: Request,
    state: Optional[str] = Query(None),
    ports: Optional[str] = Query(None, description="Comma-separated port numbers"),
    services: Optional[str] = Query(None, description="Comma-separated service names"),
    subnets: Optional[str] = Query(None, description="Comma-separated CIDR blocks"),
    has_critical_vulns: Optional[bool] = Query(None),
    has_high_vulns: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Search IP, hostname, or OS"),
    q: Optional[str] = Query(None, description="Boolean query DSL — same vocabulary as /assist/hosts."),
    agent: Agent = Depends(require_assist_scope),
    db: Session = Depends(get_db),
):
    """The complete host set — uncapped, one JSON object per line — for when the
    answer doesn't fit a context window.

    Use this instead of paging ``/assist/hosts`` when the project has thousands
    of hosts: redirect it to a file and process it locally so coverage stays
    complete without the payload ever being read into the model:

        curl -sk -H "X-API-Key: $KEY" .../assist/hosts.ndjson -o hosts.jsonl
        jq -c 'select(.open_port_count > 0 and .os_family == "Windows")' hosts.jsonl

    Same fields, same IP ordering, and same filter vocabulary as
    ``GET /assist/hosts`` — the identical dataset, delivered so it can be
    processed without being read whole. Server memory is bounded (rows are
    paged as they stream).
    """
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
    port: Optional[int] = None
    status_code: Optional[int] = None
    title: Optional[str] = None
    server_header: Optional[str] = None
    technologies: List[str] = []
    #: Present only when EyeWitness captured a PNG.  Same contract as note
    #: attachments: a path to curl to disk, never bytes in a tool result.
    screenshot_download_path: Optional[str] = None


class AssistHostDetail(HostDetail):
    """Assist's host detail — ``HostDetail`` plus what the host is serving.

    A subclass rather than a field on the shared schema: ``HostDetail`` is also
    the recon/plan browse payload, and those workflows have no use for
    screenshot download paths.
    """
    web_interfaces: List[AssistWebInterface] = []


#: Per host. Enough to characterise what a host serves without turning a host
#: lookup into a page dump; `assist_list_hosts` with `has:web` finds the rest.
_WEB_INTERFACE_CAP = 10
#: Technology lists come from Wappalyzer and can run long on a CMS.
_TECH_CAP = 12


@router.get(
    "/assist/hosts/{host_id}",
    response_model=AssistHostDetail,
    summary="Host detail with open ports and the web services it exposes",
)
def get_assist_host(
    request: Request,
    host_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_assist_scope),
    db: Session = Depends(get_db),
):
    session = _load_assist_session(db, request)
    host = (
        db.query(models.Host)
        .options(joinedload(models.Host.ports))
        .filter(
            models.Host.id == host_id,
            models.Host.project_id == session.project_id,
        )
        .first()
    )
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    port_briefs = [PortBrief.model_validate(p) for p in host.ports]
    open_count = sum(1 for p in host.ports if p.state == "open")
    _, vuln_map, _, _, _ = _batch_host_enrichment(db, [host.id])
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
    web_interfaces = [
        AssistWebInterface(
            id=w.id,
            url=w.url,
            port=w.port,
            status_code=w.status_code,
            title=w.title,
            server_header=w.server_header,
            technologies=[str(t) for t in (w.technologies or [])][:_TECH_CAP],
            screenshot_download_path=(
                f"/api/v1/agent/assist/web-interfaces/{w.id}/screenshot"
                if w.screenshot_path else None
            ),
        )
        for w in web_rows
    ]

    return AssistHostDetail(
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
        ports=port_briefs,
        web_interfaces=web_interfaces,
    )


_SEVERITY_RANK = case(
    (Vulnerability.severity == VulnerabilitySeverity.CRITICAL, 0),
    (Vulnerability.severity == VulnerabilitySeverity.HIGH, 1),
    (Vulnerability.severity == VulnerabilitySeverity.MEDIUM, 2),
    (Vulnerability.severity == VulnerabilitySeverity.LOW, 3),
    (Vulnerability.severity == VulnerabilitySeverity.INFO, 4),
    else_=5,
)
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
    agent: Agent = Depends(require_assist_scope),
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
        q.order_by(_SEVERITY_RANK, func.coalesce(Vulnerability.cvss_score, 0).desc(), Vulnerability.id)
        .offset(offset)
        .limit(limit)
        .all()
    )

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
    ports: Optional[str] = Query(None, description="Comma-separated port numbers"),
    services: Optional[str] = Query(None, description="Comma-separated service names"),
    subnets: Optional[str] = Query(None, description="Comma-separated CIDR blocks"),
    has_critical_vulns: Optional[bool] = Query(None),
    has_high_vulns: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, description="Search IP, hostname, or OS"),
    q: Optional[str] = Query(None, description="Boolean query DSL — same vocabulary as /assist/hosts."),
    agent: Agent = Depends(require_assist_scope),
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
    stream whole into context: ``curl -sk -H 'X-API-Key: <key>'
    '<base>/agent/assist/report-context.ndjson' -o report-context.jsonl``.
    """
    from app.services.report_generator import ReportGenerator  # heavy stack — lazy

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
    host_count: int = 0
    hosts: List[str] = []


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
    status: Optional[str] = Query(None, description="open / triaged / confirmed / remediated / closed / false_positive; 'all' for every status."),
    severity: Optional[str] = Query(None, description="critical / high / medium / low / info."),
    source: Optional[str] = Query(None),
    host_id: Optional[int] = Query(None, description="Only findings affecting this host."),
    unowned: bool = Query(False, description="Only findings with no owner — the work-allocation question."),
    owner: Optional[str] = Query(None, description="Username of the owner (or 'me')."),
    search: Optional[str] = Query(None, max_length=200, description="Substring match on the title."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(require_assist_scope),
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
    rows, total = svc.list_findings(**filters, severity=severity, limit=limit, offset=offset)
    counts = svc.severity_counts(**filters)

    findings = []
    for f in rows:
        host_ips = [fh.host.ip_address for fh in (f.hosts or []) if fh.host]
        findings.append(
            AssistProjectFinding(
                id=f.id,
                title=f.title,
                severity=f.severity,
                status=f.status,
                source=f.source,
                owner_username=f.owner.username if f.owner else None,
                host_count=len(host_ips),
                # Capped: a finding on 400 hosts should not spend the agent's
                # context proving it. The count above is the answer; the sample
                # is for naming one.
                hosts=sorted(host_ips)[:10],
            )
        )
    return AssistFindingsPage(total=total, severity_counts=counts, findings=findings)


class AssistNote(BaseModel):
    id: int
    body: str
    status: Optional[str] = None
    author: Optional[str] = None
    # Agent-authored notes are stamped as such; a reader deserves to know
    # whether a colleague wrote this or an earlier agent did.
    actor_type: Optional[str] = None
    created_at: Optional[datetime] = None


@router.get(
    "/assist/hosts/{host_id}/notes",
    response_model=List[AssistNote],
    summary="The team's notes on one host",
)
def list_assist_host_notes(
    request: Request,
    host_id: int = Path(..., gt=0),
    limit: int = Query(50, ge=1, le=200),
    agent: Agent = Depends(require_assist_scope),
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

    rows = (
        db.query(models.Annotation, User.username)
        .outerjoin(User, models.Annotation.user_id == User.id)
        .filter(models.Annotation.host_id == host_id)
        .order_by(models.Annotation.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        AssistNote(
            id=a.id,
            body=a.body,
            status=a.status.value if hasattr(a.status, "value") else a.status,
            author=username,
            actor_type=a.actor_type,
            created_at=a.created_at,
        )
        for a, username in rows
    ]


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
    agent: Agent = Depends(require_assist_scope),
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
    return AssistVocabulary(
        tags=tags, labels=labels, sites=sites, scopes=scopes, usernames=usernames,
        finding_statuses=["open", "triaged", "confirmed", "remediated", "closed", "false_positive"],
        severities=["critical", "high", "medium", "low", "info"],
    )


@router.get(
    "/assist/coverage",
    summary="How much of this project has actually been assessed",
)
def assist_coverage(
    request: Request,
    agent: Agent = Depends(require_assist_scope),
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


def _sev_name(value) -> str:
    """Severity as a lowercase string, whatever the column handed back.

    The column is an enum, but a raw string arrives from some ingest paths and
    from SQLite in tests — comparing the two shapes directly is how a rollup
    silently counts zero criticals on a project that has plenty.
    """
    raw = value.value if hasattr(value, "value") else value
    return str(raw or "").lower()


class AssistTestResult(BaseModel):
    """One thing that was actually run, and what it showed."""
    status: str
    command_run: Optional[str] = None
    findings_summary: Optional[str] = None
    severity: Optional[str] = None
    is_finding: bool = False
    executed_at: Optional[datetime] = None


class AssistHostTesting(BaseModel):
    """Whether this host was tested, by whom, and with what result."""
    entry_id: int
    plan_id: int
    plan_title: Optional[str] = None
    plan_status: Optional[str] = None
    priority: Optional[str] = None
    test_phase: Optional[str] = None
    status: str
    rationale: Optional[str] = None
    findings: Optional[str] = None
    proposed_tests: List[dict] = []
    results: List[AssistTestResult] = []


@router.get(
    "/assist/hosts/{host_id}/testing",
    response_model=List[AssistHostTesting],
    summary="What has been planned or run against this host",
)
def list_assist_host_testing(
    request: Request,
    host_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_assist_scope),
    db: Session = Depends(get_db),
):
    """"Has anyone tested this, and what happened?"

    v2.293.0.  Assist could see what scanners reported and nothing about what
    the team actually did — so it could not tell a finding nobody has looked at
    from one a tester confirmed by hand, and every answer implicitly claimed the
    former. That distinction is most of what an analyst wants from a colleague.

    Mirrors the human host page: only entries from plans a human approved
    (`approved` / `in_progress` / `completed`), and never `rejected` entries —
    a reviewer flipping an entry to rejected is an explicit "do not test this",
    and an agent reporting it as outstanding work would be re-litigating a
    decision that has already been made.
    """
    session = _load_assist_session(db, request)
    from app.db.models_agent import (
        TestExecutionResult,
        TestPlan,
        TestPlanEntry,
    )

    host = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == session.project_id)
        .first()
    )
    if host is None:
        raise HTTPException(status_code=404, detail="Host not found in this project")

    entries = (
        db.query(TestPlanEntry, TestPlan)
        .join(TestPlan, TestPlanEntry.test_plan_id == TestPlan.id)
        .filter(
            TestPlanEntry.host_id == host_id,
            TestPlan.project_id == session.project_id,
            TestPlan.status.in_(("approved", "in_progress", "completed")),
            TestPlanEntry.status != "rejected",
        )
        .order_by(TestPlanEntry.id.desc())
        .all()
    )
    if not entries:
        return []

    entry_ids = [e.id for e, _ in entries]
    results_by_entry: dict = {}
    for r in (
        db.query(TestExecutionResult)
        .filter(TestExecutionResult.entry_id.in_(entry_ids))
        .order_by(TestExecutionResult.test_index)
        .all()
    ):
        results_by_entry.setdefault(r.entry_id, []).append(
            AssistTestResult(
                status=r.status.value if hasattr(r.status, "value") else str(r.status),
                command_run=r.command_run,
                findings_summary=r.findings_summary,
                severity=r.severity,
                is_finding=bool(r.is_finding),
                executed_at=r.executed_at,
            )
        )

    def _value(v):
        return v.value if hasattr(v, "value") else v

    return [
        AssistHostTesting(
            entry_id=e.id,
            plan_id=plan.id,
            plan_title=plan.title,
            plan_status=_value(plan.status),
            priority=_value(e.priority),
            test_phase=_value(e.test_phase),
            status=_value(e.status),
            rationale=e.rationale,
            findings=e.findings,
            # Normalised: entries carry either strings (legacy) or objects, and
            # an agent should not have to branch on which era wrote the row.
            proposed_tests=[
                t if isinstance(t, dict) else {"description": str(t)}
                for t in (e.proposed_tests or [])
            ],
            results=results_by_entry.get(e.id, []),
        )
        for e, plan in entries
    ]


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
    agent: Agent = Depends(require_assist_scope),
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


class AssistRecentNote(BaseModel):
    id: int
    host_id: Optional[int] = None
    host_ip: Optional[str] = None
    body: str
    status: Optional[str] = None
    author: Optional[str] = None
    actor_type: Optional[str] = None
    created_at: Optional[datetime] = None


@router.get(
    "/assist/notes",
    response_model=List[AssistRecentNote],
    summary="Recent notes across the project — what the team has been doing",
)
def list_assist_recent_notes(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None, description="open / in_progress / resolved."),
    author: Optional[str] = Query(None, description="Username, or 'me'."),
    agent: Agent = Depends(require_assist_scope),
    db: Session = Depends(get_db),
):
    """"What has the team been working on?" — newest first.

    v2.293.0.  Per-host notes answered "what do we know about THIS host"; this
    answers the question an analyst asks when they pick the engagement back up,
    which is about the work rather than about one asset. Open notes are the
    outstanding-work list the project actually keeps.
    """
    session = _load_assist_session(db, request)
    q = (
        db.query(models.Annotation, User.username, models.Host.ip_address)
        .outerjoin(User, models.Annotation.user_id == User.id)
        .outerjoin(models.Host, models.Annotation.host_id == models.Host.id)
        .filter(models.Annotation.project_id == session.project_id)
    )
    if status:
        q = q.filter(models.Annotation.status == status)
    if author:
        if author.lower() == "me":
            q = q.filter(models.Annotation.user_id == session.started_by_id)
        else:
            row = db.query(User.id).filter(func.lower(User.username) == author.lower()).first()
            if row is None:
                raise HTTPException(status_code=400, detail=f"No user named {author!r}")
            q = q.filter(models.Annotation.user_id == row[0])

    rows = q.order_by(models.Annotation.created_at.desc()).limit(limit).all()
    return [
        AssistRecentNote(
            id=a.id,
            host_id=a.host_id,
            host_ip=ip,
            body=a.body,
            status=a.status.value if hasattr(a.status, "value") else a.status,
            author=username,
            actor_type=a.actor_type,
            created_at=a.created_at,
        )
        for a, username, ip in rows
    ]


@router.get(
    "/assist/scopes",
    response_model=List[ScopeBrief],
    summary="List project scopes with their subnet CIDRs",
)
def list_assist_scopes(
    request: Request,
    agent: Agent = Depends(require_assist_scope),
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
    return [
        ScopeBrief(
            id=s.id,
            name=s.name,
            description=s.description,
            subnets=cidrs_by_scope.get(s.id, []),
            subnet_total=total_by_scope.get(s.id, 0),
            subnets_truncated=total_by_scope.get(s.id, 0) > _SUBNET_CAP,
        )
        for s in scopes
    ]


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
    agent: Agent = Depends(require_assist_scope),
    db: Session = Depends(get_db),
):
    session = _load_assist_session(db, request)
    scans = (
        db.query(models.Scan)
        .filter(models.Scan.project_id == session.project_id)
        .order_by(models.Scan.created_at.desc())
        .limit(limit)
        .all()
    )
    return [ScanBrief.model_validate(s) for s in scans]


# ---------------------------------------------------------------------------
# Self — own session info
# ---------------------------------------------------------------------------

@router.get(
    "/assist/session",
    summary="Get the current assist session's metadata",
)
def get_assist_session_self(
    request: Request,
    agent: Agent = Depends(require_assist_scope),
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
        "environment_probed": session.environment_probed_at is not None,
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
    agent: Agent = Depends(require_assist_scope),
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
        "remediation_flow": p["remediation_flow"],
        "priorities": p["priorities"],
        "decisions": p["decisions"],
        "disposition": p["disposition"],
        "sites": p["sites"],
    }


@router.get(
    "/assist/patterns",
    summary="Cross-sectional analysis — what this estate has a problem with",
)
def get_assist_patterns(
    request: Request,
    agent: Agent = Depends(require_assist_scope),
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
    unresolved_parse_errors: int = 0
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
    agent: Agent = Depends(require_assist_scope),
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

    orphan_query = (
        db.query(models.ParseError)
        .filter(
            models.ParseError.project_id == pid,
            models.ParseError.status == "unresolved",
        )
    )
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
        has_issues=bool(issues) or queued > 0 or processing > 0,
        # total_issues is the unpaginated count; `issues` is capped, so a
        # truncated list must not read as the complete set.
        total_issues=len(issues),
        issues=issues[:limit],
    )


# ---------------------------------------------------------------------------
# Finding detail — the evidence behind one finding (v2.294.0)
# ---------------------------------------------------------------------------

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


class AssistFindingNote(BaseModel):
    id: int
    body: str
    status: Optional[str] = None
    note_type: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[datetime] = None
    attachments: List[AssistAttachment] = []


class AssistFindingHost(BaseModel):
    host_id: int
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
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
    host_count: int = 0
    hosts: List[AssistFindingHost] = []
    hosts_truncated: bool = False
    # The note that justified promotion (note-sourced findings only).
    evidence_note: Optional[AssistFindingNote] = None
    # The finding's own discussion thread.
    comments: List[AssistFindingNote] = []
    # Provenance for scanner- and execution-sourced findings.
    scanner_evidence: List[dict] = []
    execution_evidence: Optional[dict] = None


_FINDING_HOST_CAP = 100


def _serialize_finding_note(note, attachments_by_note) -> "AssistFindingNote":
    return AssistFindingNote(
        id=note.id,
        body=note.body,
        status=note.status.value if hasattr(note.status, "value") else note.status,
        note_type=note.note_type,
        author=note.author.username if note.author else None,
        created_at=note.created_at,
        attachments=[
            AssistAttachment(
                id=a.id,
                filename=a.filename,
                content_type=a.content_type,
                size_bytes=a.size_bytes,
                download_path=f"/api/v1/agent/assist/attachments/{a.id}",
                uploaded_by=a.uploaded_by.username if a.uploaded_by else None,
                created_at=a.created_at,
            )
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
    agent: Agent = Depends(require_assist_scope),
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

    ``scanner_evidence`` / ``execution_evidence`` carry the other two
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
    host_rows = (
        db.query(FindingHost, models.Host)
        .join(models.Host, FindingHost.host_id == models.Host.id)
        .filter(FindingHost.finding_id == finding.id)
        .order_by(models.Host.ip_address)
        .all()
    )
    hosts = [
        AssistFindingHost(
            host_id=h.id,
            ip_address=h.ip_address,
            hostname=h.hostname,
            host_status=fh.host_status,
        )
        for fh, h in host_rows[:_FINDING_HOST_CAP]
    ]

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
    if finding.evidence_annotation_id:
        evidence_note = (
            db.query(models.Annotation)
            .options(joinedload(models.Annotation.author))
            .filter(models.Annotation.id == finding.evidence_annotation_id)
            .first()
        )

    note_ids = [n.id for n in comment_notes]
    if evidence_note is not None:
        note_ids.append(evidence_note.id)
    attachments_by_note: dict = {}
    if note_ids:
        for att in (
            db.query(models.NoteAttachment)
            .options(joinedload(models.NoteAttachment.uploaded_by))
            .filter(models.NoteAttachment.annotation_id.in_(note_ids))
            .order_by(models.NoteAttachment.created_at)
            .all()
        ):
            attachments_by_note.setdefault(att.annotation_id, []).append(att)

    # --- scanner / execution provenance ----------------------------------
    scanner_evidence: List[dict] = []
    vuln_ids = [
        row[0] for row in
        db.query(FindingVulnerability.vuln_id)
        .filter(FindingVulnerability.finding_id == finding.id)
        .all()
    ]
    if finding.vuln_id and finding.vuln_id not in vuln_ids:
        vuln_ids.append(finding.vuln_id)
    if vuln_ids:
        for v in (
            db.query(Vulnerability)
            .filter(Vulnerability.id.in_(vuln_ids))
            .all()
        ):
            scanner_evidence.append({
                "vuln_id": v.id,
                "source": v.source.value if hasattr(v.source, "value") else v.source,
                "plugin_id": v.plugin_id,
                "title": v.title,
                "severity": v.severity.value if hasattr(v.severity, "value") else v.severity,
                "cve_id": v.cve_id,
            })

    execution_evidence = None
    if finding.exec_result_id:
        from app.db.models_agent import TestExecutionResult

        res = (
            db.query(TestExecutionResult)
            .filter(TestExecutionResult.id == finding.exec_result_id)
            .first()
        )
        if res is not None:
            execution_evidence = {
                "result_id": res.id,
                "command_run": res.command_run,
                "findings_summary": res.findings_summary,
                "severity": res.severity,
                # Output is capped at the source but can still be large; a
                # write-up quotes a line or two, so send the head and say so.
                "output_excerpt": (res.raw_output or "")[:2000],
                "output_truncated": len(res.raw_output or "") > 2000,
            }

    return AssistFindingDetail(
        id=finding.id,
        title=finding.title,
        severity=finding.severity,
        status=finding.status,
        source=finding.source,
        owner_username=finding.owner.username if finding.owner else None,
        created_by_username=finding.created_by.username if finding.created_by else None,
        created_at=finding.created_at,
        host_count=len(host_rows),
        hosts=hosts,
        hosts_truncated=len(host_rows) > _FINDING_HOST_CAP,
        evidence_note=(
            _serialize_finding_note(evidence_note, attachments_by_note)
            if evidence_note is not None else None
        ),
        comments=[_serialize_finding_note(n, attachments_by_note) for n in comment_notes],
        scanner_evidence=scanner_evidence,
        execution_evidence=execution_evidence,
    )


@router.get(
    "/assist/attachments/{attachment_id}",
    summary="Download a note image attachment (project-scoped)",
)
def download_assist_attachment(
    request: Request,
    attachment_id: int = Path(..., ge=1),
    agent: Agent = Depends(require_assist_scope),
    db: Session = Depends(get_db),
):
    """Serve the bytes behind an attachment reference.

    v2.294.0.  Deliberately NOT an MCP tool — it returns an image, and the
    agent's job with it is to save it next to the report, not to read it into
    context.  ``assist_get_finding`` hands out the paths; this is what those
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
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Attachment file missing on disk")
    return FileResponse(path=str(target), media_type=att.content_type, filename=att.filename)


@router.get(
    "/assist/web-interfaces/{interface_id}/screenshot",
    summary="Download an EyeWitness screenshot of a web interface",
)
def download_assist_web_screenshot(
    request: Request,
    interface_id: int = Path(..., ge=1),
    agent: Agent = Depends(require_assist_scope),
    db: Session = Depends(get_db),
):
    """The second screenshot store, given the same treatment as note
    attachments.

    v2.297.0.  Note attachments are evidence an analyst chose to record;
    EyeWitness screenshots are captured automatically at ingest and live in a
    separate store (``web_interfaces.screenshot_path``). A write-up showing
    what an exposed admin panel actually looks like usually wants this one.

    Also not an MCP tool, for the same reason: it returns a PNG, and the
    agent's job is to save it beside the report. ``assist_get_host`` hands out
    the paths.

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
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Screenshot file missing on disk")
    return FileResponse(
        path=str(target),
        media_type="image/png",
        filename=f"web-interface-{row.id}.png",
    )
