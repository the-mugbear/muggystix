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
from app.api.v1.endpoints.auth import get_current_user
from app.db.models_agent import AgentSession, AgentSessionWorkflow
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.session import get_db
from app.services.integration_service import active_integrations_for_prompt
from app.services.agent_key_ttl import resolve_ttl_hours, session_renewal_deadline
from app.services.agent_session_service import (
    SESSION_ACTIVE,
    count_agent_sessions,
    end_agent_session,
    get_agent_session_row,
    key_expiry_for_agent_sessions,
    list_agent_sessions,
    resolve_project_agent,
    resume_agent_session,
    summarise_by_model_tool,
)


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
    # v2.402.0 — legacy assist rows only: the agent session the row belongs to
    # and whether it can still act (active, with a live or renewable key).
    # None when not computed.
    agent_session_id: Optional[int] = None
    session_live: Optional[bool] = None
    # v2.432.0 — project sessions only: its detail row's id (notes, API-call
    # feed), its last authenticated call, and the authority it acts with.
    # ``can_end`` / ``can_resume`` are the CALLER's rights on an active session
    # — owner or project admin may end, only the owner may resume (the routes
    # below enforce the same rules).
    # v2.442.0 — the host tests the session proposed and the evidence records
    # it wrote (they replace ``phases``, the runs a session used to open).
    host_test_count: int = 0
    evidence_count: int = 0
    assist_session_id: Optional[int] = None
    last_activity_at: Optional[datetime] = None
    operator_role: Optional[str] = None
    can_end: bool = False
    can_resume: bool = False


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
    """The operator's kill switch for a project session (v2.338.0).

    Until now only a session started from the assist dialog could be ended
    (through ``/assist/sessions/{id}/end``, keyed by that dialog's detail
    row); a session minted from Scopes, Test Plans or Execute had no way to
    be stopped short of its key's TTL.  This ends any ``project`` session:
    keys revoked, open execution runs abandoned (results kept),
    draft plans left as they are.  Idempotent-safe: an already-ended session
    returns 409 so the caller knows nothing changed.

    Owner or project admin only, for the reason the assist route gives:
    peers gain nothing from ending each other's agents and lose a running
    conversation.
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
    from app.api.v1.endpoints.assist import _build_mcp_clients
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
        mcp_clients=[c.model_dump() for c in _build_mcp_clients(
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
    """The session detail page's read — the list's row for one consolidated
    session, built by the same path.  Legacy per-workflow rows have their own
    pages (plan, execution run) and are not served here."""
    row = get_agent_session_row(db, project.id, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Agent session not found in this project")
    return _with_caller_rights(db, [row], user=_user, project_id=project.id)[0]
