"""
Agent API — data-read, notes & follow endpoints.

Read-only project/host/scan/scope browsing plus host notes and follow
status.  Split out of agent_api.py.

v2.65.0 — the GET endpoints here serve unscoped global agent keys
and recon-scoped keys; both predate the four-workflow split.  The
read surface has since been duplicated for assist sessions
(/agent/assist/*) and the recon-specific data lives behind
/agent/recon/*.  An unscoped key calling /agent/dashboard /
/agent/hosts / etc. is "legacy" — usually a direct curl from an
operator's terminal, or a CI integration that predates the split.

A debug-level log fires on every unscoped hit so we can see who's
actually using these endpoints before deleting them.  Recon-scoped
calls (scoped_scope_id set) and assist-scoped calls don't fire the
log — they have a defined home elsewhere; this surface is
intentionally a fallback for those, and the deprecation is only
about the truly-unscoped callers.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.db import models
from app.db.models import NoteStatus, FollowStatus
from app.db.models_agent import (
    ActorType,
    Agent,
    AgentCapability,
    AgentSession,
    AssistSession,
    ExecutionSession,
    ReconSession,
)
from app.db.models_auth import User
from app.db.models_project import Project
from app.api.deps import (
    check_agent_rate_limit,
    enforce_capability_row_scope,
    require_capability,
)
from app.db.models_tools import TOOL_APPROVED
from app.services.host_follow_service import HostFollowService
from app.services.tool_registry_service import record_suggestion

from app.api.v1.endpoints.agent_schemas import (
    PortBrief, VulnCounts, HostBrief, HostDetail,
    ScanBrief, ScopeBrief, ProjectInfo, AgentDashboard,
    AgentIdentity, AgentIdentityOperator,
    AgentNoteCreate, AgentNoteResponse, AgentFollowRequest,
    AgentHostUpdate, AgentHostUpdateResponse,
    AgentToolSuggestionRequest, AgentToolSuggestionResponse,
)
from app.api.v1.endpoints.agent_common import (
    _scoped_host_ids_subq, _scoped_scan_ids_subq,
    _apply_agent_host_filters, _batch_host_enrichment,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# v2.295.0 — ``_log_unscoped_legacy_hit`` removed.  It was deprecation
# instrumentation (v2.65.0) that fired only for a key with no scope binding at
# all, to answer "is the unscoped global key still being used?".  That key can
# no longer authenticate, so the probe could never fire again — and a silent
# probe reads as "nothing uses these endpoints", which is the opposite of what
# it would mean.  The browse routes below stay: scoped keys reach them.


def _enrich_host_briefs(db: Session, hosts) -> List[HostBrief]:
    """Convert Host ORM objects to HostBrief with port/vuln enrichment."""
    if not hosts:
        return []
    host_ids = [h.id for h in hosts]
    port_counts, vuln_map, _, _, _ = _batch_host_enrichment(db, host_ids)

    result = []
    for h in hosts:
        vc = vuln_map.get(h.id, {})
        result.append(HostBrief(
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
            ) if vc else None,
        ))
    return result


# ---------------------------------------------------------------------------
# Data-read endpoints
# ---------------------------------------------------------------------------

def _workflow_session_id(db: Session, request: Request, session) -> Optional[int]:
    """The per-workflow session row's id, which is what that workflow's URLs use.

    ``AgentSession`` unified the *binding*; the workflow tables it points at were
    kept (composition, not inheritance), so `/agent/recon/sessions/{id}/…` and
    `/agent/execution-sessions/{id}/…` are keyed by ReconSession / ExecutionSession
    ids — different numbers from the AgentSession id a key resolves to. Returning
    both is the difference between a caller filling that path itself and getting
    a 404 it can't diagnose.
    """
    if session is not None:
        model = {
            "assist": AssistSession,
            "recon": ReconSession,
            "execution": ExecutionSession,
        }.get(session.workflow)
        if model is not None:
            found = (
                db.query(model.id)
                .filter(model.agent_session_id == session.id)
                .scalar()
            )
            if found is not None:
                return found
    # Legacy keys minted before the unified binding carry the id on the key row
    # itself. plan_generation has no per-workflow session at all — None is the
    # honest answer there, not a fallback to something unrelated.
    return getattr(request.state, "scoped_assist_session_id", None) or getattr(
        request.state, "scoped_recon_session_id", None
    )


@router.get("/identity", response_model=AgentIdentity, summary="What this API key is")
def get_agent_identity(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Self-introspection for *any* agent key, whatever workflow it belongs to.

    Deliberately not behind a workflow guard — that is the whole point.  Every
    other introspection route requires the workflow it describes, so a caller
    holding a key of unknown provenance can only classify it by trying surfaces
    until one stops returning 403.  A client that must decide *before its first
    call* which tools to offer (the MCP server, at ``tools/list``) cannot work
    that way, and probing with real calls would write audit noise into whichever
    surface it guessed wrong.

    It discloses nothing the key can't already reach: the bound project and
    session are what every other call is scoped to, and the capability list is
    what the key's own writes would reveal one 403 at a time.
    """
    # No legacy-hit log here: unlike the browse routes below, this one is
    # meant to be called by every workflow, so an unscoped caller is not a
    # deprecation signal.
    workflow_family = getattr(request.state, "key_workflow", None)
    session_id = getattr(request.state, "agent_session_id", None)
    session = (
        db.query(AgentSession).filter(AgentSession.id == session_id).first()
        if session_id is not None
        else None
    )

    operator_id = getattr(request.state, "key_operator_id", None)
    operator = None
    if operator_id is not None:
        operator = AgentIdentityOperator(
            id=operator_id,
            username=db.query(User.username).filter(User.id == operator_id).scalar(),
        )

    return AgentIdentity(
        # Fall back to the coarse family for a legacy key with no session row —
        # it is the most specific thing that is still true.
        workflow=(session.workflow if session is not None else workflow_family),
        workflow_family=workflow_family,
        session_id=session_id,
        workflow_session_id=_workflow_session_id(db, request, session),
        plan_id=getattr(request.state, "key_plan_id", None),
        scope_id=getattr(request.state, "scoped_scope_id", None),
        project_id=agent.project_id,
        project_name=(
            db.query(Project.name).filter(Project.id == agent.project_id).scalar()
        ),
        agent_id=agent.id,
        agent_name=agent.name,
        capabilities=sorted(getattr(request.state, "key_capabilities", None) or []),
        capability_constraint=getattr(request.state, "key_capability_constraint", None),
        operator=operator,
        environment_probed=(
            session is not None and session.environment_probed_at is not None
        ),
        key_expires_at=getattr(request.state, "key_expires_at", None),
    )


@router.post(
    "/tool-suggestions",
    response_model=AgentToolSuggestionResponse,
    status_code=201,
    summary="Suggest a tool BlueStick doesn't approve yet",
)
def suggest_tool(
    body: AgentToolSuggestionRequest,
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Record an agent asking for a tool outside the approved set.

    Open to every workflow and gated by no capability, deliberately: the whole
    value is that an agent which hits the edge of the approved set *says so*
    rather than silently substituting something, and a capability gate would
    mean the sessions most likely to hit that edge are the ones that can't
    report it.  It grants nothing — the row lands as ``suggested``, which no
    approval rule reads, and a human decides.

    Recording it is the point: without this, the only trace of "the approved
    set was missing something" is a test that didn't happen.
    """
    entry = record_suggestion(
        db,
        name=body.name.strip(),
        rationale=body.rationale.strip(),
        agent_id=agent.id,
        project_id=agent.project_id,
        description=body.description,
        category=body.category,
    )
    already_approved = entry.status == TOOL_APPROVED
    return AgentToolSuggestionResponse(
        name=entry.name,
        status=entry.status,
        already_approved=already_approved,
        message=(
            f"{entry.name} is already approved — you may use it."
            if already_approved
            else (
                f"Recorded. {entry.name} is NOT approved yet: do not run it. "
                "A human reviews suggestions; use an approved tool for now."
            )
        ),
    )


@router.get("/project", response_model=ProjectInfo, summary="Get project metadata")
def get_project_info(
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == agent.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectInfo(
        id=project.id,
        name=project.name,
        slug=project.slug,
        description=project.description,
        status=project.status,
        start_date=project.start_date,
        end_date=project.end_date,
        agent_name=agent.name,
    )


@router.get("/dashboard", response_model=AgentDashboard, summary="Project stats summary")
def get_dashboard(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Project stats summary, scoped to the key's binding.

    Recon-scoped keys see only hosts/ports in their scope (via
    HostSubnetMapping) and scans produced by any ReconSession under the
    scope.  Unscoped keys see the full project.  Prevents cross-scope
    leakage and matches what /agent/recon/summary reports for the same
    recon session.
    """
    pid = agent.project_id
    scoped_scope = getattr(request.state, "scoped_scope_id", None)

    host_q = db.query(models.Host).filter(models.Host.project_id == pid)
    port_q = (
        db.query(models.Port)
        .join(models.Host, models.Port.host_id == models.Host.id)
        .filter(models.Host.project_id == pid, models.Port.state == "open")
    )
    scan_q = db.query(models.Scan).filter(models.Scan.project_id == pid)
    last_scan_q = (
        db.query(models.Scan.created_at)
        .filter(models.Scan.project_id == pid)
        .order_by(models.Scan.created_at.desc())
    )

    if scoped_scope is not None:
        host_subq = _scoped_host_ids_subq(db, scoped_scope)
        scan_subq = _scoped_scan_ids_subq(db, scoped_scope)
        host_q = host_q.filter(models.Host.id.in_(host_subq))
        port_q = port_q.filter(models.Host.id.in_(host_subq))
        scan_q = scan_q.filter(models.Scan.id.in_(scan_subq))
        last_scan_q = last_scan_q.filter(models.Scan.id.in_(scan_subq))

    host_count = host_q.count()
    up_host_count = host_q.filter(models.Host.state == "up").count()
    open_port_count = port_q.count()
    scan_count = scan_q.count()
    last_scan = last_scan_q.first()

    return AgentDashboard(
        host_count=host_count,
        up_host_count=up_host_count,
        open_port_count=open_port_count,
        scan_count=scan_count,
        last_scan_at=last_scan[0] if last_scan else None,
    )


@router.get("/hosts", response_model=List[HostBrief], summary="List hosts")
def list_hosts(
    request: Request,
    state: Optional[str] = Query(None),
    ports: Optional[str] = Query(None, description="Comma-separated port numbers"),
    services: Optional[str] = Query(None, description="Comma-separated service names"),
    subnets: Optional[str] = Query(None, description="Comma-separated CIDR blocks"),
    has_critical_vulns: Optional[bool] = Query(None),
    has_high_vulns: Optional[bool] = Query(None),
    has_exploit_available: Optional[bool] = Query(
        None,
        description=(
            "Filter to hosts with at least one vulnerability whose "
            "Vulnerability.exploitable is True — set by the Nessus parser "
            "when exploit_code_maturity ∈ {functional, high, "
            "proof-of-concept} or metasploit/core-impact/canvas modules "
            "are present.  v2.85.0; pre-v2.83.2 the column was never "
            "persisted so this filter would have matched nothing."
        ),
    ),
    search: Optional[str] = Query(None, description="Search IP, hostname, or OS"),
    not_in_plan_id: Optional[int] = Query(None, description="Exclude hosts already in this plan"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    q = db.query(models.Host).filter(models.Host.project_id == agent.project_id)
    # Recon-scoped keys only see hosts correlated into their scope via
    # HostSubnetMapping.  Pre-v2.13.0 this endpoint returned project-wide
    # hosts to any caller, which misled recon agents into thinking their
    # ingests had failed (empty list early) or into reading hosts from
    # other scopes (after their own ingest landed).
    scoped_scope = getattr(request.state, "scoped_scope_id", None)
    if scoped_scope is not None:
        q = q.filter(models.Host.id.in_(_scoped_host_ids_subq(db, scoped_scope)))
    q = _apply_agent_host_filters(
        q, db, project_id=agent.project_id,
        state=state, ports=ports, services=services, subnets=subnets,
        has_critical_vulns=has_critical_vulns, has_high_vulns=has_high_vulns,
        has_exploit_available=has_exploit_available,
        search=search, not_in_plan_id=not_in_plan_id,
    )
    hosts = q.order_by(models.Host.ip_address).offset(offset).limit(limit).all()
    return _enrich_host_briefs(db, hosts)


@router.get("/hosts/{host_id}", response_model=HostDetail, summary="Host detail with ports")
def get_host(
    request: Request,
    host_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    q = (
        db.query(models.Host)
        .options(joinedload(models.Host.ports))
        .filter(models.Host.id == host_id, models.Host.project_id == agent.project_id)
    )
    scoped_scope = getattr(request.state, "scoped_scope_id", None)
    if scoped_scope is not None:
        q = q.filter(models.Host.id.in_(_scoped_host_ids_subq(db, scoped_scope)))
    host = q.first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    port_briefs = [PortBrief.model_validate(p) for p in host.ports]
    open_count = sum(1 for p in host.ports if p.state == "open")
    # Compute vuln summary for consistency with list endpoint
    port_counts, vuln_map, _, _, _ = _batch_host_enrichment(db, [host.id])
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
        ) if vc else None,
        ports=port_briefs,
    )


@router.get("/scans", response_model=List[ScanBrief], summary="List scans")
def list_scans(
    request: Request,
    tool: Optional[str] = Query(
        None,
        description=(
            "Case-insensitive substring match against Scan.tool_name "
            "(e.g. ``nessus``, ``nmap``, ``masscan``).  Mirrors the "
            "user-side /scans filter added v2.82.0 / v2.83.0."
        ),
    ),
    created_after: Optional[str] = Query(
        None,
        description=(
            "ISO-8601 timestamp; only scans uploaded after this point "
            "are returned.  v2.85.0 — drives 'recent uploads' queries "
            "without paging the full history."
        ),
    ),
    sort_by: Optional[str] = Query(
        None,
        pattern="^(created_at|filename|tool_name)$",
        description=(
            "Sort column.  Allowed: ``created_at`` (default), "
            "``filename``, ``tool_name``.  v2.85.0."
        ),
    ),
    sort_order: Optional[str] = Query(
        "desc",
        pattern="^(asc|desc)$",
        description="Sort direction — asc or desc.  Defaults to desc.",
    ),
    limit: int = Query(100, ge=1, le=500),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    q = db.query(models.Scan).filter(models.Scan.project_id == agent.project_id)
    # Recon-scoped keys only see scans that came from IngestionJobs under
    # their scope's ReconSessions.  Matches the host-list scoping and
    # prevents cross-scope scan enumeration.
    scoped_scope = getattr(request.state, "scoped_scope_id", None)
    if scoped_scope is not None:
        q = q.filter(models.Scan.id.in_(_scoped_scan_ids_subq(db, scoped_scope)))
    # v2.85.0 — same filter surface as the user-side /scans endpoint, so
    # an agent that already understands the page can replicate its
    # narrowing without an extra query/round-trip.  ``created_after``
    # accepts any ISO-8601 string SQLAlchemy can compare to a TZ-aware
    # column; malformed input returns no rows rather than 400 so the
    # agent can ratchet the filter without first probing format.
    if tool:
        from app.services.host_query_common import escape_like
        q = q.filter(models.Scan.tool_name.ilike(f"%{escape_like(tool)}%", escape='\\'))
    if created_after:
        from datetime import datetime
        try:
            cutoff = datetime.fromisoformat(created_after.replace("Z", "+00:00"))
            q = q.filter(models.Scan.created_at >= cutoff)
        except (ValueError, TypeError):
            # Pin to no-results rather than 400 — keep the contract
            # symmetric with the rest of the agent surface (which favors
            # quiet empty responses over surfacing validation errors).
            return []
    _SORT_COLUMNS = {
        "created_at": models.Scan.created_at,
        "filename": models.Scan.filename,
        "tool_name": models.Scan.tool_name,
    }
    sort_column = _SORT_COLUMNS.get(sort_by or "created_at", models.Scan.created_at)
    if (sort_order or "desc").lower() == "desc":
        sort_column = sort_column.desc()
    scans = q.order_by(sort_column).limit(limit).all()
    return [ScanBrief.model_validate(s) for s in scans]


@router.get("/scopes", response_model=List[ScopeBrief], summary="List scopes")
def list_scopes(
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    scopes = (
        db.query(models.Scope)
        .options(joinedload(models.Scope.subnets))
        .filter(models.Scope.project_id == agent.project_id)
        .all()
    )
    return [
        ScopeBrief(
            id=s.id,
            name=s.name,
            description=s.description,
            subnets=[sub.cidr for sub in s.subnets],
        )
        for s in scopes
    ]


# ---------------------------------------------------------------------------
# Host notes & follow (agent-facing)
# ---------------------------------------------------------------------------

@router.post(
    "/hosts/{host_id}/notes",
    response_model=AgentNoteResponse,
    status_code=201,
    summary="Create a note on a host",
)
def create_agent_note(
    body: AgentNoteCreate,
    request: Request,
    host_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_capability(AgentCapability.WRITE_NOTES.value)),
    db: Session = Depends(get_db),
):
    """Create a note on a host.

    Requires the ``write:notes`` capability (v2.231.0).  Plan, execution,
    recon, and legacy unscoped keys carry it implicitly; an assist session
    only carries it when the operator granted write access at start time,
    and then only for hosts assigned to them.
    """
    q = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == agent.project_id)
    )
    scoped_scope = getattr(request.state, "scoped_scope_id", None)
    if scoped_scope is not None:
        q = q.filter(models.Host.id.in_(_scoped_host_ids_subq(db, scoped_scope)))
    host = q.first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    enforce_capability_row_scope(request, db, host_id=host_id)

    try:
        note_status = NoteStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid note status: {body.status}")

    svc = HostFollowService(db)
    note = svc.create_note(
        host_id,
        agent.owner_id,
        body.body,
        note_status,
        actor_type=ActorType.AGENT.value,
        agent_session_id=getattr(request.state, "agent_session_id", None),
    )

    return AgentNoteResponse(
        id=note.id,
        host_id=host_id,
        body=note.body,
        status=note.status,
        author_id=note.user_id,
        parent_id=note.parent_id,
        actor_type=note.actor_type,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@router.get(
    "/hosts/{host_id}/notes",
    response_model=List[AgentNoteResponse],
    summary="List notes for a host",
)
def list_agent_notes(
    request: Request,
    host_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    q = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == agent.project_id)
    )
    scoped_scope = getattr(request.state, "scoped_scope_id", None)
    if scoped_scope is not None:
        q = q.filter(models.Host.id.in_(_scoped_host_ids_subq(db, scoped_scope)))
    host = q.first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    svc = HostFollowService(db)
    notes = svc.list_notes(host_id)
    return [
        AgentNoteResponse(
            id=n.id,
            host_id=host_id,
            body=n.body,
            status=n.status,
            author_id=n.user_id,
            parent_id=n.parent_id,
            created_at=n.created_at,
            updated_at=n.updated_at,
        )
        for n in notes
    ]


@router.post(
    "/hosts/{host_id}/follow",
    status_code=204,
    summary="Set review status on a host",
)
def set_agent_follow(
    body: AgentFollowRequest,
    request: Request,
    host_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_capability(AgentCapability.WRITE_FOLLOW.value)),
    db: Session = Depends(get_db),
):
    """Set review status on a host.

    Requires the ``write:follow`` capability (v2.231.0) — see
    ``create_agent_note`` for how that resolves per workflow.
    """
    q = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == agent.project_id)
    )
    scoped_scope = getattr(request.state, "scoped_scope_id", None)
    if scoped_scope is not None:
        q = q.filter(models.Host.id.in_(_scoped_host_ids_subq(db, scoped_scope)))
    host = q.first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    enforce_capability_row_scope(request, db, host_id=host_id)

    try:
        follow_status = FollowStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid follow status: {body.status}")

    svc = HostFollowService(db)
    svc.set_follow_status(host_id, agent.owner_id, follow_status)


@router.patch(
    "/hosts/{host_id}",
    response_model=AgentHostUpdateResponse,
    summary="Correct operator-curated host attributes (hostname / OS)",
)
def update_agent_host(
    body: AgentHostUpdate,
    request: Request,
    host_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_capability(AgentCapability.WRITE_HOST.value)),
    db: Session = Depends(get_db),
):
    """Update ``hostname`` and/or ``os_name`` on a host after investigation.

    Requires the ``write:host`` capability. Plan / execution / recon / legacy
    keys carry it implicitly; an assist session only carries it when the
    operator granted write access at start time, and then only for hosts
    assigned to them. Deliberately narrow — only these two operator-correctable
    attributes are editable; scan-derived facts are never mutated here. The
    change is captured by the agent API audit middleware (touched host id), so
    who-changed-what stays reconstructable.
    """
    q = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == agent.project_id)
    )
    scoped_scope = getattr(request.state, "scoped_scope_id", None)
    if scoped_scope is not None:
        q = q.filter(models.Host.id.in_(_scoped_host_ids_subq(db, scoped_scope)))
    host = q.first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    enforce_capability_row_scope(request, db, host_id=host_id)

    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No editable fields supplied.")
    changed: List[str] = []
    for field in ("hostname", "os_name"):
        if field in fields:
            new_value = (fields[field] or "").strip() or None
            if getattr(host, field) != new_value:
                setattr(host, field, new_value)
                changed.append(field)
    if changed:
        db.commit()
        db.refresh(host)

    return AgentHostUpdateResponse(
        id=host.id,
        ip_address=host.ip_address,
        hostname=host.hostname,
        os_name=host.os_name,
        changed=changed,
    )
