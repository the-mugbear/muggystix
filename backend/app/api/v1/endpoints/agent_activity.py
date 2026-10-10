"""
Agent API activity log — human-facing read endpoint (v2.24.0).

The middleware in ``app/services/agent_api_log_service.py`` writes one
row to ``agent_api_calls`` per inbound /agent/* request that
authenticated as an agent.  This module exposes those rows to authorised
users so they can audit what their agent actually did.

Scoped to a project and (optionally) one agent session.
Authenticates as a regular BlueStick user (JWT or session), not as
the agent — agents must not be able to read their own audit log.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, get_current_user
from app.db.session import get_db
from app.db.models_agent import (
    AgentApiCall, Agent, AgentFeedback, AgentSession, AgentSessionWorkflow,
)
from app.db.models_auth import User
from app.db.models_project import Project
from app.services.agent_session_service import agent_session_for_legacy_assist_id

router = APIRouter()


class AgentApiCallRow(BaseModel):
    """One captured agent → BlueStick request."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    agent_id: int
    # Owner attribution — who engaged this agent.  Lets the UI label each
    # row and filter to "my agents", so one operator's calls aren't lost in
    # a project-wide firehose.  Joined from Agent.owner; null when the
    # agent or owner row was deleted.
    agent_name: Optional[str] = None
    owner_id: Optional[int] = None
    owner_username: Optional[str] = None
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

    scope_id: Optional[int] = None

    referenced_host_ids: Optional[List[int]] = None
    referenced_target_ips: Optional[List[str]] = None


class AgentApiCallListResponse(BaseModel):
    total: int = Field(description="Total rows matching the filters (before paging).")
    items: List[AgentApiCallRow]


def _base_query(
    db: Session,
    project_id: int,
    method: Optional[str],
    status_min: Optional[int],
    status_max: Optional[int],
    host_id: Optional[int],
    target_ip: Optional[str],
    since: Optional[datetime],
    until: Optional[datetime],
    mine_owner_id: Optional[int] = None,
):
    q = db.query(AgentApiCall).filter(AgentApiCall.project_id == project_id)
    if mine_owner_id is not None:
        # "My agents only" — restrict to calls made by agents this user owns.
        owned = select(Agent.id).where(
            Agent.project_id == project_id, Agent.owner_id == mine_owner_id
        )
        q = q.filter(AgentApiCall.agent_id.in_(owned))
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

    # Filter "did the agent touch host X?".  referenced_host_ids/target_ips
    # are JSON arrays (host_ids: ints, target_ips: strings).  On Postgres use
    # a real jsonb containment (@>) so host 5 matches [5] and NOT [15, 25,
    # 512] — the previous cast-to-text LIKE '%5%' produced exactly those
    # false positives AND seq-scanned this high-write table.  The @> form is
    # backed by the functional GIN index on (col::jsonb).  Fall back to the
    # text-contains match only on non-Postgres (SQLite) backends.
    is_postgres = db.get_bind().dialect.name == "postgresql"
    if host_id is not None:
        if is_postgres:
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB
            q = q.filter(cast(AgentApiCall.referenced_host_ids, JSONB).contains([host_id]))
        else:
            from sqlalchemy import cast, String as SAString
            q = q.filter(cast(AgentApiCall.referenced_host_ids, SAString).contains(str(host_id)))
    if target_ip:
        if is_postgres:
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB
            q = q.filter(cast(AgentApiCall.referenced_target_ips, JSONB).contains([target_ip]))
        else:
            from sqlalchemy import cast, String as SAString
            q = q.filter(cast(AgentApiCall.referenced_target_ips, SAString).contains(target_ip))

    return q


def _serialize_rows(db: Session, rows: List[AgentApiCall]) -> List[AgentApiCallRow]:
    """Attach owner/agent attribution to each row in one batched lookup
    (one query for the page's distinct agent_ids, not one per row)."""
    agent_ids = {r.agent_id for r in rows if r.agent_id is not None}
    owners: dict = {}
    if agent_ids:
        for aid, aname, oid, ouname in (
            db.query(Agent.id, Agent.name, User.id, User.username)
            .outerjoin(User, User.id == Agent.owner_id)
            .filter(Agent.id.in_(agent_ids))
        ):
            owners[aid] = (aname, oid, ouname)
    items = []
    for r in rows:
        aname, oid, ouname = owners.get(r.agent_id, (None, None, None))
        items.append(
            AgentApiCallRow.model_validate(r).model_copy(
                update={"agent_name": aname, "owner_id": oid, "owner_username": ouname}
            )
        )
    return items


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
    workflow: str          # session | assist | other
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


class AgentSessionHygiene(BaseModel):
    """v2.343.0 — do sessions exit cleanly, and do they say anything on the
    way out?  The feedback loop depends on both; until now neither was
    measured, so a prompt change aimed at either could not be evaluated.
    Counted over sessions STARTED in the window, independent of call volume."""
    sessions_started: int = 0
    sessions_active: int = 0
    sessions_ended: int = 0
    ended_by_agent: int = 0      # POST /agent/session/end — the clean exit
    ended_by_operator: int = 0   # End on Agent Runs / the sessions panel
    lapsed: int = 0              # the sweep, past the renewal window
    sessions_with_feedback: int = 0


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
    session_hygiene: AgentSessionHygiene = Field(default_factory=AgentSessionHygiene)


def _session_hygiene(db: Session, project_id: int, window_start: datetime) -> AgentSessionHygiene:
    """Three small aggregates over ``agent_sessions`` for the window."""
    in_window = [AgentSession.project_id == project_id, AgentSession.started_at >= window_start]
    started = db.query(func.count(AgentSession.id)).filter(*in_window).scalar() or 0
    active = (
        db.query(func.count(AgentSession.id))
        .filter(*in_window, AgentSession.status == "active")
        .scalar() or 0
    )
    by_reason = dict(
        db.query(AgentSession.end_reason, func.count(AgentSession.id))
        .filter(*in_window, AgentSession.status != "active")
        .group_by(AgentSession.end_reason)
        .all()
    )
    with_feedback = (
        db.query(func.count(func.distinct(AgentFeedback.agent_session_id)))
        .join(AgentSession, AgentSession.id == AgentFeedback.agent_session_id)
        .filter(*in_window)
        .scalar() or 0
    )
    return AgentSessionHygiene(
        sessions_started=int(started),
        sessions_active=int(active),
        sessions_ended=int(sum(by_reason.values())),
        ended_by_agent=int(by_reason.get("agent", 0)),
        ended_by_operator=int(by_reason.get("operator", 0)),
        lapsed=int(by_reason.get("lapsed", 0)),
        sessions_with_feedback=int(with_feedback),
    )


# One label per call, from the session it belongs to: "assist" for a legacy
# assist session (``agent_sessions.workflow``), "session" for any other, and
# "other" for a call with no session.  Needs the outer join to
# ``agent_sessions`` — the summary's ``labelled`` query.  (v2.449.0: the label used to be
# read from ``agent_api_calls.assist_session_id``; the "execution" and "plan"
# labels went with execution runs and test plans in v2.442.0.)
def _workflow_case():
    return case(
        (AgentApiCall.agent_session_id.is_(None), "other"),
        (AgentSession.workflow == AgentSessionWorkflow.ASSIST.value, "assist"),
        else_="session",
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
    in_window = (
        AgentApiCall.project_id == project.id,
        AgentApiCall.created_at >= window_start,
    )
    base = db.query(AgentApiCall).filter(*in_window)
    # The same calls beside their session, for the two aggregates that label a
    # call by its session's kind.  An outer join on the session's primary key:
    # it adds no rows, and a call with no session keeps its place.
    labelled = (
        db.query(AgentApiCall)
        .outerjoin(AgentSession, AgentSession.id == AgentApiCall.agent_session_id)
        .filter(*in_window)
    )

    total_calls = base.count()
    hygiene = _session_hygiene(db, project.id, window_start)
    if total_calls == 0:
        return AgentActivitySummary(
            window_days=window_days,
            total_calls=0,
            distinct_agents=0,
            status_breakdown=AgentActivityStatusBreakdown(),
            by_workflow=[],
            daily=[],
            busiest_sessions=[],
            session_hygiene=hygiene,
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
            labelled.with_entities(wf.label("wf"), func.count(AgentApiCall.id))
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

    # Busiest sessions: one GROUP BY, ranked and cut in SQL.  ``session_id`` is
    # the session's id whatever its label (v2.449.0 — a legacy assist session
    # used to be listed twice, once under each of its two ids).
    calls_per_session = func.count(AgentApiCall.id)
    busiest = [
        AgentActivitySessionRow(
            workflow=label, session_id=int(sid), calls=int(count), last_activity=last
        )
        for sid, label, count, last in (
            labelled.with_entities(
                AgentApiCall.agent_session_id,
                wf.label("wf"),
                calls_per_session,
                func.max(AgentApiCall.created_at),
            )
            .filter(AgentApiCall.agent_session_id.isnot(None))
            .group_by(AgentApiCall.agent_session_id, wf)
            .order_by(calls_per_session.desc(), AgentApiCall.agent_session_id)
            .limit(10)
            .all()
        )
    ]

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
        session_hygiene=hygiene,
    )


@router.get(
    "/agent-sessions/{session_id}/api-activity",
    response_model=AgentApiCallListResponse,
    summary="List the API calls an agent session made",
)
def list_agent_session_activity(
    project_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    method: Optional[str] = Query(None),
    status_min: Optional[int] = Query(None, ge=100, le=599),
    status_max: Optional[int] = Query(None, ge=100, le=599),
    host_id: Optional[int] = Query(None),
    target_ip: Optional[str] = Query(None),
    mine: bool = Query(False, description="Only calls made by agents the current user owns."),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """What an agent session actually did (v2.284.0) — every authenticated
    call its key made, newest first.

    Keyed by the session id (v2.449.0).  It was keyed by the session's
    ``assist_sessions`` row and had to match calls on either of two columns;
    every call now names its session in ``agent_session_id``
    (``idx_agent_api_call_session_created``).
    """
    # get_current_project enforces ProjectMembership for the path project_id;
    # the session must be THIS project's, or its calls are not listed.
    session = (
        db.query(AgentSession.id)
        .filter(AgentSession.id == session_id, AgentSession.project_id == project.id)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found in this project")
    q = _base_query(
        db, project_id=project.id,
        method=method, status_min=status_min, status_max=status_max,
        host_id=host_id, target_ip=target_ip, since=since, until=until,
        mine_owner_id=current_user.id if mine else None,
    ).filter(AgentApiCall.agent_session_id == session_id)
    total = q.count()
    rows = (
        q.order_by(AgentApiCall.created_at.desc())
        .offset(offset).limit(limit).all()
    )
    return AgentApiCallListResponse(total=total, items=_serialize_rows(db, rows))


@router.get(
    "/assist-sessions/{assist_session_id}/api-activity",
    response_model=AgentApiCallListResponse,
    deprecated=True,
    summary="A session's API calls by its old assist-session id — use /agent-sessions/{id}/api-activity",
)
def list_assist_session_activity(
    project_id: int = Path(..., gt=0),
    assist_session_id: int = Path(..., gt=0),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    method: Optional[str] = Query(None),
    status_min: Optional[int] = Query(None, ge=100, le=599),
    status_max: Optional[int] = Query(None, ge=100, le=599),
    host_id: Optional[int] = Query(None),
    target_ip: Optional[str] = Query(None),
    mine: bool = Query(False, description="Only calls made by agents the current user owns."),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    project: Project = Depends(get_current_project),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The same feed for an old link: finds the session that had this
    ``assist_sessions`` id (only sessions started before v2.449.0 have one) and
    calls the handler above."""
    session = agent_session_for_legacy_assist_id(db, project.id, assist_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Assist session not found in this project")
    return list_agent_session_activity(
        project_id=project_id, session_id=session.id, limit=limit, offset=offset,
        method=method, status_min=status_min, status_max=status_max,
        host_id=host_id, target_ip=target_ip, mine=mine, since=since, until=until,
        project=project, current_user=current_user, db=db,
    )
