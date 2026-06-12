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
from sqlalchemy import func, text, distinct
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.db.models import HostFollow, FollowStatus
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership
from app.db.models_agent import (
    TestPlan, TestPlanEntry, ExecutionSession, ReconSession,
)
from app.services.agent_session_metrics import blocked_exec_session_counts
from app.api.v1.endpoints.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

STALE_THRESHOLD_DAYS = 14


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class VulnSummaryBrief(BaseModel):
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
    vuln_summary: VulnSummaryBrief = VulnSummaryBrief()
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
    # SOC-P3 governance
    has_admin: bool = True
    admins: List[str] = []


class PortfolioSummary(BaseModel):
    total_projects: int = 0
    active_projects: int = 0
    total_hosts: int = 0
    total_open_ports: int = 0
    total_scans: int = 0
    total_unreviewed: int = 0
    # P4 attention rollups across the visible portfolio.
    projects_requiring_attention: int = 0
    projects_with_critical: int = 0
    stale_projects: int = 0
    projects_no_data: int = 0
    pending_approvals_total: int = 0
    blocked_sessions_total: int = 0
    projects_without_admin: int = 0


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

    # Host counts
    host_counts = dict(
        db.query(models.Host.project_id, func.count(models.Host.id))
        .filter(models.Host.project_id.in_(project_ids))
        .group_by(models.Host.project_id)
        .all()
    )
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

    # Scan counts and last scan time
    scan_stats = dict(
        db.query(
            models.Scan.project_id,
            func.count(models.Scan.id),
        )
        .filter(models.Scan.project_id.in_(project_ids))
        .group_by(models.Scan.project_id)
        .all()
    )
    last_scans = dict(
        db.query(
            models.Scan.project_id,
            func.max(models.Scan.created_at),
        )
        .filter(models.Scan.project_id.in_(project_ids))
        .group_by(models.Scan.project_id)
        .all()
    )

    # Review progress: count of reviewed hosts per project
    reviewed_counts = dict(
        db.query(models.HostFollow.host_id, models.HostFollow.status)
        # We need per-project, so join through host
        .join(models.Host, models.HostFollow.host_id == models.Host.id)
        .filter(models.Host.project_id.in_(project_ids))
        .with_entities(
            models.Host.project_id,
            func.count(func.distinct(models.HostFollow.host_id)),
        )
        .filter(models.HostFollow.status == "reviewed")
        .group_by(models.Host.project_id)
        .all()
    )

    # Vulnerability counts by severity per project
    # Cast the PG enum to text before lower() to avoid enum type mismatch
    vuln_rows = (
        db.query(
            models.Host.project_id,
            func.lower(text("vulnerabilities.severity::text")),
            func.count(),
        )
        .select_from(models.Host)
        .join(models.Host.vulnerabilities)
        .filter(models.Host.project_id.in_(project_ids))
        .group_by(models.Host.project_id, text("2"))
        .all()
    )
    vuln_map: Dict[int, VulnSummaryBrief] = {}
    for pid, sev, cnt in vuln_rows:
        if pid not in vuln_map:
            vuln_map[pid] = VulnSummaryBrief()
        if sev in ("critical",):
            vuln_map[pid].critical += cnt
        elif sev in ("high",):
            vuln_map[pid].high += cnt
        elif sev in ("medium",):
            vuln_map[pid].medium += cnt
        elif sev in ("low",):
            vuln_map[pid].low += cnt

    # ------------------------------------------------------------------
    # P4 control-plane signals — all batched (one GROUP BY each).
    # ------------------------------------------------------------------

    # Pending plan reviews (proposed plans) per project.
    pending_review_counts = dict(
        db.query(TestPlan.project_id, func.count(TestPlan.id))
        .filter(TestPlan.project_id.in_(project_ids), TestPlan.status == "proposed")
        .group_by(TestPlan.project_id)
        .all()
    )

    # Open tasks (non-terminal entries on accepted plans) per project.
    open_task_counts = dict(
        db.query(TestPlan.project_id, func.count(TestPlanEntry.id))
        .join(TestPlanEntry, TestPlanEntry.test_plan_id == TestPlan.id)
        .filter(
            TestPlan.project_id.in_(project_ids),
            TestPlan.status.in_(("approved", "in_progress", "completed")),
            TestPlanEntry.status.in_(("proposed", "approved", "in_progress")),
        )
        .group_by(TestPlan.project_id)
        .all()
    )

    # Active execution + recon sessions, and blocked (paused/failed) execs.
    # ExecutionSession is scoped by test_plan_id → join TestPlan for project.
    active_exec_counts = dict(
        db.query(TestPlan.project_id, func.count(ExecutionSession.id))
        .join(ExecutionSession, ExecutionSession.test_plan_id == TestPlan.id)
        .filter(
            TestPlan.project_id.in_(project_ids),
            ExecutionSession.status == "active",
        )
        .group_by(TestPlan.project_id)
        .all()
    )
    # Only the LATEST execution session per plan counts as "blocked" (paused /
    # failed) — shared with Security Posture so the two surfaces agree on the
    # invariant.  See agent_session_metrics for the rationale.
    blocked_exec_counts = blocked_exec_session_counts(db, project_ids)
    active_recon_counts = dict(
        db.query(ReconSession.project_id, func.count(ReconSession.id))
        .filter(
            ReconSession.project_id.in_(project_ids),
            ReconSession.status == "active",
        )
        .group_by(ReconSession.project_id)
        .all()
    )

    # Member counts + the caller's per-project role.
    member_counts = dict(
        db.query(ProjectMembership.project_id, func.count(ProjectMembership.id))
        .filter(ProjectMembership.project_id.in_(project_ids))
        .group_by(ProjectMembership.project_id)
        .all()
    )
    my_roles = dict(
        db.query(ProjectMembership.project_id, ProjectMembership.role)
        .filter(
            ProjectMembership.project_id.in_(project_ids),
            ProjectMembership.user_id == current_user.id,
        )
        .all()
    )

    # SOC-P3 governance — admin members per project (names), batched.  A
    # project with no admin is a governance risk (no one can manage its
    # membership), surfaced as the ``no_admin`` attention reason.
    from app.db.models_auth import User as _User
    admin_rows = (
        db.query(ProjectMembership.project_id, _User.full_name, _User.username)
        .join(_User, _User.id == ProjectMembership.user_id)
        .filter(
            ProjectMembership.project_id.in_(project_ids),
            ProjectMembership.role == "admin",
        )
        .all()
    )
    admins_map: Dict[int, List[str]] = {}
    for pid, full_name, username in admin_rows:
        admins_map.setdefault(pid, []).append(full_name or username)

    # ------------------------------------------------------------------
    # Build response
    # ------------------------------------------------------------------

    cards: List[ProjectCard] = []
    total_hosts = 0
    total_open_ports = 0
    total_scans = 0
    total_unreviewed = 0
    active_projects = 0
    projects_requiring_attention = 0
    projects_with_critical = 0
    stale_projects = 0
    projects_no_data = 0
    pending_approvals_total = 0
    blocked_sessions_total = 0
    projects_without_admin = 0

    for p in projects:
        hc = host_counts.get(p.id, 0)
        uhc = up_host_counts.get(p.id, 0)
        opc = open_port_counts.get(p.id, 0)
        sc = scan_stats.get(p.id, 0)
        ls = last_scans.get(p.id)
        rc = reviewed_counts.get(p.id, 0)
        vs = vuln_map.get(p.id, VulnSummaryBrief())

        unreviewed = max(0, hc - rc)
        review_pct = round((rc / hc) * 100, 1) if hc else 0.0

        days_since: Optional[int] = None
        is_stale = False
        if ls:
            ls_naive = ls.replace(tzinfo=None) if ls.tzinfo else ls
            now_naive = now.replace(tzinfo=None)
            days_since = (now_naive - ls_naive).days
            is_stale = days_since >= STALE_THRESHOLD_DAYS

        # Health indicator
        if vs.critical > 0:
            health = "critical"
        elif vs.high > 0 or (hc > 0 and review_pct < 50):
            health = "warning"
        elif is_stale:
            health = "stale"
        else:
            health = "healthy"

        if p.status in ("active", "in_progress"):
            active_projects += 1

        pending_reviews = pending_review_counts.get(p.id, 0)
        open_tasks = open_task_counts.get(p.id, 0)
        active_sessions = active_exec_counts.get(p.id, 0) + active_recon_counts.get(p.id, 0)
        blocked_sessions = blocked_exec_counts.get(p.id, 0)
        member_count = member_counts.get(p.id, 0)
        # Global admins may have no per-project membership row; surface
        # their global role so the table never shows a blank for them.
        role = my_roles.get(p.id)
        if role is None and current_user.role == UserRole.ADMIN:
            role = "admin"
        admins = admins_map.get(p.id, [])
        has_admin = len(admins) > 0

        # Attention reasons — a project can trip several at once.  Stable
        # codes; the frontend maps them to labels + row actions.
        reasons: List[str] = []
        if vs.critical > 0:
            reasons.append("critical_findings")
        if vs.high > 0:
            reasons.append("high_findings")
        if pending_reviews > 0:
            reasons.append("pending_review")
        if blocked_sessions > 0:
            reasons.append("blocked_session")
        if not has_admin:
            reasons.append("no_admin")  # SOC-P3 governance risk
        if is_stale:
            reasons.append("stale")
        if hc == 0:
            reasons.append("no_data")
        elif review_pct < 50:
            reasons.append("unreviewed")

        total_hosts += hc
        total_open_ports += opc
        total_scans += sc
        total_unreviewed += unreviewed
        if reasons:
            projects_requiring_attention += 1
        if vs.critical > 0:
            projects_with_critical += 1
        if is_stale:
            stale_projects += 1
        if hc == 0:
            projects_no_data += 1
        pending_approvals_total += pending_reviews
        blocked_sessions_total += blocked_sessions
        if not has_admin:
            projects_without_admin += 1

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
            last_scan_at=ls,
            days_since_last_scan=days_since,
            is_stale=is_stale,
            review_progress_pct=review_pct,
            unreviewed_hosts=unreviewed,
            vuln_summary=vs,
            health=health,
            attention_reasons=reasons,
            pending_plan_reviews=pending_reviews,
            open_tasks=open_tasks,
            active_sessions=active_sessions,
            blocked_sessions=blocked_sessions,
            member_count=member_count,
            user_role=role,
            has_admin=has_admin,
            admins=admins,
        ))

    return PortfolioDashboardResponse(
        summary=PortfolioSummary(
            total_projects=len(projects),
            active_projects=active_projects,
            total_hosts=total_hosts,
            total_open_ports=total_open_ports,
            total_scans=total_scans,
            total_unreviewed=total_unreviewed,
            projects_requiring_attention=projects_requiring_attention,
            projects_with_critical=projects_with_critical,
            stale_projects=stale_projects,
            projects_no_data=projects_no_data,
            pending_approvals_total=pending_approvals_total,
            blocked_sessions_total=blocked_sessions_total,
            projects_without_admin=projects_without_admin,
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
