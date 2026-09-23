"""Oversight — the administrators' programme dashboard (v2.377.0).

Every registered project (archived included), filterable by dates, project,
status, tester and engagement window.  Global administrators only: the router
is mounted with ``require_role(ADMIN)`` in ``api.py``, so no endpoint here can
be reached by a member or a project admin.

One request returns the whole cohort — summary, every project row and every
tester row — computed from the shared services, so the totals and the tables
can never disagree and sorting always covers every matching project.  Counting
rules live in ``engagement_metrics_service`` (targets, testing, findings,
finding states, judged / not-yet-judged observations, the share of tested
targets with a finding (``defect_rate``), contributors) and
``project_signals_service`` (approvals, runs, admins, "quiet").

Dates are UTC calendar days: ``start`` and ``end`` are both included.  Figures
are either CURRENT (the latest state, whatever the dates), SELECTED PERIOD
(events inside the dates) or THROUGH END (everything recorded up to ``end``);
every field below says which.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import distinct
from sqlalchemy.orm import Session

from app.db.models import FollowStatus, HostFollow
from app.db.models_auth import User
from app.db.models_project import Project
from app.db.session import get_db
from app.services.engagement_metrics_service import (
    SEVERITIES, SeverityCounts, StateCounts, Window, growth_series, organisation_accounts,
    period_activity, project_engagement, projects_with_tester, tester_rows,
)
from app.services.project_signals_service import IN_PROGRESS_STATUSES, project_signals

router = APIRouter()

COMPLETE_STATUSES = ("completed", "archived")
# The project multi-select's cap (the page sends the chosen ids; none = all).
MAX_PROJECT_FILTER = 500


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class Severity(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class FindingStates(BaseModel):
    """Findings by where they stand; the three add up to the findings total
    (false positives are not results and are counted apart)."""
    under_investigation: int = 0     # open / retest
    confirmed: int = 0
    closed: int = 0                  # accepted risk / remediated


class SeverityRate(BaseModel):
    """Percent of tested targets; None when nothing has been tested."""
    critical: Optional[float] = None
    high: Optional[float] = None
    medium: Optional[float] = None
    low: Optional[float] = None


class OversightWindow(BaseModel):
    start: Optional[date] = None
    end: Optional[date] = None
    timezone: str = "UTC"


class ProjectRow(BaseModel):
    id: int
    name: str
    status: str
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    admins: List[str] = []
    # Current
    host_count: int = 0
    hosts_tested: int = 0
    hosts_in_review: int = 0
    hosts_reviewed: int = 0
    findings: Severity = Severity()
    finding_states: FindingStates = FindingStates()
    findings_false_positive: int = 0
    finding_affected_targets: int = 0
    # Scanner observations (issue × host, raw tool output): every one, and
    # the judged / not-yet-judged split of the same rows.
    observations: Severity = Severity()
    observations_judged: Severity = Severity()
    observations_unjudged: Severity = Severity()
    # "Tested targets with a finding": % of tested targets with at least one
    # non-false-positive finding endpoint at that severity (was "defect").
    defect_rate: SeverityRate = SeverityRate()
    last_scan_at: Optional[datetime] = None
    pending_plan_reviews: int = 0
    blocked_sessions: int = 0
    # Selected period
    targets_added: int = 0
    reviews_concluded: int = 0
    imports: int = 0
    contributors: int = 0
    # Stable codes: critical, high, pending_review, blocked_session, no_admin,
    # quiet, no_data.  They overlap; never sum them.
    attention_reasons: List[str] = []


class TesterProjectRow(BaseModel):
    project_id: int
    project_name: str
    role: Optional[str] = None       # None: no longer a member of this project
    tested: int = 0
    in_review: int = 0
    reviewed: int = 0
    reviewed_in_period: int = 0
    findings: Severity = Severity()


class TesterRowOut(BaseModel):
    user_id: int
    username: str
    full_name: Optional[str] = None
    is_active: bool = True
    active_projects: int = 0         # current memberships on in-progress cohort projects
    tested: int = 0
    in_review: int = 0
    reviewed: int = 0
    reviewed_in_period: int = 0
    findings: Severity = Severity()  # findings on the hosts they tested
    open_tasks: int = 0
    last_contribution_at: Optional[datetime] = None
    projects: List[TesterProjectRow] = []


class SeverityBlock(BaseModel):
    findings: Severity
    finding_states: FindingStates = FindingStates()
    findings_false_positive: int = 0
    finding_affected_targets: int
    observations: Severity
    observations_judged: Severity
    observations_unjudged: Severity
    tested_targets: int
    defect_targets: Severity
    defect_rate: SeverityRate


class OversightSummary(BaseModel):
    projects_total: int
    projects_in_progress: int
    projects_complete: int
    targets_current: int
    targets_through_end: int
    targets_added: int
    targets_tested: int
    targets_in_review: int
    targets_reviewed: int
    reviews_concluded: int
    imports: int
    contributors: int
    unattributed_events: int
    severity: SeverityBlock


class AttentionCounts(BaseModel):
    """Each count names its unit; the groups overlap and are never summed."""
    critical_projects: int = 0
    pending_approval_plans: int = 0
    blocked_runs: int = 0
    no_admin_projects: int = 0
    quiet_projects: int = 0
    no_inventory_projects: int = 0


class GrowthPoint(BaseModel):
    start: date                     # first UTC day of the bucket
    targets_added: int = 0          # hosts first recorded in the bucket
    reviews_concluded: int = 0      # hosts marked reviewed in the bucket
    cumulative_targets: int = 0     # recorded targets through the bucket's end


class Growth(BaseModel):
    unit: str                       # day | week | month
    points: List[GrowthPoint] = []


class Option(BaseModel):
    id: int
    name: str
    status: Optional[str] = None


class OversightResponse(BaseModel):
    window: OversightWindow
    generated_at: datetime
    # "current": severity figures are the latest state.  "period": only
    # findings and observations first RECORDED inside the dates (their judged
    # state is still today's); the defect rate stays current.
    severity_basis: str = "current"
    summary: OversightSummary
    growth: Growth
    attention: AttentionCounts
    accounts: Dict[str, int]
    projects: List[ProjectRow]
    testers: List[TesterRowOut]
    project_options: List[Option]
    tester_options: List[Option]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rate(numerators: SeverityCounts, tested: int) -> SeverityRate:
    if tested <= 0:
        return SeverityRate()
    return SeverityRate(**{s: round(100.0 * getattr(numerators, s) / tested, 1) for s in SEVERITIES})


def _sev(c: SeverityCounts) -> Severity:
    return Severity(**c.as_dict())


def _states(c: StateCounts) -> FindingStates:
    return FindingStates(**c.as_dict())


def _window(start: Optional[date], end: Optional[date], today: date) -> Window:
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start must be on or before end")
    if (start and start > today) or (end and end > today):
        raise HTTPException(status_code=422, detail="dates cannot be in the future")
    return Window(
        start=datetime.combine(start, time.min, tzinfo=timezone.utc) if start else None,
        end=datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc) if end else None,
    )


def _overlaps(p: Project, w: Window) -> bool:
    """Engagement window [start_date, end_date] meets the reporting window.
    A project without a start date has no window and never matches."""
    if p.start_date is None:
        return False
    ps = p.start_date if p.start_date.tzinfo else p.start_date.replace(tzinfo=timezone.utc)
    pe = p.end_date
    if pe is not None and pe.tzinfo is None:
        pe = pe.replace(tzinfo=timezone.utc)
    if w.end is not None and ps >= w.end:
        return False
    if w.start is not None and pe is not None and pe < w.start:
        return False
    return True


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_model=OversightResponse)
def get_oversight_dashboard(
    start: Optional[date] = Query(None, description="First UTC day of the period (inclusive)"),
    end: Optional[date] = Query(None, description="Last UTC day of the period (inclusive)"),
    project_id: List[int] = Query(
        [],
        description=(
            f"Limit to these projects (repeat the parameter; at most {MAX_PROJECT_FILTER}). "
            "An id naming no project is ignored — it covers nothing; the response's "
            "summary.projects_total counts the projects actually covered."
        ),
    ),
    status: List[str] = Query([], description="Limit to these project statuses"),
    tester_id: Optional[int] = Query(None, description="Limit to one tester's work"),
    window_overlap: bool = Query(False, description="Only projects whose engagement window overlaps the dates"),
    severity_basis: Literal["current", "period"] = Query(
        "current", description="current = latest state; period = first recorded inside the dates",
    ),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    window = _window(start, end, now.date())
    if len(project_id) > MAX_PROJECT_FILTER:
        raise HTTPException(
            status_code=422,
            detail=f"Select at most {MAX_PROJECT_FILTER} projects, or none for every project.",
        )
    if any(pid <= 0 for pid in project_id):
        raise HTTPException(status_code=422, detail="Project ids are positive integers.")

    # Every figure below reads `ids` — the cohort after these filters — so a
    # subset of projects scopes the whole page at once.
    all_projects = db.query(Project).order_by(Project.name).all()
    cohort = all_projects
    if project_id:
        wanted = set(project_id)
        cohort = [p for p in cohort if p.id in wanted]
    if status:
        # 'in_progress' is the old name for 'active' (a saved link may carry it).
        wanted_status = {"active" if s == "in_progress" else s for s in status}
        cohort = [p for p in cohort if p.status in wanted_status]
    if window_overlap and (window.start or window.end):
        cohort = [p for p in cohort if _overlaps(p, window)]
    if tester_id is not None:
        theirs = set(projects_with_tester(db, tester_id))
        cohort = [p for p in cohort if p.id in theirs]
    ids = [p.id for p in cohort]

    engagement = project_engagement(
        db, ids, tester_id=tester_id,
        recorded_in=window if severity_basis == "period" else None,
    )
    # The defect rate is a current measure in both bases.
    defects = (
        project_engagement(db, ids, tester_id=tester_id) if severity_basis == "period" else engagement
    )
    activity, contributors, unattributed = period_activity(db, ids, window, tester_id=tester_id)
    signals = project_signals(db, cohort, now)

    rows: List[ProjectRow] = []
    attention = AttentionCounts()
    tot_findings, tot_obs, tot_judged, tot_unjudged, tot_defect = (
        SeverityCounts(), SeverityCounts(), SeverityCounts(), SeverityCounts(), SeverityCounts(),
    )
    tot_states = StateCounts()
    tot = dict(current=0, through_end=0, added=0, tested=0, in_review=0, reviewed=0,
               concluded=0, imports=0, affected=0, false_positive=0)
    for p in cohort:
        e, a, s = engagement[p.id], activity[p.id], signals[p.id]
        critical = e.findings.critical > 0 or e.observations_unjudged.critical > 0
        high = e.findings.high > 0 or e.observations_unjudged.high > 0
        reasons: List[str] = []
        if critical:
            reasons.append("critical")
            attention.critical_projects += 1
        if high:
            reasons.append("high")
        if s.pending_plan_reviews:
            reasons.append("pending_review")
            attention.pending_approval_plans += s.pending_plan_reviews
        if s.blocked_sessions:
            reasons.append("blocked_session")
            attention.blocked_runs += s.blocked_sessions
        if not s.has_admin:
            reasons.append("no_admin")
            attention.no_admin_projects += 1
        if s.is_quiet:
            reasons.append("quiet")
            attention.quiet_projects += 1
        if e.host_count == 0:
            reasons.append("no_data")
            attention.no_inventory_projects += 1

        rows.append(ProjectRow(
            id=p.id, name=p.name, status=p.status,
            start_date=p.start_date, end_date=p.end_date, admins=s.admins,
            host_count=e.host_count, hosts_tested=e.hosts_tested,
            hosts_in_review=e.hosts_in_review, hosts_reviewed=e.hosts_reviewed,
            findings=_sev(e.findings), finding_states=_states(e.finding_states),
            findings_false_positive=e.findings_false_positive,
            finding_affected_targets=e.finding_affected_targets,
            observations=_sev(e.observations), observations_judged=_sev(e.observations_judged),
            observations_unjudged=_sev(e.observations_unjudged),
            defect_rate=_rate(defects[p.id].defect_targets, e.hosts_tested),
            last_scan_at=s.last_scan_at,
            pending_plan_reviews=s.pending_plan_reviews, blocked_sessions=s.blocked_sessions,
            targets_added=a.targets_added, reviews_concluded=a.reviews_concluded,
            imports=a.imports, contributors=a.contributors,
            attention_reasons=reasons,
        ))
        tot_findings.merge(e.findings)
        tot_states.merge(e.finding_states)
        tot["false_positive"] += e.findings_false_positive
        tot_obs.merge(e.observations)
        tot_judged.merge(e.observations_judged)
        tot_unjudged.merge(e.observations_unjudged)
        tot_defect.merge(defects[p.id].defect_targets)
        tot["current"] += e.host_count
        tot["through_end"] += a.targets_through_end
        tot["added"] += a.targets_added
        tot["tested"] += e.hosts_tested
        tot["in_review"] += e.hosts_in_review
        tot["reviewed"] += e.hosts_reviewed
        tot["concluded"] += a.reviews_concluded
        tot["imports"] += a.imports
        tot["affected"] += e.finding_affected_targets

    summary = OversightSummary(
        projects_total=len(cohort),
        projects_in_progress=sum(1 for p in cohort if p.status in IN_PROGRESS_STATUSES),
        projects_complete=sum(1 for p in cohort if p.status in COMPLETE_STATUSES),
        targets_current=tot["current"], targets_through_end=tot["through_end"],
        targets_added=tot["added"], targets_tested=tot["tested"],
        targets_in_review=tot["in_review"], targets_reviewed=tot["reviewed"],
        reviews_concluded=tot["concluded"], imports=tot["imports"],
        contributors=contributors, unattributed_events=unattributed,
        severity=SeverityBlock(
            findings=_sev(tot_findings), finding_states=_states(tot_states),
            findings_false_positive=tot["false_positive"], finding_affected_targets=tot["affected"],
            observations=_sev(tot_obs), observations_judged=_sev(tot_judged),
            observations_unjudged=_sev(tot_unjudged), tested_targets=tot["tested"],
            defect_targets=_sev(tot_defect), defect_rate=_rate(tot_defect, tot["tested"]),
        ),
    )

    names = {p.id: p.name for p in all_projects}
    in_progress_ids = {p.id for p in cohort if p.status in IN_PROGRESS_STATUSES}
    testers: List[TesterRowOut] = []
    for t in tester_rows(db, ids, window):
        if tester_id is not None and t.user_id != tester_id:
            continue
        testers.append(TesterRowOut(
            user_id=t.user_id, username=t.username, full_name=t.full_name, is_active=t.is_active,
            active_projects=sum(1 for c in t.projects if c.role and c.project_id in in_progress_ids),
            tested=t.total("tested"), in_review=t.total("in_review"), reviewed=t.total("reviewed"),
            reviewed_in_period=t.total("reviewed_in_period"), findings=_sev(t.findings()),
            open_tasks=t.open_tasks, last_contribution_at=t.last_contribution_at,
            projects=[
                TesterProjectRow(
                    project_id=c.project_id, project_name=names.get(c.project_id, ""), role=c.role,
                    tested=c.tested, in_review=c.in_review, reviewed=c.reviewed,
                    reviewed_in_period=c.reviewed_in_period, findings=_sev(c.findings),
                ) for c in t.projects
            ],
        ))

    tester_options = sorted(
        (
            Option(id=uid, name=full_name or username)
            for uid, username, full_name in (
                db.query(User.id, User.username, User.full_name)
                .filter(User.id.in_(
                    db.query(distinct(HostFollow.user_id))
                    .filter(HostFollow.status.in_((FollowStatus.IN_REVIEW, FollowStatus.REVIEWED)))
                ))
                .all()
            )
        ),
        key=lambda o: o.name.lower(),
    )

    unit, points = growth_series(db, ids, window, tester_id=tester_id)
    return OversightResponse(
        window=OversightWindow(start=start, end=end),
        generated_at=now,
        severity_basis=severity_basis,
        summary=summary,
        growth=Growth(unit=unit, points=[
            GrowthPoint(start=b.start, targets_added=b.targets_added,
                        reviews_concluded=b.reviews_concluded, cumulative_targets=b.cumulative_targets)
            for b in points
        ]),
        attention=attention,
        accounts=organisation_accounts(db),
        projects=rows,
        testers=testers,
        project_options=[Option(id=p.id, name=p.name, status=p.status) for p in all_projects],
        tester_options=tester_options,
    )
