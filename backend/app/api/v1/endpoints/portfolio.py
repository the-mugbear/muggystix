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
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership
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


class PortfolioSummary(BaseModel):
    total_projects: int = 0
    active_projects: int = 0
    total_hosts: int = 0
    total_open_ports: int = 0
    total_scans: int = 0
    total_unreviewed: int = 0


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
    # Build response
    # ------------------------------------------------------------------

    cards: List[ProjectCard] = []
    total_hosts = 0
    total_open_ports = 0
    total_scans = 0
    total_unreviewed = 0
    active_projects = 0

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

        total_hosts += hc
        total_open_ports += opc
        total_scans += sc
        total_unreviewed += unreviewed

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
        ))

    return PortfolioDashboardResponse(
        summary=PortfolioSummary(
            total_projects=len(projects),
            active_projects=active_projects,
            total_hosts=total_hosts,
            total_open_ports=total_open_ports,
            total_scans=total_scans,
            total_unreviewed=total_unreviewed,
        ),
        projects=cards,
    )
