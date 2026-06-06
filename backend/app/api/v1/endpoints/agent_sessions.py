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

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_current_project
from app.api.v1.endpoints.auth import get_current_user
from app.db.models_auth import User
from app.db.models_project import Project
from app.db.session import get_db
from app.services.agent_session_service import (
    count_agent_sessions,
    list_agent_sessions,
    summarise_by_model_tool,
)


router = APIRouter()


SessionKindLiteral = Literal["recon", "plan_generation", "execution"]


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
    recon: int = 0
    plan_generation: int = 0
    execution: int = 0
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
            "Narrow to one workflow kind.  Omit to get all three "
            "(recon, plan_generation, execution)."
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
            "'rejected'.  Pass 'active' for the in-flight-runs banner."
        ),
    ),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return every agent session (recon + plan generation + execution)
    for this project, ordered newest-started first.

    Filterable by kind, agent, model, tool, user, status.  Drives the
    v3 Project Activity timeline + the per-(model, tool) comparison
    surface — a user looking at "everything claude-opus-4-7 did on
    this project" passes ``?model=claude-opus-4-7``.
    """
    # v2.43.3 (AUD-O1): total now comes from `count_agent_sessions` —
    # three SELECT COUNT(*) queries against each underlying table — and
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
    counts across the three workflows.  Drives the v3 "compare
    models" card on the project dashboard."""
    summary = summarise_by_model_tool(db, project.id)
    return ModelToolSummaryResponse(
        project_id=project.id,
        summary=[ModelToolSummaryRow(**row) for row in summary],
    )
