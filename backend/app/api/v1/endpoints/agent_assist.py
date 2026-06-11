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

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.db import models
from app.db.models_agent import (
    Agent,
    AssistSession,
    AssistSessionStatus,
    ReconSession,
    ReconSessionStatus,
)
from app.db.models_project import Project
from app.api.deps import require_assist_scope

from app.api.v1.endpoints.agent_schemas import (
    AgentDashboard,
    EnvironmentProbeRequest,
    EnvironmentProbeResponse,
    EnvironmentSummary,
    HostBrief,
    HostDetail,
    PortBrief,
    ProjectInfo,
    ScanBrief,
    ScopeBrief,
    VulnCounts,
)
from app.api.v1.endpoints.agent_common import (
    _apply_agent_host_filters,
    _batch_host_enrichment,
)
from app.services.agent_environment_probe_service import apply_environment_probe

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

    return {
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
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(require_assist_scope),
    db: Session = Depends(get_db),
):
    """Project-scoped host list with the same filter vocabulary as
    /agent/hosts.  Returns HostBrief (id, ip, hostname, state, OS,
    open-port count, vuln summary) — single round-trip surface for
    "which hosts match $criteria?" questions.

    No scope sub-filtering (assist sessions are project-wide), so
    the recon-only ``scoped_host_ids_subq`` path is skipped.  The
    handler still honours ``subnets=`` so an agent can narrow to a
    specific CIDR via the filter rather than relying on a separate
    scope binding.
    """
    session = _load_assist_session(db, request)
    q = db.query(models.Host).filter(models.Host.project_id == session.project_id)
    q = _apply_agent_host_filters(
        q,
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
    hosts = q.order_by(models.Host.ip_address).offset(offset).limit(limit).all()
    if not hosts:
        return []
    host_ids = [h.id for h in hosts]
    port_counts, vuln_map, _, _, _ = _batch_host_enrichment(db, host_ids)
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
            )
        )
    return result


@router.get(
    "/assist/hosts/{host_id}",
    response_model=HostDetail,
    summary="Host detail with full open-port list",
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
    return HostDetail(
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
        ports=port_briefs,
    )


# ---------------------------------------------------------------------------
# Scopes — list
# ---------------------------------------------------------------------------

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
    }
