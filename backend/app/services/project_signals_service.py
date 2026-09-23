"""Per-project workflow and governance signals shared by Portfolio and Oversight.

Moved out of ``endpoints/portfolio.py`` (v2.377.0) so both cross-project pages
flag the same projects for the same reasons.  Every signal is one grouped
query over the project ids.

"Quiet" is the one time rule, and it is about the PROJECT, not its evidence
(v2.374.1): a project still marked active or in progress with no import for
``QUIET_AFTER_DAYS`` asks the manager "is it finished? mark it completed".  A
completed or archived project is never quiet, and nothing here judges the age
of a scan result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_agent import ExecutionSession, ReconSession, TestPlan, TestPlanEntry
from app.db.models_auth import User
from app.db.models_project import Project, ProjectMembership
from app.services.agent_session_metrics import blocked_exec_session_counts

QUIET_AFTER_DAYS = 14
# v2.398.x — one status for a project under way ('in_progress' was merged
# into 'active' by migration d9f1b3c5e7a2).
IN_PROGRESS_STATUSES = ("active",)


@dataclass
class ProjectSignals:
    scan_count: int = 0
    last_scan_at: Optional[datetime] = None
    days_since_last_scan: Optional[int] = None
    is_quiet: bool = False
    pending_plan_reviews: int = 0
    open_tasks: int = 0
    active_sessions: int = 0
    blocked_sessions: int = 0
    member_count: int = 0
    admins: List[str] = field(default_factory=list)

    @property
    def has_admin(self) -> bool:
        return bool(self.admins)


def _grouped(query) -> Dict[int, int]:
    return {pid: n for pid, n in query.all()}


def project_signals(
    db: Session, projects: Iterable[Project], now: datetime,
) -> Dict[int, ProjectSignals]:
    """Signals for each project (every project present).  ``now`` must be
    timezone-aware; stored naive timestamps are read as UTC."""
    plist = list(projects)
    ids = [p.id for p in plist]
    out: Dict[int, ProjectSignals] = {pid: ProjectSignals() for pid in ids}
    if not ids:
        return out

    scan_rows = (
        db.query(models.Scan.project_id, func.count(models.Scan.id), func.max(models.Scan.created_at))
        .filter(models.Scan.project_id.in_(ids))
        .group_by(models.Scan.project_id)
        .all()
    )
    for pid, n, last in scan_rows:
        out[pid].scan_count = n
        out[pid].last_scan_at = last

    pending = _grouped(
        db.query(TestPlan.project_id, func.count(TestPlan.id))
        .filter(TestPlan.project_id.in_(ids), TestPlan.status == "proposed")
        .group_by(TestPlan.project_id)
    )
    # Open tasks: non-terminal entries on accepted plans.
    open_tasks = _grouped(
        db.query(TestPlan.project_id, func.count(TestPlanEntry.id))
        .join(TestPlanEntry, TestPlanEntry.test_plan_id == TestPlan.id)
        .filter(
            TestPlan.project_id.in_(ids),
            TestPlan.status.in_(("approved", "in_progress", "completed")),
            TestPlanEntry.status.in_(("proposed", "approved", "in_progress")),
        )
        .group_by(TestPlan.project_id)
    )
    active_exec = _grouped(
        db.query(TestPlan.project_id, func.count(ExecutionSession.id))
        .join(ExecutionSession, ExecutionSession.test_plan_id == TestPlan.id)
        .filter(TestPlan.project_id.in_(ids), ExecutionSession.status == "active")
        .group_by(TestPlan.project_id)
    )
    active_recon = _grouped(
        db.query(ReconSession.project_id, func.count(ReconSession.id))
        .filter(ReconSession.project_id.in_(ids), ReconSession.status == "active")
        .group_by(ReconSession.project_id)
    )
    # Only the LATEST execution session per plan counts as blocked — shared
    # with Security Posture (agent_session_metrics).
    blocked = blocked_exec_session_counts(db, ids)
    members = _grouped(
        db.query(ProjectMembership.project_id, func.count(ProjectMembership.id))
        .filter(ProjectMembership.project_id.in_(ids))
        .group_by(ProjectMembership.project_id)
    )
    # A project with no admin member: nobody can manage its membership.
    for pid, full_name, username in (
        db.query(ProjectMembership.project_id, User.full_name, User.username)
        .join(User, User.id == ProjectMembership.user_id)
        .filter(ProjectMembership.project_id.in_(ids), ProjectMembership.role == "admin")
        .order_by(User.username)
        .all()
    ):
        out[pid].admins.append(full_name or username)

    now_naive = now.replace(tzinfo=None)
    for p in plist:
        s = out[p.id]
        s.pending_plan_reviews = pending.get(p.id, 0)
        s.open_tasks = open_tasks.get(p.id, 0)
        s.active_sessions = active_exec.get(p.id, 0) + active_recon.get(p.id, 0)
        s.blocked_sessions = blocked.get(p.id, 0)
        s.member_count = members.get(p.id, 0)
        if s.last_scan_at is not None:
            last = s.last_scan_at.replace(tzinfo=None) if s.last_scan_at.tzinfo else s.last_scan_at
            s.days_since_last_scan = (now_naive - last).days
            s.is_quiet = (
                p.status in IN_PROGRESS_STATUSES
                and s.days_since_last_scan >= QUIET_AFTER_DAYS
            )
    return out
