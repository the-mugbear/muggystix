"""
Agent API activity log — human-facing read endpoint (v2.24.0).

The middleware in ``app/services/agent_api_log_service.py`` writes one
row to ``agent_api_calls`` per inbound /agent/* request that
authenticated as an agent.  This module exposes those rows to authorised
users so they can audit what their agent actually did.

Scoped to a project + (optionally) a test_plan / recon_session.
Authenticates as a regular BlueStick user (JWT or session), not as
the agent — agents must not be able to read their own audit log.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_project
from app.db.session import get_db
from app.db.models_agent import AgentApiCall
from app.db.models_auth import User
from app.db.models_project import Project

router = APIRouter()


class AgentApiCallRow(BaseModel):
    """One captured agent → BlueStick request."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    agent_id: int
    api_key_prefix: Optional[str] = None
    source_ip: Optional[str] = None

    method: str
    path: str
    path_template: Optional[str] = None
    path_params: Optional[dict] = None
    query_params: Optional[dict] = None
    request_body_summary: Optional[dict] = None
    status_code: int
    response_bytes: Optional[int] = None
    duration_ms: int

    test_plan_id: Optional[int] = None
    execution_session_id: Optional[int] = None
    recon_session_id: Optional[int] = None
    scope_id: Optional[int] = None

    referenced_host_ids: Optional[List[int]] = None
    referenced_entry_ids: Optional[List[int]] = None
    referenced_target_ips: Optional[List[str]] = None


class AgentApiCallListResponse(BaseModel):
    total: int = Field(description="Total rows matching the filters (before paging).")
    items: List[AgentApiCallRow]


def _base_query(
    db: Session,
    project_id: int,
    test_plan_id: Optional[int],
    recon_session_id: Optional[int],
    method: Optional[str],
    status_min: Optional[int],
    status_max: Optional[int],
    host_id: Optional[int],
    target_ip: Optional[str],
    since: Optional[datetime],
    until: Optional[datetime],
):
    q = db.query(AgentApiCall).filter(AgentApiCall.project_id == project_id)
    if test_plan_id is not None:
        q = q.filter(AgentApiCall.test_plan_id == test_plan_id)
    if recon_session_id is not None:
        q = q.filter(AgentApiCall.recon_session_id == recon_session_id)
    if method:
        q = q.filter(AgentApiCall.method == method.upper())
    if status_min is not None:
        q = q.filter(AgentApiCall.status_code >= status_min)
    if status_max is not None:
        q = q.filter(AgentApiCall.status_code <= status_max)
    if since is not None:
        q = q.filter(AgentApiCall.created_at >= since)
    if until is not None:
        q = q.filter(AgentApiCall.created_at <= until)

    # Filter "did the agent touch host X?".  referenced_host_ids is a
    # JSON column; the contains() pattern below works on Postgres'
    # JSONB and on SQLite's JSON1 (the test suite uses Postgres but
    # we keep the SQLite path working for the SQLite-only test path).
    if host_id is not None:
        from sqlalchemy import cast, String as SAString
        q = q.filter(cast(AgentApiCall.referenced_host_ids, SAString).contains(str(host_id)))
    if target_ip:
        from sqlalchemy import cast, String as SAString
        q = q.filter(cast(AgentApiCall.referenced_target_ips, SAString).contains(target_ip))

    return q


@router.get(
    "/test-plans/{plan_id}/api-activity",
    response_model=AgentApiCallListResponse,
    summary="List the agent's API calls for this plan",
)
def list_plan_activity(
    project_id: int = Path(..., gt=0),
    plan_id: int = Path(..., gt=0),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    method: Optional[str] = Query(None, description="Filter by HTTP method (GET/POST/...)."),
    status_min: Optional[int] = Query(None, ge=100, le=599),
    status_max: Optional[int] = Query(None, ge=100, le=599),
    host_id: Optional[int] = Query(None, description="Only rows that referenced this host."),
    target_ip: Optional[str] = Query(None, description="Only rows that referenced this IP."),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = _base_query(
        db, project_id=project_id, test_plan_id=plan_id, recon_session_id=None,
        method=method, status_min=status_min, status_max=status_max,
        host_id=host_id, target_ip=target_ip, since=since, until=until,
    )
    total = q.count()
    rows = (
        q.order_by(AgentApiCall.created_at.desc())
        .offset(offset).limit(limit).all()
    )
    return AgentApiCallListResponse(
        total=total,
        items=[AgentApiCallRow.model_validate(r) for r in rows],
    )


# ---------------------------------------------------------------------------
# Project-level analytics summary — aggregates across ALL agent workflows,
# unlike the per-plan / per-recon list endpoints above.  Reads the same
# agent_api_calls audit table; everything is computed server-side with
# GROUP BY so the response stays small regardless of call volume.
# ---------------------------------------------------------------------------

class AgentActivityStatusBreakdown(BaseModel):
    success: int = 0       # 2xx
    client_error: int = 0  # 4xx
    server_error: int = 0  # 5xx
    other: int = 0         # 1xx / 3xx


class AgentActivityWorkflowCount(BaseModel):
    workflow: str          # plan | execution | recon | assist | other
    calls: int


class AgentActivityDayBucket(BaseModel):
    day: str               # ISO date (UTC)
    calls: int
    errors: int            # status_code >= 400


class AgentActivitySessionRow(BaseModel):
    workflow: str
    session_id: int
    calls: int
    last_activity: Optional[datetime] = None


class AgentActivitySummary(BaseModel):
    window_days: int
    total_calls: int
    distinct_agents: int
    first_call_at: Optional[datetime] = None
    last_call_at: Optional[datetime] = None
    status_breakdown: AgentActivityStatusBreakdown
    by_workflow: List[AgentActivityWorkflowCount]
    daily: List[AgentActivityDayBucket]
    busiest_sessions: List[AgentActivitySessionRow]


# Priority-ordered workflow label.  A row can carry several session FKs
# (an execution call also references its test_plan_id); pick the most
# specific so each call counts once.
def _workflow_case():
    return case(
        (AgentApiCall.recon_session_id.isnot(None), "recon"),
        (AgentApiCall.execution_session_id.isnot(None), "execution"),
        (AgentApiCall.assist_session_id.isnot(None), "assist"),
        (AgentApiCall.test_plan_id.isnot(None), "plan"),
        else_="other",
    )


@router.get(
    "/agent-activity/summary",
    response_model=AgentActivitySummary,
    summary="Project-wide agent API-call analytics",
)
def get_agent_activity_summary(
    window_days: int = Query(14, ge=1, le=90, description="Look-back window in days."),
    project: Project = Depends(get_current_project),
    db: Session = Depends(get_db),
):
    """Aggregate the agent API audit log for a project: volume over time,
    HTTP status mix, per-workflow split, and the busiest sessions.

    Complements the per-plan / per-recon list endpoints — this answers
    "how active have agents been across the whole project, and where are
    the errors?".  All aggregation is server-side (GROUP BY), so the
    payload is bounded regardless of how many calls were logged.
    """
    window_start = datetime.now(timezone.utc) - timedelta(days=window_days)
    base = db.query(AgentApiCall).filter(
        AgentApiCall.project_id == project.id,
        AgentApiCall.created_at >= window_start,
    )

    total_calls = base.count()
    if total_calls == 0:
        return AgentActivitySummary(
            window_days=window_days,
            total_calls=0,
            distinct_agents=0,
            status_breakdown=AgentActivityStatusBreakdown(),
            by_workflow=[],
            daily=[],
            busiest_sessions=[],
        )

    distinct_agents = (
        base.with_entities(func.count(func.distinct(AgentApiCall.agent_id))).scalar() or 0
    )
    first_call_at, last_call_at = base.with_entities(
        func.min(AgentApiCall.created_at), func.max(AgentApiCall.created_at)
    ).one()

    # Status mix in a single pass.
    status_row = base.with_entities(
        func.sum(case((AgentApiCall.status_code.between(200, 299), 1), else_=0)),
        func.sum(case((AgentApiCall.status_code.between(400, 499), 1), else_=0)),
        func.sum(case((AgentApiCall.status_code >= 500, 1), else_=0)),
        func.sum(
            case(
                (AgentApiCall.status_code.between(200, 299), 0),
                (AgentApiCall.status_code.between(400, 499), 0),
                (AgentApiCall.status_code >= 500, 0),
                else_=1,
            )
        ),
    ).one()
    status_breakdown = AgentActivityStatusBreakdown(
        success=int(status_row[0] or 0),
        client_error=int(status_row[1] or 0),
        server_error=int(status_row[2] or 0),
        other=int(status_row[3] or 0),
    )

    # Per-workflow split.
    wf = _workflow_case()
    by_workflow = [
        AgentActivityWorkflowCount(workflow=label, calls=int(count))
        for label, count in (
            base.with_entities(wf.label("wf"), func.count(AgentApiCall.id))
            .group_by(wf)
            .all()
        )
    ]
    by_workflow.sort(key=lambda w: w.calls, reverse=True)

    # Daily buckets (UTC) for the volume/error sparkline.  date_trunc is
    # Postgres (production); strftime keeps the SQLite test path working.
    if db.get_bind().dialect.name == "postgresql":
        day = func.date_trunc("day", AgentApiCall.created_at)
    else:
        day = func.strftime("%Y-%m-%d", AgentApiCall.created_at)
    daily = [
        AgentActivityDayBucket(
            day=(d.date().isoformat() if hasattr(d, "date") else str(d)),
            calls=int(calls),
            errors=int(errors or 0),
        )
        for d, calls, errors in (
            base.with_entities(
                day.label("d"),
                func.count(AgentApiCall.id),
                func.sum(case((AgentApiCall.status_code >= 400, 1), else_=0)),
            )
            .group_by(day)
            .order_by(day)
            .all()
        )
    ]

    # Busiest sessions across workflows — one small GROUP BY per FK,
    # merged and capped.
    busiest: List[AgentActivitySessionRow] = []
    for col, label in (
        (AgentApiCall.recon_session_id, "recon"),
        (AgentApiCall.execution_session_id, "execution"),
        (AgentApiCall.assist_session_id, "assist"),
        (AgentApiCall.test_plan_id, "plan"),
    ):
        for sid, count, last in (
            base.with_entities(col, func.count(AgentApiCall.id), func.max(AgentApiCall.created_at))
            .filter(col.isnot(None))
            .group_by(col)
            .all()
        ):
            busiest.append(
                AgentActivitySessionRow(
                    workflow=label, session_id=int(sid), calls=int(count), last_activity=last
                )
            )
    busiest.sort(key=lambda s: s.calls, reverse=True)
    busiest = busiest[:10]

    return AgentActivitySummary(
        window_days=window_days,
        total_calls=total_calls,
        distinct_agents=int(distinct_agents),
        first_call_at=first_call_at,
        last_call_at=last_call_at,
        status_breakdown=status_breakdown,
        by_workflow=by_workflow,
        daily=daily,
        busiest_sessions=busiest,
    )


@router.get(
    "/recon-sessions/{recon_session_id}/api-activity",
    response_model=AgentApiCallListResponse,
    summary="List the agent's API calls for this recon session",
)
def list_recon_session_activity(
    project_id: int = Path(..., gt=0),
    recon_session_id: int = Path(..., gt=0),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    method: Optional[str] = Query(None),
    status_min: Optional[int] = Query(None, ge=100, le=599),
    status_max: Optional[int] = Query(None, ge=100, le=599),
    host_id: Optional[int] = Query(None),
    target_ip: Optional[str] = Query(None),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = _base_query(
        db, project_id=project_id, test_plan_id=None, recon_session_id=recon_session_id,
        method=method, status_min=status_min, status_max=status_max,
        host_id=host_id, target_ip=target_ip, since=since, until=until,
    )
    total = q.count()
    rows = (
        q.order_by(AgentApiCall.created_at.desc())
        .offset(offset).limit(limit).all()
    )
    return AgentApiCallListResponse(
        total=total,
        items=[AgentApiCallRow.model_validate(r) for r in rows],
    )
