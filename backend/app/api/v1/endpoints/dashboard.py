from typing import Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, case
from app.db.session import get_db
from app.db import models
from app.schemas.schemas import (
    DashboardStats,
    ScanSummary,
    SubnetStats,
    VulnerabilityStats,
    NoteActivitySummary,
    NoteActivityEntry,
    ReviewProgress,
)
from app.services.subnet_calculator import SubnetCalculator
from app.services.vulnerability_service import VulnerabilityService
from app.services.host_follow_service import HostFollowService
# v2.244.0 — the personal-work routes (my-tasks / my-attention / team-review /
# new-scans-since) were removed: GET /workbench batches all four from the same
# operations_read_service functions and is what the Operations page calls.  The
# DTO re-exports went with them; nothing imported them from this module.
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project
from app.db.models_auth import User
from app.db.models_project import Project
import logging

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

@router.get(
    "/stats",
    response_model=DashboardStats,
    summary="Project-wide aggregate statistics",
)
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    # Get total counts scoped to this project
    total_scans = db.query(func.count(models.Scan.id)).filter(
        models.Scan.project_id == project.id
    ).scalar() or 0
    total_hosts = db.query(func.count(func.distinct(models.Host.ip_address))).filter(
        models.Host.project_id == project.id
    ).scalar() or 0
    total_ports = (
        db.query(func.count(models.Port.id))
        .join(models.Host, models.Port.host_id == models.Host.id)
        .filter(models.Host.project_id == project.id)
        .scalar() or 0
    )
    total_subnets = (
        db.query(func.count(models.Subnet.id))
        .join(models.Scope, models.Subnet.scope_id == models.Scope.id)
        .filter(models.Scope.project_id == project.id)
        .scalar() or 0
    )

    # Get overall up hosts and open ports counts
    up_hosts = db.query(func.count(func.distinct(models.Host.ip_address))).filter(
        models.Host.project_id == project.id,
        models.Host.state == 'up',
    ).scalar() or 0

    open_ports = (
        db.query(func.count(models.Port.id))
        .join(models.Host, models.Port.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project.id,
            models.Port.state == 'open',
        )
        .scalar() or 0
    )

    # Get recent scans (last 10) with host and port counts
    recent_scans_query = (
        db.query(models.Scan)
        .filter(models.Scan.project_id == project.id)
        .order_by(desc(models.Scan.created_at))
        .limit(10)
    )

    recent_results = recent_scans_query.all()
    scan_ids = [r.id for r in recent_results]

    # Batched stat lookup — replaces 4 queries × N scans (40 queries
    # for the default 10-scan dashboard) with two GROUP BY queries
    # that return the same data shape.  ``case(...)`` lets a single
    # row carry both the total and the "matching state" subtotal so
    # the loop doesn't have to issue a second count.
    host_stats_by_scan: dict[int, tuple[int, int]] = {}
    port_stats_by_scan: dict[int, tuple[int, int]] = {}
    if scan_ids:
        host_rows = (
            db.query(
                models.HostScanHistory.scan_id,
                func.count(models.HostScanHistory.host_id).label("total"),
                func.sum(
                    case(
                        (models.HostScanHistory.state_at_scan == "up", 1),
                        else_=0,
                    )
                ).label("up"),
            )
            .filter(models.HostScanHistory.scan_id.in_(scan_ids))
            .group_by(models.HostScanHistory.scan_id)
            .all()
        )
        host_stats_by_scan = {r.scan_id: (int(r.total or 0), int(r.up or 0)) for r in host_rows}

        port_rows = (
            db.query(
                models.PortScanHistory.scan_id,
                func.count(models.PortScanHistory.port_id).label("total"),
                func.sum(
                    case(
                        (models.PortScanHistory.state_at_scan == "open", 1),
                        else_=0,
                    )
                ).label("open"),
            )
            .filter(models.PortScanHistory.scan_id.in_(scan_ids))
            .group_by(models.PortScanHistory.scan_id)
            .all()
        )
        port_stats_by_scan = {r.scan_id: (int(r.total or 0), int(r.open or 0)) for r in port_rows}

    recent_scans = []
    for result in recent_results:
        # Per-scan counts — distinct local names so they do NOT clobber the
        # project-wide total_hosts/up_hosts/total_ports/open_ports computed
        # above and returned in DashboardStats below.
        scan_total_hosts, scan_up_hosts = host_stats_by_scan.get(result.id, (0, 0))
        scan_total_ports, scan_open_ports = port_stats_by_scan.get(result.id, (0, 0))
        recent_scans.append(ScanSummary(
            id=result.id,
            filename=result.filename,
            scan_type=result.scan_type,
            created_at=result.created_at,
            total_hosts=scan_total_hosts,
            up_hosts=scan_up_hosts,
            total_ports=scan_total_ports,
            open_ports=scan_open_ports,
        ))


    # Get enhanced subnet statistics with calculations
    subnet_stats = []

    try:
        # Get basic subnet info with scope names, scoped to project
        subnets = (
            db.query(models.Subnet)
            .join(models.Scope)
            # Eager-load scope so `subnet.scope.name` in the loop below
            # doesn't fire one lazy SELECT per subnet (the host-count
            # lookup right below was de-N+1'd in v2.85.0; this access was
            # missed).
            .options(joinedload(models.Subnet.scope))
            .filter(models.Scope.project_id == project.id)
            .limit(20)
            .all()
        )

        # v2.85.0 — batch the host-count lookup.  Pre-v2.85.0 this loop
        # fired one COUNT() per subnet (20 extra queries every dashboard
        # hit, more if the 20-row cap is later raised).  One GROUP BY
        # against the IN(...) of subnet ids is now sufficient.
        subnet_ids = [s.id for s in subnets]
        host_count_map: Dict[int, int] = {}
        if subnet_ids:
            host_count_map = dict(
                db.query(
                    models.HostSubnetMapping.subnet_id,
                    func.count(models.HostSubnetMapping.id),
                )
                .filter(models.HostSubnetMapping.subnet_id.in_(subnet_ids))
                .group_by(models.HostSubnetMapping.subnet_id)
                .all()
            )

        for subnet in subnets:
            host_count = host_count_map.get(subnet.id, 0)

            # Calculate subnet metrics using the new calculator
            metrics = SubnetCalculator.calculate_subnet_metrics(subnet.cidr)
            utilization = SubnetCalculator.calculate_utilization_percentage(host_count, subnet.cidr)
            risk_info = SubnetCalculator.get_subnet_risk_level(utilization, host_count)

            subnet_stats.append(SubnetStats(
                id=subnet.id,
                cidr=subnet.cidr,
                scope_name=subnet.scope.name,
                description=subnet.description,
                host_count=host_count,
                total_addresses=metrics['total_addresses'],
                usable_addresses=metrics['usable_addresses'],
                utilization_percentage=round(utilization, 2),
                risk_level=risk_info['risk_level'],
                network_address=metrics['network_address'],
                is_private=metrics['is_private']
            ))

        # Sort by utilization percentage descending, then by host count
        subnet_stats.sort(key=lambda x: (x.utilization_percentage, x.host_count), reverse=True)

    except Exception as e:
        logger.error(f"Error calculating subnet statistics: {e}")
        subnet_stats = []

    # Get vulnerability statistics
    vulnerability_stats = None
    try:
        vulnerability_service = VulnerabilityService(db)
        vuln_data = vulnerability_service.get_dashboard_statistics(project_id=project.id)
        vulnerability_stats = VulnerabilityStats(
            total_vulnerabilities=vuln_data['total_vulnerabilities'],
            critical=vuln_data['severity_breakdown'].get('critical', 0),
            high=vuln_data['severity_breakdown'].get('high', 0),
            medium=vuln_data['severity_breakdown'].get('medium', 0),
            low=vuln_data['severity_breakdown'].get('low', 0),
            info=vuln_data['severity_breakdown'].get('info', 0),
            hosts_with_vulnerabilities=vuln_data['hosts_with_vulnerabilities']
        )
    except Exception as e:
        logger.error(f"Error getting vulnerability statistics: {e}")

    note_activity = None
    try:
        follow_service = HostFollowService(db)
        activity_data = follow_service.get_dashboard_activity(current_user.id, limit=6, project_id=project.id)
        rp = activity_data.get("review_progress")
        review_progress = ReviewProgress(**rp) if rp else None
        note_activity = NoteActivitySummary(
            total_notes=activity_data["total_notes"],
            active_host_count=activity_data["active_host_count"],
            following_count=activity_data["following_count"],
            review_progress=review_progress,
            recent_notes=[
                NoteActivityEntry(
                    note_id=item["note_id"],
                    host_id=item["host_id"],
                    ip_address=item["ip_address"],
                    hostname=item["hostname"],
                    status=item["status"],
                    preview=item["preview"],
                    created_at=item["created_at"],
                    updated_at=item["updated_at"],
                )
                for item in activity_data["recent_notes"]
            ],
        )
    except Exception as e:
        logger.error(f"Error gathering note activity: {e}")

    return DashboardStats(
        total_scans=total_scans,
        total_hosts=total_hosts,
        total_ports=total_ports,
        up_hosts=up_hosts,
        open_ports=open_ports,
        total_subnets=total_subnets,
        recent_scans=recent_scans,
        subnet_stats=subnet_stats,
        vulnerability_stats=vulnerability_stats,
        note_activity=note_activity,
    )
