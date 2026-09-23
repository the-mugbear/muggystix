"""Per-project engagement counts shared by every cross-project surface.

Portfolio (members) and Oversight (global admins) must report the SAME numbers
for a project, so neither computes them itself.  Everything here is a handful
of grouped queries over a list of project ids — never one query per project,
host or row.

What is counted (PORTFOLIO.md, "Metrics and counting rules"):

* **Targets** — hosts in the project's inventory (one per IP per project).
* **Review** — distinct hosts with a review record in ``in_review`` /
  ``reviewed`` (any reviewer).  *Tested* is either, each host once, so it can
  be less than in-review + reviewed when two people hold one host in
  different states.  "Watching" is not testing.
* **Findings** — distinct ``Finding`` rows by the finding's severity, without
  false positives: a finding whose own status is ``false_positive``, or every
  one of whose endpoints is, is not a result.  A finding with no endpoint rows
  still counts.  One finding on 40 hosts counts once; ``affected_targets`` is
  the distinct hosts with a non-false-positive endpoint.
* **Scanner observations** — ``vulnerabilities`` rows by the scanner's
  severity, split into *judged* and *not yet judged* by the host inspector's
  rule (``host_serialization._vuln_coverage``): a row is judged when a scanner
  finding for its issue — promoted from this row, or sharing its issue key —
  includes the row's host, in any endpoint state (promoted, false positive
  here, accepted risk, …).  A finding that covers the issue only on OTHER hosts
  leaves this row not yet judged.  ``tests/test_engagement_metrics.py`` pins
  the SQL to ``_vuln_coverage`` so the two cannot drift.
* **Defect rate** — of the tested targets, those with at least one
  non-false-positive finding endpoint at a severity.  Both sides are tested
  hosts only, so the rate can never pass 100%.

With a ``tester_id`` the review, finding, observation and defect figures are
limited to the hosts THAT person has in review or reviewed (host_count stays
the whole inventory).  Informational and unknown severities are left out of
every severity block.  This measures judging, not fixing — there is no
remediation dimension.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, case, distinct, exists, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from app.db.models import (
    Annotation, FollowStatus, Host, HostFollow, Port, Scan, Scope,
)
from app.db.models_agent import TestPlan, TestPlanEntry
from app.db.models_auth import User
from app.db.models_findings import (
    Finding, FindingHost, FindingHostStatus, FindingSource, FindingStatus,
    FindingStatusHistory,
)
from app.db.models_project import ProjectMembership
from app.db.models_vulnerability import (
    Vulnerability, VulnerabilitySeverity, enum_value,
)

SEVERITIES = ("critical", "high", "medium", "low")
_VULN_SEVERITIES = tuple(VulnerabilitySeverity(s) for s in SEVERITIES)
REVIEW_STATES = (FollowStatus.IN_REVIEW, FollowStatus.REVIEWED)
_FP = FindingStatus.FALSE_POSITIVE.value
_FP_ENDPOINT = FindingHostStatus.FALSE_POSITIVE.value


@dataclass
class SeverityCounts:
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0

    def add(self, severity: str, n: int) -> None:
        if severity in SEVERITIES:
            setattr(self, severity, getattr(self, severity) + int(n or 0))

    def merge(self, other: "SeverityCounts") -> None:
        for s in SEVERITIES:
            self.add(s, getattr(other, s))

    def as_dict(self) -> Dict[str, int]:
        return {s: getattr(self, s) for s in SEVERITIES}


@dataclass
class ProjectEngagement:
    host_count: int = 0
    hosts_in_review: int = 0
    hosts_reviewed: int = 0
    hosts_tested: int = 0
    findings: SeverityCounts = field(default_factory=SeverityCounts)
    finding_affected_targets: int = 0
    observations: SeverityCounts = field(default_factory=SeverityCounts)
    observations_judged: SeverityCounts = field(default_factory=SeverityCounts)
    observations_unjudged: SeverityCounts = field(default_factory=SeverityCounts)
    # Tested targets with >=1 non-false-positive finding endpoint, per severity.
    defect_targets: SeverityCounts = field(default_factory=SeverityCounts)


@dataclass
class Window:
    """A UTC reporting window: ``start`` inclusive, ``end`` exclusive.  Either
    may be None (open-ended).  Datetimes must be timezone-aware."""
    start: Optional[datetime] = None
    end: Optional[datetime] = None

    def clause(self, column):
        conds = []
        if self.start is not None:
            conds.append(column >= self.start)
        if self.end is not None:
            conds.append(column < self.end)
        return and_(*conds) if conds else literal(True)


@dataclass
class PeriodActivity:
    targets_through_end: int = 0
    targets_added: int = 0
    reviews_concluded: int = 0
    imports: int = 0
    contributors: int = 0


# ---------------------------------------------------------------------------
# SQL building blocks
# ---------------------------------------------------------------------------

def observation_judged_on_host():
    """Correlated EXISTS over an outer query on ``Vulnerability`` joined to
    ``Host``: a scanner finding for this row's issue includes this row's host.

    The SQL form of ``_vuln_coverage(...)["finding_on_this_host"]`` — the
    inspector evaluates it per row in Python, which cannot scale to an
    organisation's whole inventory.
    """
    return exists().where(
        FindingHost.finding_id == Finding.id,
        FindingHost.host_id == Vulnerability.host_id,
        Finding.project_id == Host.project_id,
        Finding.source == FindingSource.SCANNER.value,
        or_(
            Finding.vuln_id == Vulnerability.id,
            and_(
                Vulnerability.issue_key.isnot(None),
                Finding.dedup_key == Vulnerability.issue_key,
            ),
        ),
    )


def finding_is_a_result():
    """SQL condition on ``Finding``: not dismissed as a false positive —
    neither by its own status nor on every one of its endpoints."""
    has_endpoint = exists().where(FindingHost.finding_id == Finding.id)
    has_real_endpoint = exists().where(
        FindingHost.finding_id == Finding.id,
        FindingHost.host_status != _FP_ENDPOINT,
    )
    return and_(
        Finding.status != _FP,
        or_(~has_endpoint, has_real_endpoint),
    )


def tested_host_ids(tester_id: Optional[int] = None):
    """Subquery of host ids in review or reviewed (by ``tester_id`` if given)."""
    q = select(HostFollow.host_id).where(HostFollow.status.in_(REVIEW_STATES))
    if tester_id is not None:
        q = q.where(HostFollow.user_id == tester_id)
    return q


# ---------------------------------------------------------------------------
# Current engagement per project
# ---------------------------------------------------------------------------

def project_engagement(
    db: Session, project_ids: Iterable[int], tester_id: Optional[int] = None,
) -> Dict[int, ProjectEngagement]:
    """Current engagement counts for each project id (every id present)."""
    ids: List[int] = list(dict.fromkeys(project_ids))
    out: Dict[int, ProjectEngagement] = {pid: ProjectEngagement() for pid in ids}
    if not ids:
        return out
    scoped_hosts = tested_host_ids(tester_id) if tester_id is not None else None

    for pid, n in (
        db.query(Host.project_id, func.count(Host.id))
        .filter(Host.project_id.in_(ids))
        .group_by(Host.project_id)
        .all()
    ):
        out[pid].host_count = n

    def _follows(q):
        q = q.join(Host, Host.id == HostFollow.host_id).filter(
            Host.project_id.in_(ids), HostFollow.status.in_(REVIEW_STATES),
        )
        return q.filter(HostFollow.user_id == tester_id) if tester_id is not None else q

    for pid, status, n in (
        _follows(db.query(Host.project_id, HostFollow.status, func.count(distinct(HostFollow.host_id))))
        .group_by(Host.project_id, HostFollow.status)
        .all()
    ):
        if enum_value(status) == FollowStatus.IN_REVIEW.value:
            out[pid].hosts_in_review = n
        else:
            out[pid].hosts_reviewed = n
    for pid, n in (
        _follows(db.query(Host.project_id, func.count(distinct(HostFollow.host_id))))
        .group_by(Host.project_id)
        .all()
    ):
        out[pid].hosts_tested = n

    finding_filters = [Finding.project_id.in_(ids), Finding.severity.in_(SEVERITIES), finding_is_a_result()]
    if scoped_hosts is not None:
        finding_filters.append(exists().where(
            FindingHost.finding_id == Finding.id,
            FindingHost.host_id.in_(scoped_hosts),
            FindingHost.host_status != _FP_ENDPOINT,
        ))
    for pid, severity, n in (
        db.query(Finding.project_id, Finding.severity, func.count(Finding.id))
        .filter(*finding_filters)
        .group_by(Finding.project_id, Finding.severity)
        .all()
    ):
        out[pid].findings.add(severity, n)

    endpoint_filters = [
        Finding.project_id.in_(ids),
        Finding.severity.in_(SEVERITIES),
        Finding.status != _FP,
        FindingHost.host_status != _FP_ENDPOINT,
    ]
    if scoped_hosts is not None:
        endpoint_filters.append(FindingHost.host_id.in_(scoped_hosts))
    for pid, n in (
        db.query(Finding.project_id, func.count(distinct(FindingHost.host_id)))
        .join(FindingHost, FindingHost.finding_id == Finding.id)
        .filter(*endpoint_filters)
        .group_by(Finding.project_id)
        .all()
    ):
        out[pid].finding_affected_targets = n
    # Defect rate numerators: tested hosts only (the tester's, when scoped).
    for pid, severity, n in (
        db.query(Finding.project_id, Finding.severity, func.count(distinct(FindingHost.host_id)))
        .join(FindingHost, FindingHost.finding_id == Finding.id)
        .filter(*endpoint_filters, FindingHost.host_id.in_(scoped_hosts if scoped_hosts is not None else tested_host_ids()))
        .group_by(Finding.project_id, Finding.severity)
        .all()
    ):
        out[pid].defect_targets.add(severity, n)

    judged = observation_judged_on_host()
    obs_filters = [Host.project_id.in_(ids), Vulnerability.severity.in_(_VULN_SEVERITIES)]
    if scoped_hosts is not None:
        obs_filters.append(Vulnerability.host_id.in_(scoped_hosts))
    for pid, severity, total, n_judged in (
        db.query(
            Host.project_id,
            Vulnerability.severity,
            func.count(Vulnerability.id),
            func.sum(case((judged, 1), else_=0)),
        )
        .select_from(Vulnerability)
        .join(Host, Host.id == Vulnerability.host_id)
        .filter(*obs_filters)
        .group_by(Host.project_id, Vulnerability.severity)
        .all()
    ):
        sev = enum_value(severity)
        n_judged = int(n_judged or 0)
        e = out[pid]
        e.observations.add(sev, total)
        e.observations_judged.add(sev, n_judged)
        e.observations_unjudged.add(sev, total - n_judged)

    return out


# ---------------------------------------------------------------------------
# Activity in a window
# ---------------------------------------------------------------------------

def contribution_events(project_ids: List[int]):
    """Every authored action on the given projects as ``(user_id, project_id,
    at)`` rows — a UNION ALL over records that already carry an author:
    scans uploaded, notes (whatever they are attached to), findings recorded,
    finding status changes, plans approved or rejected, host reviews
    concluded.  Page views and polling are never contributions.  ``user_id``
    is NULL when no author is recorded — a deleted account (SET NULL), or a
    row written without one (seeds, older records) — which is the
    "unattributed" bucket, never guessed at.  A deleted user's host reviews
    are gone entirely (``host_follows`` CASCADE).
    """
    ids = project_ids

    def ev(user_col, project_col, at_col, *joins_and_filters):
        q = select(user_col.label("user_id"), project_col.label("project_id"), at_col.label("at"))
        for step in joins_and_filters:
            q = step(q)
        return q.where(project_col.in_(ids), at_col.isnot(None))

    A = Annotation
    parts = [
        ev(Scan.uploaded_by_id, Scan.project_id, Scan.created_at),
        ev(A.user_id, Host.project_id, A.created_at, lambda q: q.join(Host, Host.id == A.host_id)),
        ev(A.user_id, Host.project_id, A.created_at,
           lambda q: q.join(Port, Port.id == A.port_id).join(Host, Host.id == Port.host_id)),
        ev(A.user_id, A.project_id, A.created_at),
        ev(A.user_id, Finding.project_id, A.created_at, lambda q: q.join(Finding, Finding.id == A.finding_id)),
        ev(A.user_id, TestPlan.project_id, A.created_at, lambda q: q.join(TestPlan, TestPlan.id == A.plan_id)),
        ev(A.user_id, Scan.project_id, A.created_at, lambda q: q.join(Scan, Scan.id == A.scan_id)),
        ev(A.user_id, Scope.project_id, A.created_at, lambda q: q.join(Scope, Scope.id == A.scope_id)),
        ev(Finding.created_by_id, Finding.project_id, Finding.created_at),
        ev(FindingStatusHistory.changed_by_id, Finding.project_id, FindingStatusHistory.created_at,
           lambda q: q.join(Finding, Finding.id == FindingStatusHistory.finding_id)),
        ev(TestPlan.approved_by_id, TestPlan.project_id, TestPlan.approved_at),
        ev(TestPlan.rejected_by_id, TestPlan.project_id, TestPlan.rejected_at),
        ev(HostFollow.user_id, Host.project_id, HostFollow.reviewed_at,
           lambda q: q.join(Host, Host.id == HostFollow.host_id)),
    ]
    return union_all(*parts).subquery("contribution_events")


def period_activity(
    db: Session, project_ids: Iterable[int], window: Window, tester_id: Optional[int] = None,
) -> Tuple[Dict[int, PeriodActivity], int, int]:
    """Per-project activity in ``window``, plus the cohort-wide distinct
    contributor count and the number of unattributed events (the per-project
    contributor counts do not sum: one person may work in several)."""
    ids: List[int] = list(dict.fromkeys(project_ids))
    out: Dict[int, PeriodActivity] = {pid: PeriodActivity() for pid in ids}
    if not ids:
        return out, 0, 0

    through_end = Window(end=window.end)
    for pid, n in (
        db.query(Host.project_id, func.count(Host.id))
        .filter(Host.project_id.in_(ids), through_end.clause(Host.first_seen))
        .group_by(Host.project_id).all()
    ):
        out[pid].targets_through_end = n
    for pid, n in (
        db.query(Host.project_id, func.count(Host.id))
        .filter(Host.project_id.in_(ids), window.clause(Host.first_seen))
        .group_by(Host.project_id).all()
    ):
        out[pid].targets_added = n
    reviews = (
        db.query(Host.project_id, func.count(distinct(HostFollow.host_id)))
        .join(Host, Host.id == HostFollow.host_id)
        .filter(
            Host.project_id.in_(ids),
            HostFollow.status == FollowStatus.REVIEWED,
            HostFollow.reviewed_at.isnot(None),
            window.clause(HostFollow.reviewed_at),
        )
    )
    if tester_id is not None:
        reviews = reviews.filter(HostFollow.user_id == tester_id)
    for pid, n in reviews.group_by(Host.project_id).all():
        out[pid].reviews_concluded = n
    for pid, n in (
        db.query(Scan.project_id, func.count(Scan.id))
        .filter(Scan.project_id.in_(ids), window.clause(Scan.created_at))
        .group_by(Scan.project_id).all()
    ):
        out[pid].imports = n

    events = contribution_events(ids)
    in_window = window.clause(events.c.at)
    for pid, n in (
        db.query(events.c.project_id, func.count(distinct(events.c.user_id)))
        .filter(events.c.user_id.isnot(None), in_window)
        .group_by(events.c.project_id).all()
    ):
        out[pid].contributors = n
    total_contributors = (
        db.query(func.count(distinct(events.c.user_id)))
        .filter(events.c.user_id.isnot(None), in_window).scalar()
    ) or 0
    unattributed = (
        db.query(func.count()).select_from(events)
        .filter(events.c.user_id.is_(None), in_window).scalar()
    ) or 0
    return out, int(total_contributors), int(unattributed)


# ---------------------------------------------------------------------------
# Testers
# ---------------------------------------------------------------------------

@dataclass
class TesterProject:
    project_id: int
    tested: int = 0
    in_review: int = 0
    reviewed: int = 0
    reviewed_in_period: int = 0
    findings: SeverityCounts = field(default_factory=SeverityCounts)
    role: Optional[str] = None          # None = no longer a member


@dataclass
class TesterRow:
    user_id: int
    username: str
    full_name: Optional[str]
    is_active: bool
    projects: List[TesterProject] = field(default_factory=list)
    open_tasks: int = 0
    last_contribution_at: Optional[datetime] = None

    def total(self, attr: str) -> int:
        return sum(getattr(p, attr) for p in self.projects)

    def findings(self) -> SeverityCounts:
        f = SeverityCounts()
        for p in self.projects:
            f.merge(p.findings)
        return f


def tester_rows(db: Session, project_ids: Iterable[int], window: Window) -> List[TesterRow]:
    """Everyone with a host in review or reviewed in the given projects, with
    their per-project results.  Findings are attributed through the hosts a
    person worked on (not ``finding.owner_id``, which silently drops unowned
    findings); two reviewers of one host both get credit, so rows do not sum
    to the project totals."""
    ids: List[int] = list(dict.fromkeys(project_ids))
    if not ids:
        return []

    cells: Dict[Tuple[int, int], TesterProject] = {}

    def cell(uid: int, pid: int) -> TesterProject:
        key = (uid, pid)
        if key not in cells:
            cells[key] = TesterProject(project_id=pid)
        return cells[key]

    base = (
        db.query(HostFollow.user_id, Host.project_id, HostFollow.status, func.count(distinct(HostFollow.host_id)))
        .join(Host, Host.id == HostFollow.host_id)
        .filter(Host.project_id.in_(ids), HostFollow.status.in_(REVIEW_STATES))
        .group_by(HostFollow.user_id, Host.project_id, HostFollow.status)
    )
    for uid, pid, status, n in base.all():
        c = cell(uid, pid)
        if enum_value(status) == FollowStatus.IN_REVIEW.value:
            c.in_review = n
        else:
            c.reviewed = n
        c.tested += n  # one follow row per (host, user): states are disjoint
    for uid, pid, n in (
        db.query(HostFollow.user_id, Host.project_id, func.count(distinct(HostFollow.host_id)))
        .join(Host, Host.id == HostFollow.host_id)
        .filter(
            Host.project_id.in_(ids),
            HostFollow.status == FollowStatus.REVIEWED,
            HostFollow.reviewed_at.isnot(None),
            window.clause(HostFollow.reviewed_at),
        )
        .group_by(HostFollow.user_id, Host.project_id).all()
    ):
        cell(uid, pid).reviewed_in_period = n
    for uid, pid, severity, n in (
        db.query(HostFollow.user_id, Finding.project_id, Finding.severity, func.count(distinct(Finding.id)))
        .join(FindingHost, FindingHost.host_id == HostFollow.host_id)
        .join(Finding, Finding.id == FindingHost.finding_id)
        .filter(
            Finding.project_id.in_(ids),
            HostFollow.status.in_(REVIEW_STATES),
            Finding.severity.in_(SEVERITIES),
            Finding.status != _FP,
            FindingHost.host_status != _FP_ENDPOINT,
        )
        .group_by(HostFollow.user_id, Finding.project_id, Finding.severity).all()
    ):
        if (uid, pid) in cells:
            cells[(uid, pid)].findings.add(severity, n)

    user_ids = sorted({uid for uid, _ in cells})
    if not user_ids:
        return []
    roles = {
        (uid, pid): role for uid, pid, role in
        db.query(ProjectMembership.user_id, ProjectMembership.project_id, ProjectMembership.role)
        .filter(ProjectMembership.user_id.in_(user_ids), ProjectMembership.project_id.in_(ids)).all()
    }
    tasks = dict(
        db.query(TestPlanEntry.assigned_to_id, func.count(TestPlanEntry.id))
        .join(TestPlan, TestPlan.id == TestPlanEntry.test_plan_id)
        .filter(
            TestPlan.project_id.in_(ids),
            TestPlan.status.in_(("approved", "in_progress", "completed")),
            TestPlanEntry.status.in_(("proposed", "approved", "in_progress")),
            TestPlanEntry.assigned_to_id.in_(user_ids),
        )
        .group_by(TestPlanEntry.assigned_to_id).all()
    )
    events = contribution_events(ids)
    last = dict(
        db.query(events.c.user_id, func.max(events.c.at))
        .filter(events.c.user_id.in_(user_ids), Window(end=window.end).clause(events.c.at))
        .group_by(events.c.user_id).all()
    )
    rows: List[TesterRow] = []
    for uid, username, full_name, is_active in (
        db.query(User.id, User.username, User.full_name, User.is_active)
        .filter(User.id.in_(user_ids)).all()
    ):
        projects = sorted(
            (c for (u, _), c in cells.items() if u == uid), key=lambda c: c.project_id,
        )
        for c in projects:
            c.role = roles.get((uid, c.project_id))
        rows.append(TesterRow(
            user_id=uid, username=username, full_name=full_name,
            is_active=bool(is_active), projects=projects,
            open_tasks=int(tasks.get(uid, 0) or 0),
            last_contribution_at=last.get(uid),
        ))
    return rows


def projects_with_tester(db: Session, tester_id: int) -> List[int]:
    """Project ids where ``tester_id`` has a host in review or reviewed."""
    return [
        pid for (pid,) in
        db.query(distinct(Host.project_id))
        .join(HostFollow, HostFollow.host_id == Host.id)
        .filter(HostFollow.user_id == tester_id, HostFollow.status.in_(REVIEW_STATES)).all()
    ]


def organisation_accounts(db: Session) -> Dict[str, int]:
    """Current account totals, all projects (outside any cohort filter)."""
    total, active = db.query(
        func.count(User.id), func.sum(case((User.is_active.is_(True), 1), else_=0)),
    ).one()
    no_membership = (
        db.query(func.count(User.id))
        .filter(~exists().where(ProjectMembership.user_id == User.id)).scalar()
    )
    total = int(total or 0)
    active = int(active or 0)
    return {
        "total": total, "enabled": active, "disabled": total - active,
        "without_membership": int(no_membership or 0),
    }
