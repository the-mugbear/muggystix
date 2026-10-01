"""
Unified agent-session timeline endpoint (v2.30.0).

Surfaces the unified list assembled by
``app.services.agent_session_service``.  This is the data
foundation for the v3 UI's Project Activity timeline + per-(model,
tool) rollup card.

JWT-authenticated, project-scoped.  Agents cannot read this surface
— they should not see other agents' attribution / activity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, require_project_role
from app.core.security import check_permissions
from app.api.deps import get_current_user
from app.db.models import Annotation, Host
from app.db.models_agent import AgentSession, AgentSessionWorkflow
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.session import get_db
from app.services.integration_service import active_integrations_for_prompt
from app.services.agent_key_ttl import resolve_ttl_hours, session_renewal_deadline
from app.services.agent_session_service import (
    SESSION_ACTIVE,
    agent_session_for_legacy_assist_id,
    count_agent_sessions,
    end_agent_session,
    get_agent_session_row,
    key_expiry_for_agent_sessions,
    list_agent_sessions,
    resolve_project_agent,
    resume_agent_session,
    summarise_by_model_tool,
)
from app.services.mcp_client_setup_service import build_session_mcp_clients


router = APIRouter()


# v2.303.0 — assist was missing here, so the surface that calls itself the
# unified agent-session timeline omitted a whole workflow: an operator could
# have a live assist key and see nothing on Agent Runs.
# (The legacy "plan_generation" and "execution" kinds went with their tables
# in v2.442.0.)
SessionKindLiteral = Literal["project", "assist"]


class AgentSessionRowResponse(BaseModel):
    """One row in the unified timeline.  Mirrors the service-layer
    ``AgentSessionRow`` dataclass — kept as a separate Pydantic
    model so OpenAPI gets the right schema."""
    model_config = ConfigDict(from_attributes=False)

    kind: SessionKindLiteral
    id: int
    project_id: int
    agent_id: Optional[int] = None
    agent_name: Optional[str] = None
    user_id: Optional[int] = None
    user_username: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    generated_by_model: Optional[str] = None
    generated_by_tool: Optional[str] = None
    prompt_version: Optional[str] = None
    purpose: Optional[str] = None
    # v2.340.0 — project sessions only.  ``key_expires_at`` is when the live
    # key stops working (None once revoked); ``renewable_until`` is the
    # session's lifetime cap, past which it can neither be renewed by the agent
    # nor resumed by the operator.  Together they tell an ``active`` row apart
    # from an active row whose agent is dead and whose key has lapsed.
    key_expires_at: Optional[datetime] = None
    renewable_until: Optional[datetime] = None
    # v2.343.0 — project sessions only.  ``end_reason`` is how the session
    # ended ('agent' / 'operator' / 'lapsed'; None while active), and
    # ``feedback_count`` is how many feedback submissions it made.  Together
    # they show whether sessions are exiting cleanly and telling us anything on
    # the way out — the two things the feedback loop depends on.
    end_reason: Optional[str] = None
    feedback_count: int = 0
    # v2.402.0 — the operator's display name; the page shows it in preference
    # to the username.
    user_full_name: Optional[str] = None
    # v2.402.0 — legacy assist rows still stored as active: whether the session
    # can still act (a live or renewable key).  None when not computed.
    session_live: Optional[bool] = None
    # v2.432.0 — the session's last authenticated call and the authority it
    # acts with.  ``can_end`` / ``can_resume`` are the CALLER's rights on an
    # active project session — owner or project admin may end, only the owner
    # may resume (the routes below enforce the same rules).
    # v2.442.0 — the host tests the session proposed and the evidence records
    # it wrote (they replace ``phases``, the runs a session used to open).
    host_test_count: int = 0
    evidence_count: int = 0
    last_activity_at: Optional[datetime] = None
    operator_role: Optional[str] = None
    # v2.449.0 — how much the session did: audited calls, the notes it wrote,
    # how the agent reached it ("none" = no authenticated call yet, "mcp" = at
    # least one call through the MCP transport, "curl" = direct HTTP only) and
    # when the first call arrived.  ``id`` is the session's ONLY id: the
    # ``assist_session_id`` / ``agent_session_id`` fields went with the
    # ``assist_sessions`` table.
    call_count: int = 0
    note_count: int = 0
    connection: str = "none"
    first_call_at: Optional[datetime] = None
    can_end: bool = False
    can_resume: bool = False


class AgentSessionNote(BaseModel):
    """A note this session's agent wrote, for the session page.

    Notes are attributed to the operator with an agent badge, so "what did the
    agent put my name on" is the question this answers.
    """
    id: int
    host_id: Optional[int] = None
    host_ip: Optional[str] = None
    hostname: Optional[str] = None
    body: str
    created_at: Optional[datetime] = None


class AgentSessionNotesResponse(BaseModel):
    #: Every note the session wrote; ``items`` is the newest ``limit`` of them.
    total: int
    items: List[AgentSessionNote]


class ResumeAgentSessionRequest(BaseModel):
    ttl_hours: Optional[int] = Field(
        None, ge=1,
        description="TTL for the replacement key; omitted = deployment default, capped.",
    )


class ResumeAgentSessionResponse(BaseModel):
    """Same shape the start dialog renders: the replacement key, the prompt
    (with the resumed notice) and the per-client MCP setup."""
    session_id: int
    project_id: int
    project_name: str
    agent_id: int
    api_key: str
    instructions: str
    mcp_clients: list = []
    mcp_url: str
    key_ttl_hours: int
    key_expires_at: datetime
    renewable_until: Optional[datetime] = None


class AgentSessionListResponse(BaseModel):
    project_id: int
    sessions: List[AgentSessionRowResponse]
    # ``total`` is the count after filters but before the limit/offset
    # slice.  Lets the UI render "showing N of M" without a separate
    # count query.
    total: int


class ModelToolSummaryRow(BaseModel):
    generated_by_model: Optional[str] = None
    generated_by_tool: Optional[str] = None
    project: int = 0
    assist: int = 0
    total: int = 0


class ModelToolSummaryResponse(BaseModel):
    project_id: int
    summary: List[ModelToolSummaryRow]


@router.get(
    "/agent-sessions",
    response_model=AgentSessionListResponse,
    summary="Unified agent-session timeline for this project (v2.30.0)",
)
def get_agent_sessions(
    project_id: int = Path(..., gt=0),
    kind: Optional[SessionKindLiteral] = Query(
        None,
        description=(
            "Narrow to one kind: project (every session since v2.337.0), or a "
            "legacy assist row.  Omit for all."
        ),
    ),
    agent_id: Optional[int] = Query(None, description="Filter by agent."),
    model: Optional[str] = Query(
        None,
        description="Filter by ``generated_by_model`` (e.g. ``claude-opus-4-7``).",
    ),
    tool: Optional[str] = Query(
        None,
        description="Filter by ``generated_by_tool`` (e.g. ``claude-code``).",
    ),
    user_id: Optional[int] = Query(
        None,
        description="Filter by the user who started the session.",
    ),
    status: Optional[str] = Query(
        None,
        description=(
            "Filter by native status: a project session is 'active' / 'ended'; "
            "a legacy assist row 'active' / 'ended' / 'expired'.  Pass 'active' "
            "for the sessions still open."
        ),
    ),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return every agent session (project sessions, plus legacy assist rows)
    for this project, ordered newest-started first.

    Filterable by kind, agent, model, tool, user, status.  Drives the
    v3 Project Activity timeline + the per-(model, tool) comparison
    surface — a user looking at "everything claude-opus-4-7 did on
    this project" passes ``?model=claude-opus-4-7``.
    """
    # v2.43.3 (AUD-O1): total now comes from `count_agent_sessions` —
    # one SELECT COUNT(*) per kind against each underlying table — and
    # the row list is paginated at the SQL layer.  Pre-fix the endpoint
    # fetched up to 10_000 rows, computed total from `len()`, and
    # sliced in memory, which silently truncated long-lived projects'
    # counts AND histories.  See agent_session_service.list_agent_sessions
    # for the per-kind cap rationale.
    kinds = [kind] if kind else None
    common_filters = dict(
        kinds=kinds,
        agent_id=agent_id,
        model=model,
        tool=tool,
        user_id=user_id,
        status=status,
    )
    total = count_agent_sessions(db, project.id, **common_filters)
    page = list_agent_sessions(
        db,
        project.id,
        limit=limit,
        offset=offset,
        **common_filters,
    )
    return AgentSessionListResponse(
        project_id=project.id,
        sessions=_with_caller_rights(db, page, user=_user, project_id=project.id),
        total=total,
    )


def _with_caller_rights(
    db: Session, rows: list, *, user: User, project_id: int,
) -> List[AgentSessionRowResponse]:
    """Wire rows, with what THIS caller may do to each active project session.

    The same rules the End and Resume routes enforce, said up front so the page
    offers only the buttons that will work (it used to guess from the global
    role, so a project admin was never offered End).  Both routes need project
    auditor (an owner demoted to viewer is refused), and Resume is refused past
    the session's lifetime (v2.433.1: the flags ignored both)."""
    role: Optional[str] = None
    looked_up = False
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        row = AgentSessionRowResponse(**r.to_dict())
        if row.kind == "project" and row.status == SESSION_ACTIVE:
            if not looked_up:
                role, looked_up = _caller_project_role(db, user=user, project_id=project_id), True
            may_act = role is not None and check_permissions(role, ProjectRole.AUDITOR.value)
            owner = row.user_id == user.id
            renewable = row.renewable_until is None or _aware(row.renewable_until) > now
            row.can_end = may_act and (owner or role == ProjectRole.ADMIN.value)
            row.can_resume = may_act and owner and renewable
        out.append(row)
    return out


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def _caller_project_role(db: Session, *, user: User, project_id: int) -> Optional[str]:
    """The caller's project role; a global admin counts as project admin."""
    if user.role == UserRole.ADMIN:
        return ProjectRole.ADMIN.value
    return (
        db.query(ProjectMembership.role)
        .filter(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user.id,
        )
        .scalar()
    )


@router.post(
    "/agent-sessions/{session_id}/end",
    status_code=204,
    summary="End an agent session: revoke its key and close what it left open",
)
def end_project_agent_session(
    project_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    # v2.343.2 — AUDITOR, matching start and resume: an auditor could start a
    # session but not end it from Agent Runs.  The owner-or-project-admin
    # check below is the real authorization.
    current_user: User = Depends(require_project_role(ProjectRole.AUDITOR)),
):
    """The operator's kill switch for a session (v2.338.0) — the one End route.

    Revokes the session's key; the host tests it proposed and the evidence it
    recorded stay (they are project data).  An already-ended session returns
    409 so the caller knows nothing changed.

    Owner or project admin only.  An operator may always stop their own
    agent; a project admin may clean up after someone who closed their laptop
    or left the engagement.  Peers may not: they gain nothing from ending each
    other's agents and the owner loses a running conversation (v2.240.4 — any
    analyst could, which handed a colleague's agent 401s mid-run).
    """
    session = (
        db.query(AgentSession)
        .filter(AgentSession.id == session_id, AgentSession.project_id == project.id)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found in this project")
    if session.started_by_id != current_user.id and _caller_project_role(
        db, user=current_user, project_id=project.id
    ) != ProjectRole.ADMIN.value:
        raise HTTPException(
            status_code=403,
            detail=(
                "This session belongs to another operator. Only its owner or a "
                "project admin can end it."
            ),
        )
    if session.status != SESSION_ACTIVE:
        raise HTTPException(
            status_code=409, detail=f"Session already in state '{session.status}'.",
        )
    end_agent_session(db, session, ended_by=current_user)
    db.commit()


@router.post(
    "/agent-sessions/{session_id}/resume",
    response_model=ResumeAgentSessionResponse,
    summary="Resume an agent session: rotate its key and re-issue the prompt + MCP setup",
)
def resume_project_agent_session(
    body: ResumeAgentSessionRequest = ResumeAgentSessionRequest(),
    project_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    request: Request = None,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.AUDITOR)),
):
    """Reconnect an operator to a session whose agent process died (v2.340.0).

    The common case: an editor's agent session timed out while a scan ran, so
    the agent never ended the session and it sits
    ``active`` with a perfectly good key that the operator may or may not still
    have configured.  Agent Activity showed such a row with only an End
    button.  This is the other button.

    Same session, same audit trail: the key is rotated on
    the existing ``AgentSession`` (the prior key is revoked in the same
    statement), and the response carries what the start dialog carried — the
    replacement key, the prompt with the resumed notice, and the MCP client
    setup — so the operator can hand the session back to an agent.  It is the
    only resume (v2.433.0 removed the per-run and per-plan resume routes,
    which minted a NEW session and ended the old one with all its work).

    Owner only — no admin override, unlike End.  The key acts as the operator
    who started the session, so handing it to anyone else would let them act
    under that operator's name.  An admin who needs to take over ends this
    session and starts their own.
    """
    from app.services.agent_prompt_service import build_session_instructions, resolve_base_url

    session = (
        db.query(AgentSession)
        .filter(AgentSession.id == session_id, AgentSession.project_id == project.id)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found in this project")
    if session.workflow != AgentSessionWorkflow.PROJECT.value:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This is a legacy '{session.workflow}' session, which cannot be "
                "resumed. End it and start a new agent session."
            ),
        )
    if session.started_by_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only the operator who started this session can resume it — its key "
                "acts under their name. End it and start your own session instead."
            ),
        )
    ttl_hours = body.ttl_hours
    agent = resolve_project_agent(
        db, project_id=project.id, user=current_user, prefer_agent_id=session.agent_id,
    )
    raw_key = resume_agent_session(
        db, session, agent=agent, resumed_by=current_user, ttl_hours=ttl_hours,
    )
    instructions = build_session_instructions(
        request=request,
        session_id=session.id,
        project_id=project.id,
        project_name=project.name,
        purpose=session.purpose,
        raw_api_key=raw_key,
        user_label=current_user.full_name or current_user.username,
        user_id=current_user.id,
        integrations=active_integrations_for_prompt(
            db, user_id=current_user.id, project_id=project.id,
        ),
        resumed=True,
    )
    db.commit()

    expires_at = key_expiry_for_agent_sessions(db, [session.id]).get(session.id)
    mcp_url = f"{resolve_base_url(request)}/mcp"
    return ResumeAgentSessionResponse(
        session_id=session.id,
        project_id=project.id,
        project_name=project.name,
        agent_id=agent.id,
        api_key=raw_key,
        instructions=instructions,
        mcp_clients=[c.model_dump() for c in build_session_mcp_clients(
            mcp_url, raw_key, project_name=project.name, agent_session_id=session.id,
        )],
        mcp_url=mcp_url,
        key_ttl_hours=resolve_ttl_hours(ttl_hours),
        key_expires_at=expires_at,
        renewable_until=session_renewal_deadline(session),
    )


@router.get(
    "/agent-sessions/by-model-tool",
    response_model=ModelToolSummaryResponse,
    summary="Aggregate session counts grouped by (model, tool) (v2.30.0)",
)
def get_agent_session_summary(
    project_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Per-(generated_by_model, generated_by_tool) rollup of session
    counts across the four workflows.  Drives the v3 "compare
    models" card on the project dashboard."""
    summary = summarise_by_model_tool(db, project.id)
    return ModelToolSummaryResponse(
        project_id=project.id,
        summary=[ModelToolSummaryRow(**row) for row in summary],
    )


# Registered after ``/agent-sessions/by-model-tool``: a static segment declared
# later would otherwise be read as a session id (and refused as a non-integer).
@router.get(
    "/agent-sessions/{session_id}",
    response_model=AgentSessionRowResponse,
    summary="One agent session: key state, the work it opened, the caller's rights (v2.432.0)",
)
def get_agent_session(
    project_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """The session detail page's read — the list's row for one session, built
    by the same path.  A ``project`` session or a legacy ``assist`` one; the
    other legacy workflow rows (recon / plan / execution) are on no page."""
    row = get_agent_session_row(db, project.id, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Agent session not found in this project")
    return _with_caller_rights(db, [row], user=_user, project_id=project.id)[0]


def _listed_session_or_404(db: Session, project_id: int, session_id: int) -> AgentSession:
    """A session the timeline lists, in THIS project — another project's id
    must 404 here, not leak its purpose and note bodies."""
    session = (
        db.query(AgentSession)
        .filter(
            AgentSession.id == session_id,
            AgentSession.project_id == project_id,
            AgentSession.workflow.in_([
                AgentSessionWorkflow.PROJECT.value, AgentSessionWorkflow.ASSIST.value,
            ]),
        )
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found in this project")
    return session


@router.get(
    "/agent-sessions/{session_id}/notes",
    response_model=AgentSessionNotesResponse,
    summary="The notes an agent session wrote, newest first (v2.449.0)",
)
def get_agent_session_notes(
    project_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(require_project_role(ProjectRole.VIEWER)),
):
    """What the agent wrote under its operator's name.  The session's reads
    are the separate ``/agent-sessions/{id}/api-activity`` feed; its tests and
    evidence are on the hosts.  (Served by ``/assist/sessions/{id}``, keyed by
    the pointer row's id, until v2.449.0.)"""
    session = _listed_session_or_404(db, project.id, session_id)
    q = (
        db.query(Annotation, Host.ip_address, Host.hostname)
        .outerjoin(Host, Annotation.host_id == Host.id)
        .filter(Annotation.agent_session_id == session.id)
    )
    total = q.count()
    return AgentSessionNotesResponse(
        total=total,
        items=[
            AgentSessionNote(
                id=a.id, host_id=a.host_id, host_ip=ip, hostname=hostname,
                body=a.body, created_at=a.created_at,
            )
            for a, ip, hostname in q.order_by(Annotation.created_at.desc()).limit(limit).all()
        ],
    )


# ---------------------------------------------------------------------------
# Old links (deprecated)
# ---------------------------------------------------------------------------
#
# Until v2.449.0 a session also had an ``assist_sessions`` row, and its review
# page, End and API-call feed were addressed by THAT row's id.  Notes, feedback
# rows and bookmarks from then still carry such an id.  These routes answer for
# it by finding the session (``agent_sessions.legacy_assist_session_id``) and
# calling the handler above — they hold no logic of their own, and a session
# started since has no such id.  The fourth one, the API-call feed, is beside
# its handler in ``agent_activity.py``.

def session_id_for_legacy_assist_id(db: Session, project_id: int, legacy_id: int) -> int:
    session = agent_session_for_legacy_assist_id(db, project_id, legacy_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Assist session not found in this project")
    return session.id


@router.get(
    "/assist-sessions/{assist_session_id}",
    response_model=AgentSessionRowResponse,
    deprecated=True,
    summary="The session an old assist-session id belongs to — use /agent-sessions/{id}",
)
def get_agent_session_by_legacy_id(
    project_id: int = Path(..., gt=0),
    assist_session_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Resolve an old ``/assist-sessions/{id}`` link: the response is the
    session's row, whose ``id`` is the one every current route takes."""
    return get_agent_session(
        project_id=project_id,
        session_id=session_id_for_legacy_assist_id(db, project.id, assist_session_id),
        db=db, project=project, _user=_user,
    )


@router.get(
    "/assist/sessions/{assist_session_id}",
    response_model=AgentSessionRowResponse,
    deprecated=True,
    summary="The session an old assist-session id belongs to — use /agent-sessions/{id}",
)
def get_agent_session_by_legacy_id_old_path(
    project_id: int = Path(..., gt=0),
    assist_session_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(require_project_role(ProjectRole.VIEWER)),
):
    """As above, at the path the review page's read had.  The response is the
    session row (not the old ``AssistSessionDetail``): its notes are
    ``/agent-sessions/{id}/notes``."""
    return get_agent_session(
        project_id=project_id,
        session_id=session_id_for_legacy_assist_id(db, project.id, assist_session_id),
        db=db, project=project, _user=_user,
    )


@router.post(
    "/assist/sessions/{assist_session_id}/end",
    status_code=204,
    deprecated=True,
    summary="End a session by its old assist-session id — use /agent-sessions/{id}/end",
)
def end_agent_session_by_legacy_id(
    project_id: int = Path(..., gt=0),
    assist_session_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    current_user: User = Depends(require_project_role(ProjectRole.AUDITOR)),
):
    end_project_agent_session(
        project_id=project_id,
        session_id=session_id_for_legacy_assist_id(db, project.id, assist_session_id),
        db=db, project=project, current_user=current_user,
    )
