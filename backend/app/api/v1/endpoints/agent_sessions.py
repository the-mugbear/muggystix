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

from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, require_project_role
from app.api.v1.endpoints.auth import get_current_user
from app.db.models_agent import AgentSession, AgentSessionWorkflow
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.session import get_db
from app.services.agent_key_ttl import resolve_ttl_hours, session_renewal_deadline
from app.services.agent_session_service import (
    SESSION_ACTIVE,
    count_agent_sessions,
    end_agent_session,
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
SessionKindLiteral = Literal["project", "recon", "plan_generation", "execution", "assist"]


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
    scope_id: Optional[int] = None
    test_plan_id: Optional[int] = None
    # v2.306.0 — the session's declared target in words (scope name + CIDRs, or
    # the plan title). "Scope #3" cannot tell a second analyst that a range is
    # already being worked, which is the reason a session declares one.
    target_label: Optional[str] = None
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
    active_recon_session_ids: List[int] = []
    active_execution_session_ids: List[int] = []


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
    recon: int = 0
    plan_generation: int = 0
    execution: int = 0
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
            "Narrow to one workflow kind.  Omit to get all four "
            "(recon, plan_generation, execution, assist)."
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
            "Filter by native status of each kind.  Recon + execution use "
            "'active' / 'paused' / 'completed' / 'failed' / 'abandoned'; "
            "plan_generation uses TestPlan.status — 'draft' / "
            "'pending_review' / 'approved' / 'in_progress' / 'completed' / "
            "'rejected'; assist uses 'active' / 'ended' / 'expired'.  Pass "
            "'active' for the in-flight-runs banner — it is the one value "
            "every kind shares."
        ),
    ),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return every agent session (recon + plan generation + execution + assist)
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
        sessions=[AgentSessionRowResponse(**r.to_dict()) for r in page],
        total=total,
    )


def _is_project_admin(db: Session, *, user: User, project_id: int) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    membership = (
        db.query(ProjectMembership)
        .filter(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user.id,
        )
        .first()
    )
    return membership is not None and membership.role == ProjectRole.ADMIN.value


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
    keys revoked, open recon runs abandoned, open execution runs paused,
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
    if session.started_by_id != current_user.id and not _is_project_admin(
        db, user=current_user, project_id=project.id
    ):
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
    the agent never called the phase's ``/complete`` and the session sits
    ``active`` with a perfectly good key that the operator may or may not still
    have configured.  Agent Activity showed such a row with only an End
    button.  This is the other button.

    Same session, same open phases, same audit trail: the key is rotated on
    the existing ``AgentSession`` (the prior key is revoked in the same
    statement), and the response carries what the start dialog carried — the
    replacement key, the prompt with the resumed notice, and the MCP client
    setup — so the operator can hand the session back to an agent.  The
    per-phase resume routes on Scopes / Test Plans mint a *new* session and
    supersede the old one; they remain for legacy phase rows that have no
    project session.

    Owner only — no admin override, unlike End.  The key acts as the operator
    who started the session, so handing it to anyone else would let them act
    under that operator's name.  An admin who needs to take over ends this
    session and starts their own.
    """
    from app.api.v1.endpoints.assist import _build_mcp_clients
    from app.services.agent_prompt_service import build_session_instructions, resolve_base_url
    from app.services.agent_session_service import session_phase_summary

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
                f"This is a legacy '{session.workflow}' session; resume it from its own "
                "page (the scope's recon run or the plan's execution run)."
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
        resumed=True,
    )
    db.commit()

    phases = session_phase_summary(db, session)
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
        active_recon_session_ids=phases["active_recon_session_ids"],
        active_execution_session_ids=phases["active_execution_session_ids"],
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
