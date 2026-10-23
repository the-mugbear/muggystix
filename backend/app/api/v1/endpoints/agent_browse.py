"""
Agent API — the session's own routes, host notes, follow and corrections.

Identity, key renewal and session end, tool suggestions, a host note, the
operator's review status and a host correction.  Split out of agent_api.py.
The inventory reads are /agent/assist/* (agent_assist.py); the scope's own
lists are /agent/scope/….

Also here: the recorded request for one configured scanner's credentials
(``POST /agent/scanner-integrations/{integration_id}/credentials``).
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

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
    agent_session_metadata_write,
    authenticate_for_renewal,
    check_agent_rate_limit,
    get_client_info,
    session_renewal_deadline,
)
from app.core.security import check_permissions
from app.db.models_tools import TOOL_REFERENCE
from app.services.host_follow_service import HostFollowService
from app.services.tool_registry_service import record_suggestion
from app.services.agent_session_service import (
    close_agent_session_from_agent, note_agent_model,
)

from app.api.v1.endpoints.agent_schemas import (
    AgentIdentity, AgentIdentityOperator,
    AgentNoteCreate, AgentNoteResponse, AgentFollowRequest, FOLLOW_CLEAR,
    AgentHostUpdate, AgentHostUpdateResponse,
    AgentToolSuggestionRequest, AgentToolSuggestionResponse,
    ScannerCredentialsRequest, ScannerCredentialsResponse,
)
from app.services.agent_policy import SCANNER_ASK_FIRST_REFUSAL, SCANNER_CREDENTIALS_NOTE
from app.services.integration_service import IntegrationService, share_credentials_with_agent
from app.api.v1.endpoints.agent_common import load_agent_session, load_operator

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
    # Ending one's own session is lifecycle bookkeeping: an auditor can start
    # a session and must be able to end it.  Ownership (the key identifies its
    # own session) is in close_agent_session_from_agent.
    dependencies=[Depends(agent_session_metadata_write)],
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
    """The agent's own exit.

    Marks the session ended and revokes its key, so the operator's Agent
    Sessions page stops listing it as live.  A session nobody ends stays
    active until the hourly sweep lapses it — after the key has expired *and*
    the session has passed its lifetime cap, a week by default.

    It never refuses and never loses work: the tests proposed and the
    evidence recorded are project data.  Feedback goes before this call, not
    after — the key is revoked on the way out.
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
    # This router is mounted outside the operator gate; declared so that it
    # would not become a project write if it were ever moved under it.
    dependencies=[Depends(agent_session_metadata_write)],
    response_model=SessionRenewResponse,
    summary="Extend this key's deadline (accepts an already-expired key)",
)
def renew_session_key(
    api_key_obj=Depends(authenticate_for_renewal),
    db: Session = Depends(get_db),
):
    """Push this key's expiry out, keeping the same secret.

    **Renewal, not rotation.** The token is unchanged, so an agent
    part-way through a job does not have to be re-bootstrapped — which is the
    entire point, because the caller is typically holding scan output it cannot
    reproduce cheaply.

    It deliberately **accepts an expired key**. The failure this exists for is
    discovered late: an agent launches a long scan, blocks for
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
    """What this key is: its session and project, the operator it acts for
    and their project role, whether it may write project data, and when it
    expires (with where to renew it).

    The first read of a session — what the read-back to the operator is built
    from.  A read-only operator's agent may call it too.

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

    # The operator the access gate checked — this endpoint describes that gate.
    operator_id = request.state.key_operator_id
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

    return AgentIdentity(
        session_id=session_id,
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
    dependencies=[Depends(agent_session_metadata_write)],
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
    operator's call in their own client.  The row lands as
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


# ---------------------------------------------------------------------------
# A configured scanner's credentials — one integration, on a recorded request
# ---------------------------------------------------------------------------

@router.post(
    "/scanner-integrations/{integration_id}/credentials",
    response_model=ScannerCredentialsResponse,
    summary="Request one configured scanner's credentials (recorded)",
)
def request_scanner_credentials(
    body: ScannerCredentialsRequest,
    request: Request,
    integration_id: int = Path(..., ge=1),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Hand the agent ONE scanner integration's credentials, and record it.

    A POST that declares nothing, so the gate's default holds: the key's
    operator must be able to WRITE to the project (analyst and above; an
    auditor's or a viewer's agent is refused 403).  Integrations are the
    installation's, so any project's session may ask for any active one.

    ``operator_agreed`` is the agent's statement that it asked.  The server
    cannot check a conversation it never sees, so nothing here is an approval:
    what it does is refuse a request that does not make the statement, and
    write an audit row (``scanner_credentials_shared``: the operator, the
    project, the session, the integration — no secret) in this request's own
    transaction, before the credentials are returned.
    """
    if body.operator_agreed is not True:
        raise HTTPException(status_code=422, detail=SCANNER_ASK_FIRST_REFUSAL)
    row = IntegrationService(db).get(integration_id)
    if row is None or not row.is_active:
        raise HTTPException(status_code=404, detail="No active scanner integration has that id")
    shared = share_credentials_with_agent(
        db,
        row,
        operator_id=request.state.key_operator_id,
        project_id=request.state.agent_project_id,
        agent_session_id=getattr(request.state, "agent_session_id", None),
        **get_client_info(request),
    )
    db.commit()
    return {**shared, "note": SCANNER_CREDENTIALS_NOTE}


#: The pre-assist reads — ``GET /agent/project``, ``/agent/dashboard``,
#: ``/agent/hosts``, ``/agent/hosts/{id}``, ``/agent/scans`` and
#: ``GET /agent/hosts/{id}/notes`` — went with review 2026-10-23b's AGT-4 (owner): each
#: re-implemented a page read with its own semantics (a substring tool match,
#: four hand-written counts, an unpaged note list).  Their reads are
#: ``/agent/assist/context``, ``/assist/hosts``, ``/assist/hosts/{id}``,
#: ``/assist/scans`` (``created_after`` included) and ``/assist/hosts/{id}/notes``.
#: ``GET /agent/scopes`` went in 2.488.0: a project has one scope.


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

    The note is its operator's, and it is announced like one the operator
    typed (``HostFollowService.post_note``, the page's path): an ``@mention``
    of a project member notifies them, as does a note on a host they are
    reviewing.  The operator is not told about their own agent's note.
    """
    q = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == agent.project_id)
    )
    host = q.first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    note, told = HostFollowService(db).post_note(
        host,
        load_operator(db, request),
        db.get(Project, agent.project_id),
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
        # An @mention that matches nobody is said, not dropped: the agent
        # tells its operator who was not reached (the page shows the same).
        unmatched_mentions=list(told.get("unmatched_mentions") or []),
    )


class AgentFollowResponse(BaseModel):
    host_id: int
    #: The operator's review status on the host after the call (``none`` = removed).
    status: str


@router.post(
    "/hosts/{host_id}/follow",
    response_model=AgentFollowResponse,
    summary="Set review status on a host",
)
def set_agent_follow(
    body: AgentFollowRequest,
    request: Request,
    host_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Set the operator's review status on a host, or remove it (``none``).

    Gated by the operator's project role; see ``create_agent_note``.  The
    accepted values are the request model's (``AgentFollowStatus``): anything
    else — the retired ``watching`` included — is a 422 naming them.  The
    answer states the status now held (it was an empty 204, which an MCP
    client showed as the bare text "OK").
    """
    q = (
        db.query(models.Host)
        .filter(models.Host.id == host_id, models.Host.project_id == agent.project_id)
    )
    host = q.first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    svc = HostFollowService(db)

    # ``none`` removes the operator's review status entirely, so an agent
    # that set one can undo it.
    if body.status == FOLLOW_CLEAR:
        svc.unfollow(host_id, request.state.key_operator_id)
        return AgentFollowResponse(host_id=host_id, status=FOLLOW_CLEAR)

    svc.set_follow_status(
        host_id, request.state.key_operator_id, FollowStatus(body.status),
    )
    return AgentFollowResponse(host_id=host_id, status=body.status)


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
    are editable, and scan-derived facts are never mutated here. Either one,
    once set, is the operator's: no later import replaces it (an empty string
    clears it and hands it back to the scanners). The change is
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
                # The other OS fields follow the corrected name: the family
                # from it (v2.421.0 — left alone, a scanner's "Linux" stayed
                # beside "Windows"), and the scanner's vendor / type /
                # generation / accuracy, which described the guess being
                # replaced, are cleared.  ``os_source`` locks it as the host
                # name's source does: no later import replaces an operator's
                # OS (``HostDeduplicationService._update_existing_host``);
                # clearing the OS drops the lock.
                from app.services.os_family import os_family_from_name
                host.os_family = os_family_from_name(new_value)
                host.os_vendor = None
                host.os_type = None
                host.os_generation = None
                host.os_accuracy = None
                host.os_source = "operator" if new_value else None
            if field == "hostname":
                # An operator correction is the top-ranked display-name source:
                # no later scan, PTR or forward answer may replace it (see
                # dns_name_service.apply_hostname_candidate).  Clearing the
                # name drops the lock so ingestion can name the host again.
                host.hostname_source = "operator" if new_value else None
    # ``changed`` lists the fields whose VALUE moved.  Sending the name the
    # host already has changes none, but it still pins the name (the source
    # above) — and that used to be left uncommitted, so the next scan could
    # rename a host its operator had just confirmed.
    if changed or db.is_modified(host):
        db.commit()
        db.refresh(host)

    return AgentHostUpdateResponse(
        id=host.id,
        ip_address=host.ip_address,
        hostname=host.hostname,
        os_name=host.os_name,
        changed=changed,
    )
