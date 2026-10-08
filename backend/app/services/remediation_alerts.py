"""Alerts for remediation deadlines (v2.461.0; owner, 2026-10-08).

When a finding on a host enters the "due soon" window, and again when it
becomes overdue, the project's admins and the global admins are told — an
in-app notification, nothing leaves the server.  Only on an installation that
turned remediation tracking on.

Each deadline alerts ONCE per kind: the row remembers the deadline it was
alerted for (``due_soon_alerted_for`` / ``overdue_alerted_for``), so a sweep
that runs every minute says nothing new, and a deadline that moved (another
assigned date, another severity, another timeline) alerts again.  A row that
is already overdue when first seen gets the overdue alert only.

One notification per recipient, project and kind per sweep, carrying the
count — never one per row.
"""
import logging
from datetime import date
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Notification, Project, ProjectMembership, ProjectRole
from app.db.models_remediation import FindingHostRemediation as Remediation
from app.services import remediation_policy as deadlines

logger = logging.getLogger(__name__)

#: ``notifications.source_type`` per kind — what the link is decided from.
SOURCE_TYPES = {"due_soon": "remediation_due_soon", "overdue": "remediation_overdue"}
_ALERTED = {"due_soon": Remediation.due_soon_alerted_for, "overdue": Remediation.overdue_alerted_for}


def _recipients(db: Session, project_id: int) -> set[int]:
    """The project's admins and the global admins, active accounts only."""
    project_admins = {uid for (uid,) in db.query(ProjectMembership.user_id).join(
        User, User.id == ProjectMembership.user_id).filter(
        ProjectMembership.project_id == project_id,
        ProjectMembership.role == ProjectRole.ADMIN.value, User.is_active.is_(True))}
    global_admins = {uid for (uid,) in db.query(User.id).filter(
        User.role == UserRole.ADMIN, User.is_active.is_(True))}
    return project_admins | global_admins


def _title(kind: str, n: int, project_name: str, policy: deadlines.Policy) -> str:
    what = "finding on a host" if n == 1 else "findings on hosts"
    if kind == "overdue":
        text = f"{n} {what} {'is' if n == 1 else 'are'} past the remediation deadline"
    else:
        text = f"{n} {what} {'is' if n == 1 else 'are'} due within {policy.due_soon_days} days"
    return f"{project_name}: {text}"[:255]


def sweep(db: Session, today: Optional[date] = None) -> int:
    """Raise the alerts that are due; returns how many notifications were
    written.  The caller commits."""
    policy = deadlines.load(db)
    if not policy.enabled:
        return 0
    today = today or policy.today()
    state_of, due = deadlines.state_expr(db, policy, today), deadlines.due_expr(db, policy)
    written = 0
    for kind in ("overdue", "due_soon"):
        alerted = _ALERTED[kind]
        # SKIP LOCKED: the sweep never waits for a row a writer holds (it is
        # alerted on the next sweep), so it cannot be part of a lock cycle
        # with the writers, which lock in ``remediation_service._lock_for_write``'s order.
        rows = (db.query(Remediation.id, Remediation.project_id, due)
                .join(FindingHost, FindingHost.id == Remediation.finding_host_id)
                .join(Finding, Finding.id == FindingHost.finding_id)
                .filter(state_of == kind, or_(alerted.is_(None), alerted != due))
                .order_by(Remediation.id).with_for_update(of=Remediation, skip_locked=True).all())
        per_project: dict[int, int] = {}
        by_deadline: dict[date, list[int]] = {}
        for row_id, project_id, due_on in rows:
            per_project[project_id] = per_project.get(project_id, 0) + 1
            by_deadline.setdefault(deadlines.as_date(due_on), []).append(row_id)
        names = dict(db.query(Project.id, Project.name).filter(Project.id.in_(per_project))) if per_project else {}
        for project_id, found in per_project.items():
            title = _title(kind, found, names.get(project_id) or f"Project {project_id}", policy)
            for user_id in sorted(_recipients(db, project_id)):
                db.add(Notification(
                    user_id=user_id, project_id=project_id, type="remediation", title=title,
                    body="Open the project's Remediation page to see them and follow up with their contacts.",
                    source_type=SOURCE_TYPES[kind], source_id=project_id, actor_id=None,
                ))
                written += 1
        # One statement per deadline, not per row: the rows that share a
        # deadline remember it together.
        for due_on, row_ids in by_deadline.items():
            db.query(Remediation).filter(Remediation.id.in_(row_ids)).update(
                {alerted: due_on}, synchronize_session=False)
    db.flush()
    return written
