"""
Portfolio Dashboard Endpoint

Aggregates summary statistics across all projects the authenticated user
has access to.  Provides a birds-eye view for multi-project management.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.db.models import HostFollow, FollowStatus
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership
from app.db.models_agent import TestPlan, TestPlanEntry
from app.services.engagement_metrics_service import project_engagement
from app.services.project_signals_service import project_signals
from app.api.v1.endpoints.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SeverityBrief(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class ProjectCard(BaseModel):
    id: int
    name: str
    slug: str
    status: str
    description: Optional[str] = None
    host_count: int = 0
    up_host_count: int = 0
    open_port_count: int = 0
    scan_count: int = 0
    last_scan_at: Optional[datetime] = None
    days_since_last_scan: Optional[int] = None
    is_stale: bool = False
    review_progress_pct: float = 0.0
    unreviewed_hosts: int = 0
    # v2.389.0 — the review states on their own, so the page states ONE
    # definition ("12 reviewed · 3 in review · 40 not started") instead of
    # "tested" (in review OR reviewed), "unreviewed" (everything not reviewed,
    # in-review included) and a percentage of reviewed — which did not add up.
    hosts_in_review: int = 0
    hosts_reviewed: int = 0
    # v2.376.0 — targets tested = hosts in review or reviewed (each once).
    hosts_tested: int = 0
    # Two severity representations (engagement_metrics_service): findings are
    # ISSUES (one finding on 40 hosts counts once, false positives excluded);
    # not-yet-judged observations are scanner rows (issue × host) that no
    # finding covers on their host.  Different units — never subtract them.
    findings: SeverityBrief = SeverityBrief()
    unjudged_observations: SeverityBrief = SeverityBrief()
    health: str = "healthy"  # healthy, warning, critical, stale
    # P4 control-plane fields — workflow/attention signals so the
    # cross-project table can answer "what needs attention, and what can
    # I do next?" without opening each project.
    attention_reasons: List[str] = []  # stable codes; frontend maps to labels
    pending_plan_reviews: int = 0
    open_tasks: int = 0
    active_sessions: int = 0          # recon + execution sessions in "active"
    blocked_sessions: int = 0         # execution sessions paused/failed
    member_count: int = 0
    user_role: Optional[str] = None   # caller's project role (None if global-admin non-member)
    # v2.377.0 — the "no project admin" governance signal (has_admin, admins,
    # projects_without_admin, the no_admin reason) moved to Oversight, the
    # admin-only dashboard; this page serves every member.


class PortfolioSummary(BaseModel):
    total_projects: int = 0
    active_projects: int = 0
    total_hosts: int = 0
    total_open_ports: int = 0
    total_scans: int = 0
    total_unreviewed: int = 0
    # v2.389.0 — the lead and measures: review states and the two severity
    # representations, summed over the caller's projects.
    total_reviewed: int = 0
    total_in_review: int = 0
    findings: SeverityBrief = SeverityBrief()
    unjudged_observations: SeverityBrief = SeverityBrief()
    # P4 attention rollups across the visible portfolio.
    projects_requiring_attention: int = 0
    projects_with_critical: int = 0
    stale_projects: int = 0
    projects_no_data: int = 0
    pending_approvals_total: int = 0
    blocked_sessions_total: int = 0


class PortfolioDashboardResponse(BaseModel):
    summary: PortfolioSummary
    projects: List[ProjectCard]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_model=PortfolioDashboardResponse)
def get_portfolio_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Determine accessible projects
    if current_user.role == UserRole.ADMIN:
        projects = (
            db.query(Project)
            .filter(Project.is_archived.is_(False))
            .order_by(Project.name)
            .all()
        )
    else:
        projects = (
            db.query(Project)
            .join(ProjectMembership, ProjectMembership.project_id == Project.id)
            .filter(
                ProjectMembership.user_id == current_user.id,
                Project.is_archived.is_(False),
            )
            .order_by(Project.name)
            .all()
        )

    if not projects:
        return PortfolioDashboardResponse(
            summary=PortfolioSummary(),
            projects=[],
        )

    project_ids = [p.id for p in projects]
    now = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Batch queries for all projects at once
    # ------------------------------------------------------------------

    up_host_counts = dict(
        db.query(models.Host.project_id, func.count(models.Host.id))
        .filter(
            models.Host.project_id.in_(project_ids),
            models.Host.state == "up",
        )
        .group_by(models.Host.project_id)
        .all()
    )

    # Open port counts (join through hosts)
    open_port_counts = dict(
        db.query(models.Host.project_id, func.count(models.Port.id))
        .join(models.Port, models.Port.host_id == models.Host.id)
        .filter(
            models.Host.project_id.in_(project_ids),
            models.Port.state == "open",
        )
        .group_by(models.Host.project_id)
        .all()
    )

    # Targets, review counts, findings and judged / not-yet-judged scanner
    # observations — the shared definitions (engagement_metrics_service), so
    # this page and Oversight report the same numbers for a project.  The
    # severity blocks used to be raw scanner rows, which kept a host-level
    # false-positive dismissal counting as "critical".
    engagement = project_engagement(db, project_ids)

    # Workflow + governance signals (pending approvals, open tasks, active /
    # blocked runs, members, admins, last import, "quiet") — shared with
    # Oversight (project_signals_service).
    signals = project_signals(db, projects, now)

    # The caller's per-project role.
    my_roles = dict(
        db.query(ProjectMembership.project_id, ProjectMembership.role)
        .filter(
            ProjectMembership.project_id.in_(project_ids),
            ProjectMembership.user_id == current_user.id,
        )
        .all()
    )

    # ------------------------------------------------------------------
    # Build response
    # ------------------------------------------------------------------

    cards: List[ProjectCard] = []
    total_hosts = 0
    total_open_ports = 0
    total_scans = 0
    total_unreviewed = 0
    total_reviewed = 0
    total_in_review = 0
    total_findings = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    total_unjudged = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    active_projects = 0
    projects_requiring_attention = 0
    projects_with_critical = 0
    stale_projects = 0
    projects_no_data = 0
    pending_approvals_total = 0
    blocked_sessions_total = 0

    for p in projects:
        e = engagement[p.id]
        s = signals[p.id]
        hc = e.host_count
        uhc = up_host_counts.get(p.id, 0)
        opc = open_port_counts.get(p.id, 0)
        sc = s.scan_count
        rc = e.hosts_reviewed
        findings = SeverityBrief(**e.findings.as_dict())
        unjudged = SeverityBrief(**e.observations_unjudged.as_dict())
        # A critical/high signal is a finding at that severity OR scanner
        # output at that severity nobody has judged yet.  A row dismissed as a
        # false positive on its host is judged, so it no longer keeps a
        # project red.
        has_critical = findings.critical > 0 or unjudged.critical > 0
        has_high = findings.high > 0 or unjudged.high > 0

        unreviewed = max(0, hc - rc)
        review_pct = round((rc / hc) * 100, 1) if hc else 0.0

        # "Quiet" (API code ``stale``): still marked active, no import for a
        # fortnight — a question about the project, never about its evidence.
        is_stale = s.is_quiet

        # Health indicator — v2.389.0: follows what testing FOUND (the Posture
        # rule).  Review coverage under 50% also made a project "warning", so
        # every project that had just started read Warning and "needs
        # attention"; coverage is shown as its own column instead.
        if has_critical:
            health = "critical"
        elif has_high:
            health = "warning"
        elif is_stale:
            health = "stale"
        else:
            health = "healthy"

        if p.status == "active":
            active_projects += 1

        pending_reviews = s.pending_plan_reviews
        blocked_sessions = s.blocked_sessions
        # Global admins may have no per-project membership row; surface
        # their global role so the table never shows a blank for them.
        role = my_roles.get(p.id)
        if role is None and current_user.role == UserRole.ADMIN:
            role = "admin"

        # Attention reasons — a project can trip several at once.  Stable
        # codes; the frontend maps them to labels + row actions.
        reasons: List[str] = []
        if findings.critical > 0:
            reasons.append("critical_findings")
        if findings.high > 0:
            reasons.append("high_findings")
        if unjudged.critical > 0:
            reasons.append("critical_unjudged")
        if unjudged.high > 0:
            reasons.append("high_unjudged")
        if pending_reviews > 0:
            reasons.append("pending_review")
        if blocked_sessions > 0:
            reasons.append("blocked_session")
        if is_stale:
            reasons.append("stale")
        if hc == 0:
            reasons.append("no_data")

        total_hosts += hc
        total_open_ports += opc
        total_scans += sc
        total_unreviewed += unreviewed
        total_reviewed += rc
        total_in_review += e.hosts_in_review
        for sev in ("critical", "high", "medium", "low"):
            total_findings[sev] += getattr(findings, sev)
            total_unjudged[sev] += getattr(unjudged, sev)
        if reasons:
            projects_requiring_attention += 1
        if has_critical:
            projects_with_critical += 1
        if is_stale:
            stale_projects += 1
        if hc == 0:
            projects_no_data += 1
        pending_approvals_total += pending_reviews
        blocked_sessions_total += blocked_sessions

        cards.append(ProjectCard(
            id=p.id,
            name=p.name,
            slug=p.slug,
            status=p.status,
            description=p.description,
            host_count=hc,
            up_host_count=uhc,
            open_port_count=opc,
            scan_count=sc,
            last_scan_at=s.last_scan_at,
            days_since_last_scan=s.days_since_last_scan,
            is_stale=is_stale,
            review_progress_pct=review_pct,
            unreviewed_hosts=unreviewed,
            hosts_tested=e.hosts_tested,
            hosts_in_review=e.hosts_in_review,
            hosts_reviewed=rc,
            findings=findings,
            unjudged_observations=unjudged,
            health=health,
            attention_reasons=reasons,
            pending_plan_reviews=pending_reviews,
            open_tasks=s.open_tasks,
            active_sessions=s.active_sessions,
            blocked_sessions=blocked_sessions,
            member_count=s.member_count,
            user_role=role,
        ))

    return PortfolioDashboardResponse(
        summary=PortfolioSummary(
            total_projects=len(projects),
            active_projects=active_projects,
            total_hosts=total_hosts,
            total_open_ports=total_open_ports,
            total_scans=total_scans,
            total_unreviewed=total_unreviewed,
            total_reviewed=total_reviewed,
            total_in_review=total_in_review,
            findings=SeverityBrief(**total_findings),
            unjudged_observations=SeverityBrief(**total_unjudged),
            projects_requiring_attention=projects_requiring_attention,
            projects_with_critical=projects_with_critical,
            stale_projects=stale_projects,
            projects_no_data=projects_no_data,
            pending_approvals_total=pending_approvals_total,
            blocked_sessions_total=blocked_sessions_total,
        ),
        projects=cards,
    )


# ---------------------------------------------------------------------------
# SOC-P4 — cross-project team roster + per-member workload.
# Member-centric view (who's on what, and how loaded), complementing the
# project-centric dashboard above.
# ---------------------------------------------------------------------------

class TeamMemberProject(BaseModel):
    project_id: int
    project_name: str
    role: str


class TeamMember(BaseModel):
    user_id: int
    username: str
    full_name: Optional[str] = None
    project_count: int = 0
    projects: List[TeamMemberProject] = []
    open_tasks: int = 0          # assigned, non-terminal entries (visible projects)
    hosts_in_review: int = 0     # distinct hosts the member has In Review


class TeamResponse(BaseModel):
    members: List[TeamMember] = []
    total_members: int = 0


@router.get("/team", response_model=TeamResponse)
def get_portfolio_team(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cross-project team roster across the projects the caller can see
    (global admins: all non-archived; members: their projects).  Each row
    is a person with their per-project roles and current workload
    (assigned open tasks + hosts In Review).  All counts are batched."""
    if current_user.role == UserRole.ADMIN:
        project_ids = [
            pid for (pid,) in db.query(Project.id).filter(Project.is_archived.is_(False)).all()
        ]
    else:
        project_ids = [
            pid for (pid,) in (
                db.query(Project.id)
                .join(ProjectMembership, ProjectMembership.project_id == Project.id)
                .filter(
                    ProjectMembership.user_id == current_user.id,
                    Project.is_archived.is_(False),
                )
                .all()
            )
        ]
    if not project_ids:
        return TeamResponse(members=[], total_members=0)

    # Membership rows (one grouped pass via joins).
    rows = (
        db.query(
            User.id, User.username, User.full_name,
            Project.id, Project.name, ProjectMembership.role,
        )
        .join(ProjectMembership, ProjectMembership.user_id == User.id)
        .join(Project, Project.id == ProjectMembership.project_id)
        .filter(ProjectMembership.project_id.in_(project_ids))
        .all()
    )

    # Per-user open assigned tasks (non-terminal entries on accepted plans).
    task_counts = dict(
        db.query(TestPlanEntry.assigned_to_id, func.count(TestPlanEntry.id))
        .join(TestPlan, TestPlanEntry.test_plan_id == TestPlan.id)
        .filter(
            TestPlan.project_id.in_(project_ids),
            TestPlan.status.in_(("approved", "in_progress", "completed")),
            TestPlanEntry.status.in_(("proposed", "approved", "in_progress")),
            TestPlanEntry.assigned_to_id.isnot(None),
        )
        .group_by(TestPlanEntry.assigned_to_id)
        .all()
    )
    # Per-user distinct hosts In Review.
    review_counts = dict(
        db.query(HostFollow.user_id, func.count(distinct(HostFollow.host_id)))
        .join(models.Host, HostFollow.host_id == models.Host.id)
        .filter(
            models.Host.project_id.in_(project_ids),
            HostFollow.status == FollowStatus.IN_REVIEW,
        )
        .group_by(HostFollow.user_id)
        .all()
    )

    members: Dict[int, TeamMember] = {}
    for uid, username, full_name, pid, pname, role in rows:
        m = members.get(uid)
        if m is None:
            m = TeamMember(
                user_id=uid, username=username, full_name=full_name,
                open_tasks=task_counts.get(uid, 0) or 0,
                hosts_in_review=review_counts.get(uid, 0) or 0,
            )
            members[uid] = m
        m.projects.append(TeamMemberProject(project_id=pid, project_name=pname, role=role))

    for m in members.values():
        m.project_count = len(m.projects)
        m.projects.sort(key=lambda x: x.project_name.lower())

    roster = sorted(
        members.values(),
        key=lambda m: (-(m.open_tasks + m.hosts_in_review), (m.full_name or m.username).lower()),
    )
    return TeamResponse(members=roster, total_members=len(roster))
