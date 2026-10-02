"""
Agent API — data-read, notes & follow endpoints.

Read-only project/host/scan/scope browsing plus host notes and follow
status.  Split out of agent_api.py.

Every read is bounded by the key's project.  Keys were once bound to a scope
as well (recon runs, removed in v2.433.0); nothing narrows a read to a scope
now, and a scope's own hosts are read through /agent/scopes/{scope_id}/….
The read surface is duplicated for assist sessions (/agent/assist/*), which
is where agents mostly read.
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.db import models
from app.db.models import FollowStatus
from app.db.models_agent import (
    ActorType,
    Agent,
    AgentSession,
)
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.api.deps import (
    AGENT_SESSION_RENEW_PATH,
    authenticate_for_renewal,
    check_agent_rate_limit,
    session_renewal_deadline,
)
from app.core.security import check_permissions
from app.db.models_tools import TOOL_REFERENCE
from app.services import dns_name_service
from app.services.host_follow_service import HostFollowService
from app.services.host_serialization import exploit_count_maps
from app.services.tool_registry_service import record_suggestion
from app.services.agent_session_service import (
    close_agent_session_from_agent, note_agent_model,
)

from app.api.v1.endpoints.agent_schemas import (
    PortBrief, VulnCounts, HostBrief, HostDetail,
    ScanBrief, ScopeBrief, ProjectInfo, AgentDashboard,
    AgentIdentity, AgentIdentityOperator,
    AgentNoteCreate, AgentNoteResponse, AgentFollowRequest,
    AgentHostUpdate, AgentHostUpdateResponse,
    AgentToolSuggestionRequest, AgentToolSuggestionResponse,
)
from app.api.v1.endpoints.agent_common import (
    PORTS_PARAM_HELP, SERVICES_PARAM_HELP,
    apply_agent_host_filters, batch_host_enrichment, load_agent_session,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# v2.305.0 — key renewal is mounted on its own router, deliberately OUTSIDE the
# operator-access gate applied to every other agent route. That gate runs the
# normal authentication chain, which rejects an expired key — which would defeat
# the one endpoint whose entire purpose is accepting one.
#
# Safe because renewal grants no authority: it extends a deadline and nothing
# else. The renewed key still passes through the operator gate on every real
# request, so an operator who lost project membership can renew a key that can
# then do nothing with it.
renewal_router = APIRouter()


# v2.295.0 — ``_log_unscoped_legacy_hit`` removed.  It was deprecation
# instrumentation (v2.65.0) that fired only for a key with no scope binding at
# all, to answer "is the unscoped global key still being used?".  That key can
# no longer authenticate, so the probe could never fire again — and a silent
# probe reads as "nothing uses these endpoints", which is the opposite of what
# it would mean.  The browse routes below stay: every session key reaches them.


def _enrich_host_briefs(db: Session, hosts) -> List[HostBrief]:
    """Convert Host ORM objects to HostBrief with port/vuln enrichment."""
    if not hosts:
        return []
    host_ids = [h.id for h in hosts]
    port_counts, vuln_map = batch_host_enrichment(db, host_ids)
    exploits, critical_exploits = exploit_count_maps(db, host_ids)

    result = []
    for h in hosts:
        vc = vuln_map.get(h.id, {})
        result.append(HostBrief(
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
            ) if vc else None,
        ))
    return result


# ---------------------------------------------------------------------------
# Data-read endpoints
# ---------------------------------------------------------------------------

class SessionRenewResponse(BaseModel):
    """The new deadline on the SAME token — nothing to re-bootstrap."""
    expires_at: datetime
    #: When renewal stops being possible: the session's start plus its maximum
    #: lifetime. Past this, expiry is terminal.
    renewable_until: datetime
    session_id: int
    message: str = (
        "Key extended. Retry the request you were making — do not re-run work "
        "whose output you already hold."
    )


class SessionEndRequest(BaseModel):
    notes: Optional[str] = Field(
        None, max_length=2000,
        description="One or two lines on what the session did; lands on the session record.",
    )
    agent_model: Optional[str] = Field(
        None, max_length=100,
        description="The model you are running as (e.g. claude-opus-5-5). Optional; labels the session.",
    )


class SessionEndResponse(BaseModel):
    session_id: int
    status: str
    ended_at: datetime
    message: str = (
        "Session ended and your key is revoked. Nothing further will authenticate; "
        "tell the operator you are done."
    )


@router.post(
    "/session/end",
    response_model=SessionEndResponse,
    summary="End this session — the LAST call you make (revokes your key)",
)
def end_own_session(
    # A default instance rather than ``Optional[...] = None``: the MCP contract
    # test reads the body's properties off OpenAPI, and an Optional body
    # renders as ``anyOf [ref, null]`` where it finds none.
    body: SessionEndRequest = SessionEndRequest(),
    request: Request = None,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The agent's own exit (v2.340.0).

    The session an agent holds the key for stayed ``active`` after the agent
    finished, because nothing on the agent surface could end it: recon and
    execution phases have a ``/complete``, the session itself had none, and
    the operator's timeline showed every finished session as still running
    until the hourly sweep lapsed it — after the key had expired *and* the
    session had passed its lifetime cap, a week by default.

    Refuses with 409 while an execution run is still open, naming the ids:
    complete it first, then end the session.  Feedback goes before this call,
    not after — the key is revoked on the way out.
    """
    session = load_agent_session(db, request)
    note_agent_model(session, body.agent_model)
    close_agent_session_from_agent(db, session, notes=body.notes)
    db.commit()
    db.refresh(session)
    logger.info("agent session %s ended by its agent (%s)", session.id, agent.name)
    return SessionEndResponse(
        session_id=session.id, status=session.status, ended_at=session.completed_at,
    )


@renewal_router.post(
    "/session/renew",
    response_model=SessionRenewResponse,
    summary="Extend this key's deadline (accepts an already-expired key)",
)
def renew_session_key(
    api_key_obj=Depends(authenticate_for_renewal),
    db: Session = Depends(get_db),
):
    """Push this key's expiry out, keeping the same secret.

    v2.304.0.  **Renewal, not rotation.** The token is unchanged, so an agent
    part-way through a job does not have to be re-bootstrapped — which is the
    entire point, because the caller is typically holding scan output it cannot
    reproduce cheaply.

    It deliberately **accepts an expired key**. The failure this exists for is
    discovered late: an agent launches nmap / masscan / Nessus, blocks for
    hours, its key lapses while it waits, and it only finds out when it tries to
    upload. Refusing renewal there would discard completed work over a lapsed
    credential. Prevention cannot cover this on its own — a blocked agent issues
    no requests, so no heartbeat can fire, and scan durations are not
    predictable.

    Bounded by the session: renewal works while the session is active and under
    ``AGENT_SESSION_MAX_LIFETIME_HOURS`` from its start. Ending the session
    revokes the key immediately, and that — not expiry — is the control.

    No path parameter: the key identifies its own session, so an agent can
    always call this without knowing any ids.
    """
    from app.services.agent_key_ttl import resolve_expires_at

    session = api_key_obj.agent_session
    deadline = session_renewal_deadline(session)
    new_expiry = resolve_expires_at(None)
    # Never let a renewal outlive the session cap — otherwise the cap would be
    # a formality that any renewal could step past.
    if deadline is not None and new_expiry > deadline:
        new_expiry = deadline
    api_key_obj.expires_at = new_expiry
    db.commit()

    logger.info(
        "agent key %s renewed until %s (session %s, renewable until %s)",
        api_key_obj.key_prefix, new_expiry.isoformat(),
        session.id, deadline.isoformat() if deadline else "n/a",
    )
    return SessionRenewResponse(
        expires_at=new_expiry,
        renewable_until=deadline,
        session_id=session.id,
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
    session are what every other call is scoped to, and the operator is who the
    key acts for — which its own writes would reveal one 403 at a time anyway.
    """
    session_id = getattr(request.state, "agent_session_id", None)
    session = (
        db.query(AgentSession).filter(AgentSession.id == session_id).first()
        if session_id is not None
        else None
    )

    # Same resolution the access gate uses, including the ``Agent.owner_id``
    # fallback — reporting no operator for a key the gate is happily checking
    # against one would make this endpoint disagree with the thing it describes.
    operator_id = getattr(request.state, "key_operator_id", None) or agent.owner_id
    is_global_admin = bool(getattr(request.state, "key_operator_is_admin", False))
    project_role = getattr(request.state, "key_operator_role", None)
    operator = None
    if operator_id is not None:
        operator = AgentIdentityOperator(
            id=operator_id,
            username=db.query(User.username).filter(User.id == operator_id).scalar(),
            project_role=project_role,
            is_global_admin=is_global_admin,
        )
    can_write_project_data = is_global_admin or (
        project_role is not None
        and check_permissions(project_role, ProjectRole.ANALYST.value)
    )

    # v2.337.0 — the phases this session has open, so the MCP layer can fill
    # tool arguments (an execution session's plan_id) and
    # the client can see what is in flight without probing surfaces.

    return AgentIdentity(
        workflow=(session.workflow if session is not None else None),
        session_id=session_id,
        # Each id in its own space; a single active run resolves cleanly,
        # several open return None and the agent names the one it means.
        project_id=agent.project_id,
        project_name=(
            db.query(Project.name).filter(Project.id == agent.project_id).scalar()
        ),
        agent_id=agent.id,
        agent_name=agent.name,
        operator=operator,
        can_write_project_data=can_write_project_data,
        key_expires_at=getattr(request.state, "key_expires_at", None),
        renew_path=AGENT_SESSION_RENEW_PATH,
        renewable_until=session_renewal_deadline(session),
    )


@router.post(
    "/tool-suggestions",
    response_model=AgentToolSuggestionResponse,
    status_code=201,
    summary="Suggest a tool for BlueStick's catalogue",
)
def suggest_tool(
    body: AgentToolSuggestionRequest,
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Record an agent proposing a tool the catalogue doesn't have.

    Catalogue intake, not permission: whether a tool may be run is the
    operator's call in their own client (v2.433.0).  The row lands as
    ``suggested`` and a curator adds it to the catalogue or declines it.
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
    already_catalogued = entry.status == TOOL_REFERENCE
    return AgentToolSuggestionResponse(
        name=entry.name,
        status=entry.status,
        already_catalogued=already_catalogued,
        message=(
            f"{entry.name} is already in the catalogue."
            if already_catalogued
            else f"Recorded {entry.name} as a catalogue suggestion for a curator."
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
    """Project stats summary for the key's project."""
    pid = agent.project_id

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
    ports: Optional[str] = Query(None, description=PORTS_PARAM_HELP),
    services: Optional[str] = Query(None, description=SERVICES_PARAM_HELP),
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
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    q = db.query(models.Host).filter(models.Host.project_id == agent.project_id)
    q = apply_agent_host_filters(
        q, db, project_id=agent.project_id,
        state=state, ports=ports, services=services, subnets=subnets,
        has_critical_vulns=has_critical_vulns, has_high_vulns=has_high_vulns,
        has_exploit_available=has_exploit_available,
        search=search,
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
    host = q.first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    port_briefs = [PortBrief.model_validate(p) for p in host.ports]
    open_count = sum(1 for p in host.ports if p.state == "open")
    # Compute vuln summary for consistency with list endpoint
    port_counts, vuln_map = batch_host_enrichment(db, [host.id])
    vc = vuln_map.get(host.id, {})
    # v2.323.0 — every name observed at this address, most recently seen
    # first; the valid target_fqdn set (shared with /assist/hosts/{id}).
    return HostDetail(
        names=dns_name_service.observed_names_at_address(db, host.project_id, host.ip_address),
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
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Create a note on a host.

    v2.309.0 — gated by the operator's project role, like every other write on
    this surface (``enforce_agent_operator_access``, applied at the router).
    The ``write:notes`` capability it used to require is gone with the rest of
    the capability system: an agent may write a note if the person whose
    session it is may write a note.
    """
    q = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == agent.project_id)
    )
    host = q.first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    svc = HostFollowService(db)
    note = svc.create_note(
        host_id,
        agent.owner_id,
        body.body,
        actor_type=ActorType.AGENT.value,
        agent_session_id=getattr(request.state, "agent_session_id", None),
    )

    return AgentNoteResponse(
        id=note.id,
        host_id=host_id,
        body=note.body,
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
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Set review status on a host.

    v2.309.0 — gated by the operator's project role; see ``create_agent_note``.
    """
    q = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == agent.project_id)
    )
    host = q.first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    svc = HostFollowService(db)

    # "none"/"clear" removes the operator's follow entirely — the inverse the
    # enum otherwise lacks, so an agent that set a status can undo it rather
    # than being stuck at watching/in_review/reviewed forever (v2.315.0).
    if body.status in ("none", "clear"):
        svc.unfollow(host_id, agent.owner_id)
        return

    try:
        follow_status = FollowStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid follow status: {body.status}")

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
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Update ``hostname`` and/or ``os_name`` on a host after investigation.

    v2.309.0 — gated by the operator's project role; see ``create_agent_note``.
    Still deliberately narrow: only these two operator-correctable attributes
    are editable, and scan-derived facts are never mutated here. The change is
    captured by the agent API audit middleware (touched host id), so
    who-changed-what stays reconstructable.
    """
    q = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == agent.project_id)
    )
    host = q.first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

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
                if field == "os_name":
                    # v2.421.0 — the family follows the corrected name; left
                    # alone, a scanner's "Linux" stayed beside "Windows".
                    from app.services.os_family import os_family_from_name
                    host.os_family = os_family_from_name(new_value)
            if field == "hostname":
                # An operator correction is the top-ranked display-name source:
                # no later scan, PTR or forward answer may replace it (see
                # dns_name_service.apply_hostname_candidate).  Clearing the
                # name drops the lock so ingestion can name the host again.
                host.hostname_source = "operator" if new_value else None
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
