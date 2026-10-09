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
count and naming the worst few rows ("<finding> on <address>", then "and N
more") — never one per row.

A third kind: a DEFERRED row that reached the day it was to be looked at again
(``deferred_review_on``), once per review date (``deferral_alerted_for``).
"""
import logging
from datetime import date
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.db.models import Host
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Notification, Project, ProjectMembership, ProjectRole
from app.db.models_remediation import FindingHostRemediation as Remediation
from app.services import remediation_policy as deadlines

logger = logging.getLogger(__name__)

#: ``notifications.source_type`` per kind — what the link is decided from
#: (with ``source_id``, the project): the list of overdue rows, of due-soon
#: rows, or of deferrals due for review, in that project.
SOURCE_TYPES = {"due_soon": "remediation_due_soon", "overdue": "remediation_overdue",
                "deferral_review": "remediation_deferral_review"}
_ALERTED = {"due_soon": Remediation.due_soon_alerted_for, "overdue": Remediation.overdue_alerted_for,
            "deferral_review": Remediation.deferral_alerted_for}
#: How many rows a notification names before "and N more".
NAMED_ROWS = 3
_TITLE_CHARS = 80


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
    elif kind == "deferral_review":
        text = (f"{n} deferred {what} {'has' if n == 1 else 'have'} reached "
                f"{'its' if n == 1 else 'their'} review date")
    else:
        text = f"{n} {what} {'is' if n == 1 else 'are'} due within {policy.due_soon_days} days"
    return f"{project_name}: {text}"[:255]


_THEN = {
    "overdue": "Open the project's overdue list to see them and follow up with their contacts.",
    "due_soon": "Open the project's due-soon list to see them and follow up with their contacts.",
    "deferral_review": "Open the project's deferrals due for review to decide each: a new review date, or back to open.",
}


def _body(kind: str, named: list[tuple[str, str]], total: int) -> str:
    """Which rows: the worst few as "<finding> on <address>", then how many more."""
    rows = [f"{(title or 'Untitled finding')[:_TITLE_CHARS]} on {address}" for title, address in named]
    more = total - len(rows)
    listed = "; ".join(rows) + (f"; and {more} more" if more > 0 else "")
    return f"{listed}. {_THEN[kind]}"


def sweep(db: Session, today: Optional[date] = None) -> int:
    """Raise the alerts that are due; returns how many notifications were
    written.  The caller commits."""
    policy = deadlines.load(db)
    if not policy.enabled:
        return 0
    today = today or policy.today()
    state_of, due = deadlines.state_expr(db, policy, today), deadlines.due_expr(db, policy)
    # What a row is alerted FOR, per kind: its deadline, or — a deferral — the
    # day it was to be looked at again.  A deferral with no review date (made
    # before review dates existed) has no day to reach: the page lists it
    # (``flag=deferral_review_due``), no alert is raised for it.
    review = Remediation.deferred_review_on
    kinds = (
        ("overdue", state_of == "overdue", due),
        ("due_soon", state_of == "due_soon", due),
        ("deferral_review", and_(deadlines.flag_exprs(db, today)["deferral_review_due"], review.isnot(None)), review),
    )
    written = 0
    for kind, reached, day in kinds:
        alerted = _ALERTED[kind]
        # SKIP LOCKED: the sweep never waits for a row a writer holds (it is
        # alerted on the next sweep), so it cannot be part of a lock cycle
        # with the writers, which lock in ``remediation_service._lock_for_write``'s order.
        # The most overdue (or the soonest) first: the first few are named.
        rows = (db.query(Remediation.id, Remediation.project_id, day, Finding.title, Host.ip_address)
                .join(FindingHost, FindingHost.id == Remediation.finding_host_id)
                .join(Finding, Finding.id == FindingHost.finding_id)
                .join(Host, Host.id == FindingHost.host_id)
                .filter(reached, or_(alerted.is_(None), alerted != day))
                .order_by(day, Remediation.id).with_for_update(of=Remediation, skip_locked=True).all())
        per_project: dict[int, list[tuple[str, str]]] = {}
        by_day: dict[date, list[int]] = {}
        for row_id, project_id, reached_on, title, address in rows:
            per_project.setdefault(project_id, []).append((title, address))
            by_day.setdefault(deadlines.as_date(reached_on), []).append(row_id)
        names = dict(db.query(Project.id, Project.name).filter(Project.id.in_(per_project))) if per_project else {}
        for project_id, found in per_project.items():
            title = _title(kind, len(found), names.get(project_id) or f"Project {project_id}", policy)
            body = _body(kind, found[:NAMED_ROWS], len(found))
            for user_id in sorted(_recipients(db, project_id)):
                db.add(Notification(
                    user_id=user_id, project_id=project_id, type="remediation", title=title, body=body,
                    source_type=SOURCE_TYPES[kind], source_id=project_id, actor_id=None,
                ))
                written += 1
        # One statement per day, not per row: the rows that share a deadline
        # (or a review date) remember it together.
        for reached_on, row_ids in by_day.items():
            db.query(Remediation).filter(Remediation.id.in_(row_ids)).update(
                {alerted: reached_on}, synchronize_session=False)
    db.flush()
    return written
