"""Shared agent-session metrics — single source of truth for the workflow
invariants two manager surfaces (Portfolio + Security Posture) both encode.

The one that bit us: "blocked runs". Only the LATEST execution session per plan
counts — starting a replacement run deliberately leaves the prior one paused, so
counting every historical paused/failed session left a permanent "blocked" flag
on a normal workflow. ABANDONED is excluded on purpose: an abandoned session is
already deliberately closed, not stuck.
"""
from __future__ import annotations

from typing import Dict, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models_agent import ExecutionSession, TestPlan

# A plan's execution is "blocked" when its latest session is paused or failed.
# NOT abandoned (deliberately closed) and NOT completed/active (progressing).
BLOCKED_EXEC_STATUSES = ("paused", "failed")


def blocked_exec_session_counts(
    db: Session, project_ids: Sequence[int],
) -> Dict[int, int]:
    """Map project_id → count of plans whose LATEST execution session is in a
    blocked state. Sessions scope to a project through their test plan.

    Latest = max(id) per plan (id is monotonic). Projects with no blocked
    latest session are simply absent from the dict (callers use .get(pid, 0)).
    """
    project_ids = list(project_ids)
    if not project_ids:
        return {}
    latest_exec_subq = (
        db.query(
            ExecutionSession.test_plan_id.label("plan_id"),
            func.max(ExecutionSession.id).label("max_id"),
        )
        .group_by(ExecutionSession.test_plan_id)
        .subquery()
    )
    return dict(
        db.query(TestPlan.project_id, func.count(ExecutionSession.id))
        .join(latest_exec_subq, ExecutionSession.id == latest_exec_subq.c.max_id)
        .join(TestPlan, ExecutionSession.test_plan_id == TestPlan.id)
        .filter(
            TestPlan.project_id.in_(project_ids),
            ExecutionSession.status.in_(BLOCKED_EXEC_STATUSES),
        )
        .group_by(TestPlan.project_id)
        .all()
    )
