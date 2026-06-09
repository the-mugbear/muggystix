from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, case, and_, or_, distinct, false
from app.db.session import get_db
from app.db import models
from app.db.models import FollowStatus, HostFollow
from app.db.models_vulnerability import Vulnerability
from app.db.models_agent import TestPlan, TestPlanEntry
from app.schemas.schemas import (
    DashboardStats,
    ScanSummary,
    SubnetStats,
    VulnerabilityStats,
    RiskInsightResponse,
    NoteActivitySummary,
    NoteActivityEntry,
    ReviewProgress,
)
from app.services.subnet_calculator import SubnetCalculator
from app.services.vulnerability_service import VulnerabilityService
from app.services.risk_insight_service import RiskInsightService
from app.services.host_follow_service import HostFollowService
# CR4-2 — the personal-work aggregations + their DTOs now live in a read
# service; these routes are thin wrappers over it.  Re-exported so any
# caller that imported the DTOs from this router keeps working.
from app.services.operations_read_service import (
    MyAttentionHost,
    MyAttentionResponse,
    TeamReviewHostRow,
    TeamReviewerGroup,
    TeamReviewResponse,
    MyTaskItem,
    MyTasksReasonCounts,
    MyTasksResponse,
    compute_my_attention_queue,
    compute_team_review,
    compute_my_tasks,
)
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

@router.get(
    "/port-stats",
    summary="Top 20 open ports across the project",
)
def get_port_statistics(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    # Get top 20 most common open ports scoped to project
    port_stats = (
        db.query(
            models.Port.port_number,
            models.Port.service_name,
            func.count(models.Port.id).label('count')
        )
        .join(models.Host, models.Port.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project.id,
            models.Port.state == 'open',
        )
        .group_by(models.Port.port_number, models.Port.service_name)
        .order_by(desc(func.count(models.Port.id)))
        .limit(20)
        .all()
    )

    return [
        {
            "port": stat.port_number,
            "service": stat.service_name or "unknown",
            "count": stat.count
        }
        for stat in port_stats
    ]

@router.get(
    "/os-stats",
    summary="OS distribution across the project",
)
def get_os_statistics(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    # Get operating system distribution scoped to project
    os_stats = (
        db.query(
            models.Host.os_name,
            func.count(models.Host.id).label('count')
        )
        .filter(
            models.Host.project_id == project.id,
            models.Host.os_name.isnot(None),
        )
        .group_by(models.Host.os_name)
        .order_by(desc(func.count(models.Host.id)))
        .limit(10)
        .all()
    )

    return [
        {
            "os": stat.os_name,
            "count": stat.count
        }
        for stat in os_stats
    ]


@router.get(
    "/risk-insights",
    response_model=RiskInsightResponse,
    summary="Risk insights — top hosts, ports of interest, vulnerability hotspots",
)
def get_risk_insights(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    # v2.86.4 — top-N style endpoint; bound at 50 so a stray call can't
    # ask for all insights at once.
    limit: int = Query(10, ge=1, le=50),
):
    service = RiskInsightService(db)
    return service.generate_insights(limit=limit, project_id=project.id)


# ---------------------------------------------------------------------------
# Personal "My Queue" — replaces the project-wide highest-risk Attention
# Queue widget on the dashboard.  Each user sees only the hosts they have
# personally marked **In Review** via the host follow feature.
#
# Watching is intentionally excluded: it represents passive interest
# ("I want to see what someone else is doing on this host"), not active
# work, and surfacing it on the queue widget would dilute "what do I
# need to do?" with "what am I keeping an eye on?".  Two analysts on
# the same project will see different rows here, reflecting their own
# in-flight work rather than a shared "highest-risk" board.
# ---------------------------------------------------------------------------

@router.get(
    "/my-attention",
    response_model=MyAttentionResponse,
    summary="My Queue — hosts I've marked In Review",
)
def get_my_attention_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    limit: int = Query(10, ge=1, le=50),
):
    """My Queue — see ``operations_read_service.compute_my_attention_queue``."""
    return compute_my_attention_queue(db, current_user, project, limit)


@router.get(
    "/team-review",
    response_model=TeamReviewResponse,
    summary="Team Review — who has which hosts In Review",
)
def get_team_review(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    limit: int = Query(
        500,
        ge=1,
        le=2000,
        description=(
            "Cap on follow rows returned.  ``total_hosts_in_review`` is "
            "computed in SQL and is unaffected by this cap, so the widget "
            "can still surface a correct 'showing N of T' figure even when "
            "the roster overflows."
        ),
    ),
):
    """Team Review roster — see ``operations_read_service.compute_team_review``."""
    return compute_team_review(db, current_user, project, limit)


@router.get(
    "/my-tasks",
    response_model=MyTasksResponse,
    summary="My Tasks — assigned + in-review + unassigned-triage test plan entries",
)
def get_my_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    limit: int = Query(15, ge=1, le=100),
):
    """My Tasks — see ``operations_read_service.compute_my_tasks``."""
    return compute_my_tasks(db, current_user, project, limit)


# ---------------------------------------------------------------------------
# "New scans since last visit" alert.
#
# The frontend stores its own "last dashboard visit" timestamp in
# localStorage and passes it to this endpoint as `since`.  The backend
# returns a count and the most recent filename so the dashboard can
# render an alert ("3 new scans uploaded since your last visit — open
# Latest Scan").  No DB schema change needed: the frontend owns the
# "I've seen up to" cursor, the backend just answers point queries.
# ---------------------------------------------------------------------------

class NewScansSinceResponse(BaseModel):
    count: int
    latest_scan_id: Optional[int] = None
    latest_scan_filename: Optional[str] = None
    latest_scan_created_at: Optional[datetime] = None


@router.get(
    "/new-scans-since",
    response_model=NewScansSinceResponse,
    summary="Count scans uploaded since a client-supplied timestamp",
)
def get_new_scans_since(
    since: Optional[datetime] = Query(
        None,
        description="ISO timestamp; only count scans created after this. Omit to count all scans.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Return how many scans have been uploaded to this project since
    a client-supplied timestamp.

    The client (Dashboard page) is responsible for tracking *its own*
    last-visit timestamp in localStorage and passing it here.  This
    keeps the alert scoped to "what's new since I last looked at the
    dashboard," independent of activity on other pages.
    """
    q = db.query(models.Scan).filter(models.Scan.project_id == project.id)
    if since is not None:
        q = q.filter(models.Scan.created_at > since)

    count = q.count()
    if count == 0:
        return NewScansSinceResponse(count=0)

    latest = q.order_by(desc(models.Scan.created_at)).first()
    return NewScansSinceResponse(
        count=count,
        latest_scan_id=latest.id if latest else None,
        latest_scan_filename=latest.filename if latest else None,
        latest_scan_created_at=latest.created_at if latest else None,
    )


# ---------------------------------------------------------------------------
# Scan staleness — "what needs re-scanning?".  Project-level age of the
# newest scan, plus per-scope freshness (newest host observation in the
# scope).  Drives the Operations "needs re-scan" tile and Scopes badges.
# ---------------------------------------------------------------------------

class ScopeStaleness(BaseModel):
    scope_id: int
    scope_name: str
    last_activity_at: Optional[datetime] = None
    days_since: Optional[int] = None
    is_stale: bool = False


class StalenessResponse(BaseModel):
    stale_days: int
    latest_scan_at: Optional[datetime] = None
    days_since_last_scan: Optional[int] = None
    project_is_stale: bool = False
    stale_scope_count: int = 0
    scopes: List[ScopeStaleness] = Field(default_factory=list)


def _days_since(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    # Host.last_seen / Scan.created_at are tz-aware; tolerate a naive value.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


@router.get(
    "/staleness",
    response_model=StalenessResponse,
    summary="Scan freshness — project + per-scope age, flags what needs re-scanning",
)
def get_staleness(
    stale_days: int = Query(14, ge=1, le=365, description="Age (days) past which a scope/project is 'stale'."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Report scan freshness for the project.

    ``latest_scan_at`` is the newest scan upload; a scope's
    ``last_activity_at`` is the newest ``Host.last_seen`` among hosts
    mapped into that scope.  Scopes with no discovered hosts (or whose
    newest observation is older than ``stale_days``) are flagged stale —
    i.e. they need a (re-)scan.
    """
    latest_scan_at = (
        db.query(func.max(models.Scan.created_at))
        .filter(models.Scan.project_id == project.id)
        .scalar()
    )

    # Per-scope newest host observation.  Outer joins so scopes with no
    # hosts still appear (last_activity None → stale → "needs recon").
    rows = (
        db.query(models.Scope.id, models.Scope.name, func.max(models.Host.last_seen))
        .select_from(models.Scope)
        .outerjoin(models.Subnet, models.Subnet.scope_id == models.Scope.id)
        .outerjoin(models.HostSubnetMapping, models.HostSubnetMapping.subnet_id == models.Subnet.id)
        .outerjoin(models.Host, models.Host.id == models.HostSubnetMapping.host_id)
        .filter(models.Scope.project_id == project.id)
        .group_by(models.Scope.id, models.Scope.name)
        .all()
    )

    scopes: List[ScopeStaleness] = []
    stale_count = 0
    for scope_id, scope_name, last_activity in rows:
        days = _days_since(last_activity)
        is_stale = last_activity is None or (days is not None and days > stale_days)
        if is_stale:
            stale_count += 1
        scopes.append(ScopeStaleness(
            scope_id=scope_id,
            scope_name=scope_name,
            last_activity_at=last_activity,
            days_since=days,
            is_stale=is_stale,
        ))

    # Stalest first (None = never = most stale), then by name.
    scopes.sort(key=lambda s: (-(s.days_since if s.days_since is not None else 10**9), s.scope_name or ""))

    days_since_last_scan = _days_since(latest_scan_at)
    project_is_stale = latest_scan_at is None or (
        days_since_last_scan is not None and days_since_last_scan > stale_days
    )

    return StalenessResponse(
        stale_days=stale_days,
        latest_scan_at=latest_scan_at,
        days_since_last_scan=days_since_last_scan,
        project_is_stale=project_is_stale,
        stale_scope_count=stale_count,
        scopes=scopes,
    )


# ---------------------------------------------------------------------------
# Network topology — project → scope → subnet graph for the topology view.
# Bounded by design: subnets carry host counts (not host-level nodes), and
# the subnet set is capped so the payload/render stay sane on large
# estates.  Host-level drill-down happens via deep-links into /hosts.
# ---------------------------------------------------------------------------

class TopoNode(BaseModel):
    id: str
    type: str  # project | scope | subnet | unscoped
    label: str
    host_count: int = 0
    meta: dict = Field(default_factory=dict)


class TopoEdge(BaseModel):
    id: str
    source: str
    target: str


class TopologyResponse(BaseModel):
    nodes: List[TopoNode] = Field(default_factory=list)
    edges: List[TopoEdge] = Field(default_factory=list)
    truncated: bool = False


_TOPO_SUBNET_CAP = 500


@router.get(
    "/topology",
    response_model=TopologyResponse,
    summary="Network topology graph — project → scope → subnet (host counts)",
)
def get_topology(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Hierarchical graph for the topology view.

    Returns a project root, one node per scope, and one node per subnet
    (labelled with its host count + critical-host count for risk tinting).
    Subnets are capped at 500; an "unscoped" node aggregates hosts not
    mapped to any subnet.  No host-level nodes — drill-down is via
    deep-links into the filtered Hosts page.
    """
    nodes: List[TopoNode] = [
        TopoNode(id="project", type="project", label=project.name, meta={"project_id": project.id})
    ]
    edges: List[TopoEdge] = []

    scopes = (
        db.query(models.Scope.id, models.Scope.name)
        .filter(models.Scope.project_id == project.id)
        .order_by(models.Scope.name)
        .all()
    )
    for scope_id, scope_name in scopes:
        nodes.append(TopoNode(
            id=f"scope-{scope_id}", type="scope", label=scope_name or f"Scope {scope_id}",
            meta={"scope_id": scope_id},
        ))
        edges.append(TopoEdge(id=f"e-project-scope-{scope_id}", source="project", target=f"scope-{scope_id}"))

    # Subnets + host counts (one grouped query), capped.  Project isolation:
    # we count DISTINCT in-project hosts, with the ``Host.project_id`` predicate
    # in the join ON clause (not WHERE) so subnets with zero in-project hosts
    # still appear with count 0, and a stray cross-project host-subnet mapping
    # (overlapping CIDRs) can never inflate another project's count.
    subnet_rows = (
        db.query(
            models.Subnet.id,
            models.Subnet.cidr,
            models.Subnet.scope_id,
            func.count(func.distinct(models.Host.id)),
        )
        .select_from(models.Subnet)
        .join(models.Scope, models.Subnet.scope_id == models.Scope.id)
        .outerjoin(models.HostSubnetMapping, models.HostSubnetMapping.subnet_id == models.Subnet.id)
        .outerjoin(
            models.Host,
            and_(
                models.Host.id == models.HostSubnetMapping.host_id,
                models.Host.project_id == project.id,
            ),
        )
        .filter(models.Scope.project_id == project.id)
        .group_by(models.Subnet.id, models.Subnet.cidr, models.Subnet.scope_id)
        .order_by(func.count(func.distinct(models.Host.id)).desc())
        .limit(_TOPO_SUBNET_CAP + 1)
        .all()
    )
    truncated = len(subnet_rows) > _TOPO_SUBNET_CAP
    subnet_rows = subnet_rows[:_TOPO_SUBNET_CAP]
    subnet_ids = [r[0] for r in subnet_rows]

    # Critical-host count per subnet (one query) for risk tinting.
    critical_map: dict = {}
    if subnet_ids:
        for sid, crit in (
            db.query(
                models.HostSubnetMapping.subnet_id,
                func.count(func.distinct(Vulnerability.host_id)),
            )
            # Same project isolation: only in-project hosts count toward a
            # subnet's critical tally.
            .join(models.Host, models.Host.id == models.HostSubnetMapping.host_id)
            .join(Vulnerability, Vulnerability.host_id == models.HostSubnetMapping.host_id)
            .filter(
                models.HostSubnetMapping.subnet_id.in_(subnet_ids),
                models.Host.project_id == project.id,
                Vulnerability.severity == "CRITICAL",
            )
            .group_by(models.HostSubnetMapping.subnet_id)
            .all()
        ):
            critical_map[sid] = int(crit or 0)

    for subnet_id, cidr, scope_id, host_count in subnet_rows:
        nodes.append(TopoNode(
            id=f"subnet-{subnet_id}",
            type="subnet",
            label=cidr or f"Subnet {subnet_id}",
            host_count=int(host_count or 0),
            meta={"subnet_id": subnet_id, "cidr": cidr, "scope_id": scope_id,
                  "critical_hosts": critical_map.get(subnet_id, 0)},
        ))
        edges.append(TopoEdge(
            id=f"e-scope-{scope_id}-subnet-{subnet_id}",
            source=f"scope-{scope_id}",
            target=f"subnet-{subnet_id}",
        ))

    # Unscoped = in-project hosts with no mapping to a subnet in *this
    # project's* scopes.  "Any mapping at all" would be wrong: a host
    # mapped only to a foreign subnet (overlapping-CIDR corruption) would
    # then vanish from both the subnet counts (project-isolated above) and
    # this node — disappearing from its own project's topology entirely.
    scoped_host_ids = (
        db.query(models.HostSubnetMapping.host_id)
        .join(models.Subnet, models.Subnet.id == models.HostSubnetMapping.subnet_id)
        .join(models.Scope, models.Scope.id == models.Subnet.scope_id)
        .filter(models.Scope.project_id == project.id)
        .scalar_subquery()
    )
    unscoped_count = (
        db.query(func.count(func.distinct(models.Host.id)))
        .filter(
            models.Host.project_id == project.id,
            ~models.Host.id.in_(scoped_host_ids),
        )
        .scalar()
    ) or 0
    if unscoped_count:
        nodes.append(TopoNode(id="unscoped", type="unscoped", label="Out of scope", host_count=int(unscoped_count)))
        edges.append(TopoEdge(id="e-project-unscoped", source="project", target="unscoped"))

    return TopologyResponse(nodes=nodes, edges=edges, truncated=truncated)
