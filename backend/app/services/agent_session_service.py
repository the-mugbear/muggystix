"""
Unified agent-session listing — v2.30.0 backend prep for v3 UI.

A project's three agent workflows (recon, plan generation, plan
execution) currently surface on three different pages.  The v3 UI
wants a single timeline ("what has any agent done against this
project") and per-model rollups ("compare claude-opus vs gpt-5-codex
on this project's data") which need a unified read path.

This service is that read path.  It pulls rows from
``recon_sessions``, ``test_plans`` (treating each plan creation as
a "plan_generation" session), and ``execution_sessions``, and
projects them into a common ``AgentSessionRow`` shape.

A SQL view would also work but adds a schema artifact that has to
move with column changes.  A Python UNION ALL is cheaper to evolve
at the current scale (O(10s) sessions per project) and lets the
service add cross-kind logic (e.g. session-status normalisation)
without DDL.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Literal, Optional

from sqlalchemy.orm import Session

from app.db.models_agent import (
    ExecutionSession,
    ReconSession,
    TestPlan,
)


SessionKind = Literal["recon", "plan_generation", "execution"]


@dataclass
class AgentSessionRow:
    """One row in the unified agent-session timeline.

    The three workflows have different native shapes; this is the
    least-common-denominator the v3 UI consumes.  ``scope_id`` is
    populated for recon kind only; ``test_plan_id`` is populated
    for plan_generation + execution.  ``status`` is each session's
    native status field (no normalisation — the v3 UI can map them
    to a presentation palette).
    """
    kind: SessionKind
    id: int
    project_id: int
    agent_id: Optional[int]
    user_id: Optional[int]
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    generated_by_model: Optional[str]
    generated_by_tool: Optional[str]
    prompt_version: Optional[str]
    scope_id: Optional[int]
    test_plan_id: Optional[int]
    # Denormalised display labels — populated by joining agents/users
    # at the service layer so the UI doesn't have to round-trip.
    agent_name: Optional[str] = None
    user_username: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "generated_by_model": self.generated_by_model,
            "generated_by_tool": self.generated_by_tool,
            "prompt_version": self.prompt_version,
            "scope_id": self.scope_id,
            "test_plan_id": self.test_plan_id,
            "agent_name": self.agent_name,
            "user_username": self.user_username,
        }


def _apply_recon_filters(q, *, agent_id, model, tool, user_id, status):
    if agent_id is not None:
        q = q.filter(ReconSession.agent_id == agent_id)
    if model is not None:
        q = q.filter(ReconSession.generated_by_model == model)
    if tool is not None:
        q = q.filter(ReconSession.generated_by_tool == tool)
    if user_id is not None:
        q = q.filter(ReconSession.started_by_id == user_id)
    if status is not None:
        q = q.filter(ReconSession.status == status)
    return q


def _apply_plan_filters(q, *, agent_id, model, tool, user_id, status):
    if agent_id is not None:
        q = q.filter(TestPlan.agent_id == agent_id)
    if model is not None:
        q = q.filter(TestPlan.generated_by_model == model)
    if tool is not None:
        q = q.filter(TestPlan.generated_by_tool == tool)
    if user_id is not None:
        q = q.filter(TestPlan.created_by_user_id == user_id)
    if status is not None:
        q = q.filter(TestPlan.status == status)
    return q


def _plan_generation_status(plan_status: str) -> str:
    """Collapse TestPlan.status to the agent-generation lifecycle.

    v2.45.2 — agent_session_service used to pass TestPlan.status
    through verbatim, which conflated the GENERATION lifecycle
    (DRAFT → PROPOSED) with the EXECUTION lifecycle (APPROVED →
    IN_PROGRESS → COMPLETED).  A plan that the agent submitted
    successfully and the user then approved + executed would show
    in the plan_generation timeline as "in_progress", even though
    the generation agent's work concluded at /submit time.

    Mapping:
      DRAFT     → "in_progress"   (agent is filling in entries)
      PROPOSED  → "submitted"      (agent done; awaiting human review)
      APPROVED, IN_PROGRESS, COMPLETED  → "completed"
        (generation conclusively done — downstream is execution,
        tracked via the execution-session row not this one)
      REJECTED  → "rejected"       (terminal — human declined)
      ARCHIVED  → "archived"       (terminal — long-tail)
      anything else (unknown enum value): passed through unchanged,
        so future TestPlanStatus additions don't silently become
        "in_progress".
    """
    if plan_status == "draft":
        return "in_progress"
    if plan_status == "proposed":
        return "submitted"
    if plan_status in ("approved", "in_progress", "completed"):
        return "completed"
    if plan_status == "rejected":
        return "rejected"
    if plan_status == "archived":
        return "archived"
    return plan_status


def _apply_execution_filters(q, *, agent_id, model, tool, user_id, status):
    if agent_id is not None:
        q = q.filter(ExecutionSession.agent_id == agent_id)
    if model is not None:
        q = q.filter(ExecutionSession.generated_by_model == model)
    if tool is not None:
        q = q.filter(ExecutionSession.generated_by_tool == tool)
    if user_id is not None:
        q = q.filter(ExecutionSession.started_by_id == user_id)
    if status is not None:
        q = q.filter(ExecutionSession.status == status)
    return q


def count_agent_sessions(
    db: Session,
    project_id: int,
    *,
    kinds: Optional[List[SessionKind]] = None,
    agent_id: Optional[int] = None,
    model: Optional[str] = None,
    tool: Optional[str] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
) -> int:
    """v2.43.3 (AUD-O1): count matching sessions across the three kinds
    via three SELECT COUNT(*) queries.  Cheap (uses each table's
    project_id index) and — unlike the old approach of fetching up to
    10_000 rows and computing ``len()`` in Python — never silently
    under-reports the total on long-lived projects.
    """
    from sqlalchemy import func as _func

    total = 0
    want = set(kinds) if kinds is not None else {"recon", "plan_generation", "execution"}

    if "recon" in want:
        q = db.query(_func.count(ReconSession.id)).filter(
            ReconSession.project_id == project_id
        )
        q = _apply_recon_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        total += q.scalar() or 0

    if "plan_generation" in want:
        q = db.query(_func.count(TestPlan.id)).filter(TestPlan.project_id == project_id)
        q = _apply_plan_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        total += q.scalar() or 0

    if "execution" in want:
        q = (
            db.query(_func.count(ExecutionSession.id))
            .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
            .filter(TestPlan.project_id == project_id)
        )
        q = _apply_execution_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        total += q.scalar() or 0

    return total


def list_agent_sessions(
    db: Session,
    project_id: int,
    *,
    kinds: Optional[List[SessionKind]] = None,
    agent_id: Optional[int] = None,
    model: Optional[str] = None,
    tool: Optional[str] = None,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[AgentSessionRow]:
    """Return the unified agent-session list for a project.

    Filters are AND'd.  Ordering is started_at DESC (most recent first)
    with ``id`` + ``kind`` as deterministic tiebreakers.

    ``status`` matches each kind's native status column (recon +
    execution use 'active' / 'paused' / 'completed' / 'failed' /
    'abandoned'; plan_generation uses TestPlan.status —
    'draft' / 'pending_review' / 'approved' / 'in_progress' /
    'completed' / 'rejected').  Pass 'active' for the in-flight
    banner; pass 'completed' for a "last week's runs" view.

    The query strategy is three separate per-kind queries, each
    pre-filtered + pre-sorted, merged + sorted + sliced in Python.
    At project scale (O(10s)-O(100s) sessions per project) this is
    cheaper than a Postgres view UNION because each underlying query
    can use its own index.

    v2.43.3 (AUD-O1): each per-kind SQL query is now bounded by
    ``offset + limit`` (instead of fetching every matching row), so a
    project with thousands of sessions doesn't materialize all of them
    in Python.  Worst case the merge-sort runs across
    ``3 * (offset + limit)`` rows.  For accurate ``total``, the
    endpoint calls ``count_agent_sessions()`` separately.
    """
    rows: List[AgentSessionRow] = []
    want = set(kinds) if kinds is not None else {"recon", "plan_generation", "execution"}
    # Each per-kind query needs at least (offset + limit) rows so that
    # after merge-sort we can correctly slice the requested page; the
    # discarded slop is small relative to fetching the whole table.
    per_kind_cap = max(offset + limit, 1)

    if "recon" in want:
        q = db.query(ReconSession).filter(ReconSession.project_id == project_id)
        q = _apply_recon_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        for s in q.order_by(ReconSession.started_at.desc()).limit(per_kind_cap).all():
            rows.append(AgentSessionRow(
                kind="recon",
                id=s.id,
                project_id=s.project_id,
                agent_id=s.agent_id,
                user_id=s.started_by_id,
                status=s.status,
                started_at=s.started_at,
                completed_at=s.completed_at,
                generated_by_model=s.generated_by_model,
                generated_by_tool=s.generated_by_tool,
                prompt_version=s.prompt_version,
                scope_id=s.scope_id,
                test_plan_id=None,
                agent_name=s.agent.name if s.agent else None,
                user_username=s.started_by.username if s.started_by else None,
            ))

    if "plan_generation" in want:
        # Plan creation is the closest analogue to a "session" for the
        # plan-generation workflow.  Each TestPlan row is one
        # creation event; the timeline uses ``created_at`` as the
        # started_at.
        #
        # v2.45.2 — the displayed status is GENERATION-BOUNDED, not
        # the plan's full lifecycle.  Pre-fix the row passed
        # ``p.status`` through unchanged, so a plan that had been
        # submitted, approved, AND moved to execution would show
        # the agent's plan-generation session as "in_progress" —
        # conflating execution state with generation work that
        # actually finished at /submit time.  The mapping below
        # collapses every post-PROPOSED state to "completed"
        # because the agent's job ends when the plan moves out of
        # DRAFT; downstream lifecycle is the user's concern via
        # the execution surface (which has its own session rows).
        q = db.query(TestPlan).filter(TestPlan.project_id == project_id)
        q = _apply_plan_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        for p in q.order_by(TestPlan.created_at.desc()).limit(per_kind_cap).all():
            gen_status = _plan_generation_status(p.status)
            rows.append(AgentSessionRow(
                kind="plan_generation",
                id=p.id,
                project_id=p.project_id,
                agent_id=p.agent_id,
                user_id=p.created_by_user_id,
                status=gen_status,
                started_at=p.created_at,
                # Generation is "done" the moment the plan leaves
                # DRAFT.  updated_at is a reasonable proxy because
                # the /submit call is the last write the agent makes;
                # subsequent edits (entry additions etc. by humans)
                # don't shift this materially.
                completed_at=p.updated_at if p.status != "draft" else None,
                generated_by_model=p.generated_by_model,
                generated_by_tool=p.generated_by_tool,
                prompt_version=p.prompt_version,
                scope_id=None,
                test_plan_id=p.id,
                agent_name=p.agent.name if p.agent else None,
                user_username=p.created_by_user.username if p.created_by_user else None,
            ))

    if "execution" in want:
        # ExecutionSession is plan-scoped, not project-scoped.  Join
        # through TestPlan to get the project filter.
        q = (
            db.query(ExecutionSession)
            .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
            .filter(TestPlan.project_id == project_id)
        )
        q = _apply_execution_filters(q, agent_id=agent_id, model=model, tool=tool, user_id=user_id, status=status)
        for e in q.order_by(ExecutionSession.started_at.desc()).limit(per_kind_cap).all():
            rows.append(AgentSessionRow(
                kind="execution",
                id=e.id,
                project_id=project_id,  # joined from test_plan above
                agent_id=e.agent_id,
                user_id=e.started_by_id,
                status=e.status,
                started_at=e.started_at,
                completed_at=e.completed_at,
                generated_by_model=e.generated_by_model,
                generated_by_tool=e.generated_by_tool,
                prompt_version=e.prompt_version,
                scope_id=None,
                test_plan_id=e.test_plan_id,
                agent_name=e.agent.name if e.agent else None,
                user_username=e.started_by.username if e.started_by else None,
            ))

    # Stable ordering: most-recent started_at first; nulls last;
    # then by (kind, id) so two rows with identical timestamps
    # don't flip-flop between calls.
    rows.sort(
        key=lambda r: (
            r.started_at is None,  # nulls last
            -(r.started_at.timestamp() if r.started_at else 0),
            r.kind,
            r.id,
        )
    )
    return rows[offset : offset + limit]


def summarise_by_model_tool(
    db: Session,
    project_id: int,
) -> List[dict]:
    """Aggregate sessions by ``(generated_by_model, generated_by_tool)``
    for the v3 per-model rollup card.

    Returns one dict per tuple with counts of each kind so the UI
    can render "claude-opus-4-7 / claude-code: 3 recon, 2 plans,
    5 executions".  Rows with null model+tool are folded into the
    ``(None, None)`` bucket so they're visible — usually the
    pre-v2.28 / pre-v2.30 sessions that never reported attribution.

    v2.43.3 (AUD-O1): aggregation pushed to SQL ``GROUP BY``.  The
    pre-fix path fetched up to 10_000 rows via list_agent_sessions and
    counted in Python, which silently truncated long-lived projects'
    rollups.  Three small GROUP BY queries with the same project_id
    filter are cheap (each table has the index) and complete.
    """
    from sqlalchemy import func as _func

    counts: dict[tuple, dict] = {}

    def _bucket(model, tool, kind, n):
        key = (model, tool)
        bucket = counts.setdefault(key, {
            "generated_by_model": model,
            "generated_by_tool": tool,
            "recon": 0,
            "plan_generation": 0,
            "execution": 0,
            "total": 0,
        })
        bucket[kind] += int(n)
        bucket["total"] += int(n)

    recon_rows = (
        db.query(
            ReconSession.generated_by_model,
            ReconSession.generated_by_tool,
            _func.count(ReconSession.id),
        )
        .filter(ReconSession.project_id == project_id)
        .group_by(ReconSession.generated_by_model, ReconSession.generated_by_tool)
        .all()
    )
    for model, tool, n in recon_rows:
        _bucket(model, tool, "recon", n)

    plan_rows = (
        db.query(
            TestPlan.generated_by_model,
            TestPlan.generated_by_tool,
            _func.count(TestPlan.id),
        )
        .filter(TestPlan.project_id == project_id)
        .group_by(TestPlan.generated_by_model, TestPlan.generated_by_tool)
        .all()
    )
    for model, tool, n in plan_rows:
        _bucket(model, tool, "plan_generation", n)

    exec_rows = (
        db.query(
            ExecutionSession.generated_by_model,
            ExecutionSession.generated_by_tool,
            _func.count(ExecutionSession.id),
        )
        .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
        .filter(TestPlan.project_id == project_id)
        .group_by(ExecutionSession.generated_by_model, ExecutionSession.generated_by_tool)
        .all()
    )
    for model, tool, n in exec_rows:
        _bucket(model, tool, "execution", n)

    # Sort: tuples with reported attribution first, total DESC.
    out = sorted(
        counts.values(),
        key=lambda b: (
            b["generated_by_model"] is None,
            b["generated_by_tool"] is None,
            -b["total"],
        ),
    )
    return out
