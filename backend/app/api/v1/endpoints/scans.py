import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, case, distinct, and_, text, or_
from pydantic import BaseModel, Field

from app.db.session import get_db
from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.schemas.pagination import Paginated
from app.schemas.schemas import (
    Scan,
    ScanSummary,
    ScanPortBreakdown,
    ScanVulnerabilitySummary,
    OutOfScopeHost,
    DNSRecord,
)
from app.services.command_explanation_service import CommandExplanationService
from app.api.v1.endpoints.auth import get_current_user, require_role
from app.api.deps import get_current_project, require_project_role
from app.db.models_auth import UserRole, User
from app.db.models_project import Project, ProjectRole

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


# --- Response schemas for untyped endpoints ---

class PurgeResponse(BaseModel):
    purged: int = Field(..., description="Number of records deleted")


class MessageResponse(BaseModel):
    message: str


class DeleteScanResponse(BaseModel):
    message: str
    hosts_removed: int = Field(..., ge=0, description="Number of orphaned hosts removed")


class ScanDeletionImpact(BaseModel):
    """Preview of exactly what deleting a scan removes.

    Because hosts are deduplicated per-IP-per-project (one Host row shared
    across every scan that observed the IP), deleting a scan does NOT delete
    all the hosts it touched — only those seen by no other scan ("orphans").
    Hosts also seen by other scans survive; their provenance is re-pointed.
    This preview lets the delete modal tell the truth instead of implying a
    blanket wipe.
    """

    scan_id: int
    filename: str
    hosts_removed: int = Field(..., ge=0, description="Hosts seen ONLY by this scan; deleted")
    hosts_kept: int = Field(..., ge=0, description="Hosts also seen by other scans; kept, re-pointed")
    sample_removed_ips: List[str] = Field(
        default_factory=list, description="Up to 10 IPs of the hosts that will be removed"
    )
    ports_removed: int = Field(..., ge=0, description="Open ports on the removed (orphan) hosts")
    vulnerabilities_removed: int = Field(..., ge=0, description="Vulnerabilities recorded by this scan")
    web_interfaces_removed: int = Field(..., ge=0, description="Web interfaces/screenshots from this scan")


class CountResponse(BaseModel):
    total: int = Field(..., ge=0, description="Total count")


class ScanInventorySummary(BaseModel):
    """Filter-aware totals for the /scans page headline cards.

    The list endpoint is paginated ("Load more"), so summing the loaded
    page client-side under-reports once a project has more scans than one
    page holds.  This carries the true totals across *all* scans matching
    the active filters, independent of pagination.
    """

    total_scans: int = Field(..., ge=0, description="Scans matching the filters")
    total_hosts: int = Field(..., ge=0, description="Sum of host observations across matching scans")
    up_hosts: int = Field(..., ge=0, description="Sum of up-host observations across matching scans")
    open_services: int = Field(..., ge=0, description="Sum of open ports across matching scans")


class CommandArgument(BaseModel):
    arg: str
    description: str
    category: Optional[str] = None
    risk_level: Optional[str] = None
    examples: Optional[list] = None


class CommandExplanationResponse(BaseModel):
    has_command: bool
    tool: Optional[str] = None
    command: Optional[str] = None
    target: Optional[str] = None
    scan_type: Optional[str] = None
    summary: Optional[str] = None
    risk_assessment: Optional[str] = None
    arguments: Optional[List[CommandArgument]] = None
    message: Optional[str] = None


# --- Scan-diff (attack-surface delta) schemas ---

class ScanDiffSide(BaseModel):
    scan_id: int
    filename: str
    tool_name: Optional[str] = None
    scan_type: Optional[str] = None
    created_at: Optional[datetime] = None
    total_hosts: int = 0
    up_hosts: int = 0
    total_ports: int = 0
    open_ports: int = 0


class ScanDiffHostRow(BaseModel):
    host_id: int
    ip_address: str
    hostname: Optional[str] = None


class ScanDiffHostStateChange(BaseModel):
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    state_a: Optional[str] = None
    state_b: Optional[str] = None


class ScanDiffPortChange(BaseModel):
    host_id: int
    ip_address: str
    port_number: int
    protocol: Optional[str] = None
    service_name: Optional[str] = None
    state_a: Optional[str] = None
    state_b: Optional[str] = None


class ScanDiffCounts(BaseModel):
    new_hosts: int = 0
    dropped_hosts: int = 0
    host_state_changes: int = 0
    newly_open_ports: int = 0
    closed_ports: int = 0


class ScanDiffResponse(BaseModel):
    scan_a: ScanDiffSide
    scan_b: ScanDiffSide
    counts: ScanDiffCounts
    # Lists below are capped at row_cap; `counts` carries exact totals.
    row_cap: int
    new_hosts: List[ScanDiffHostRow] = Field(default_factory=list)
    dropped_hosts: List[ScanDiffHostRow] = Field(default_factory=list)
    host_state_changes: List[ScanDiffHostStateChange] = Field(default_factory=list)
    newly_open_ports: List[ScanDiffPortChange] = Field(default_factory=list)
    closed_ports: List[ScanDiffPortChange] = Field(default_factory=list)


_AUTH_RESPONSES = {
    401: {"description": "Not authenticated"},
}

_ADMIN_RESPONSES = {
    401: {"description": "Not authenticated"},
    403: {"description": "Insufficient permissions — admin role required"},
}

def _apply_scan_inventory_filters(query, *, search, tool, created_after):
    """Apply the /scans page's search / tool / date-range filters.

    Shared by the list endpoint and the summary endpoint so the headline
    totals can never drift from the rows the table shows.  Assumes
    ``models.Scan`` is part of the query's FROM clause.
    """
    if search and search.strip():
        needle = f"%{search.strip()}%"
        query = query.filter(
            (models.Scan.filename.ilike(needle))
            | (models.Scan.tool_name.ilike(needle))
            | (models.Scan.scan_type.ilike(needle))
        )
    if tool and tool.strip():
        tool_lower = tool.strip().lower()
        query = query.filter(
            (func.lower(models.Scan.tool_name) == tool_lower)
            | (func.lower(models.Scan.scan_type) == tool_lower)
        )
    if created_after is not None:
        query = query.filter(models.Scan.created_at >= created_after)
    return query


@router.get("/", response_model=List[ScanSummary])
def get_scans(
    # v2.86.4 — pagination caps added (was bare ``int = 100`` with no
    # upper bound).
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    search: Optional[str] = Query(
        None, max_length=200,
        description=(
            "Case-insensitive substring match across filename, tool_name, "
            "and scan_type.  Added v2.43.0 so the CommandPalette can pass a "
            "user query straight to the server instead of fetching the full "
            "list and client-side filtering."
        ),
    ),
    tool: Optional[str] = Query(
        None, max_length=64,
        description=(
            "Filter to scans of a given tool — case-insensitive exact "
            "match on Scan.tool_name OR Scan.scan_type.  Drives the "
            "clickable tool-count chips on the /scans page (v2.82.0)."
        ),
    ),
    created_after: Optional[datetime] = Query(
        None,
        description=(
            "Only return scans with Scan.created_at >= this ISO timestamp. "
            "Drives the date-range chips on the /scans page (v2.83.0)."
        ),
    ),
    sort_by: Optional[str] = Query(
        None,
        pattern="^(created_at|filename|tool_name|total_hosts|new_hosts)$",
        description=(
            "Sort column for the inventory.  Allowed: created_at "
            "(default), filename, tool_name, total_hosts, new_hosts.  Drives "
            "the sortable column headers on the /scans desktop table "
            "(v2.83.0)."
        ),
    ),
    sort_order: Optional[str] = Query(
        "desc",
        pattern="^(asc|desc)$",
        description="Sort direction — asc or desc.  Defaults to desc.",
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    # Get scans with summary statistics using v2 audit tables
    scans_query = (
        db.query(
            models.Scan.id,
            models.Scan.filename,
            models.Scan.scan_type,
            models.Scan.tool_name,
            models.Scan.created_at,
            models.Scan.start_time,
            models.Scan.end_time,
            models.Scan.command_line,
            models.Scan.version,
            models.Scan.uploaded_by_id,
            func.count(models.HostScanHistory.id).label('total_hosts'),
            func.sum(case((models.HostScanHistory.state_at_scan == 'up', 1), else_=0)).label('up_hosts'),
            # Hosts this scan first discovered (created the row) — the rest of
            # its observed hosts were already known and got updated.
            func.sum(case((models.HostScanHistory.host_created == True, 1), else_=0)).label('new_hosts'),  # noqa: E712
        )
        .select_from(models.Scan)
        .outerjoin(models.HostScanHistory, models.Scan.id == models.HostScanHistory.scan_id)
        .filter(models.Scan.project_id == project.id)
    )
    # v2.82.0 tool filter / v2.83.0 date-range filter / search — all shared
    # with the summary endpoint via _apply_scan_inventory_filters so the
    # headline totals can't drift from the rows shown here.
    scans_query = _apply_scan_inventory_filters(
        scans_query, search=search, tool=tool, created_after=created_after
    )
    scans_query = (
        scans_query
        .group_by(
            models.Scan.id,
            models.Scan.filename,
            models.Scan.scan_type,
            models.Scan.tool_name,
            models.Scan.created_at,
            models.Scan.start_time,
            models.Scan.end_time,
            models.Scan.command_line,
            models.Scan.version,
            models.Scan.uploaded_by_id,
        )
    )
    # v2.83.0 — sortable column headers on the /scans desktop table.
    # The default is created_at desc (newest first, the previous
    # behaviour).  total_hosts is the aggregate alias from the join;
    # ordering by it is allowed because it appears in the SELECT list.
    _SORT_COLUMNS = {
        "created_at": models.Scan.created_at,
        "filename": models.Scan.filename,
        "tool_name": models.Scan.tool_name,
        "total_hosts": func.count(models.HostScanHistory.id),
        "new_hosts": func.sum(case((models.HostScanHistory.host_created == True, 1), else_=0)),  # noqa: E712
    }
    sort_column = _SORT_COLUMNS.get(sort_by or "created_at", models.Scan.created_at)
    order_direction = desc if (sort_order or "desc").lower() == "desc" else (lambda c: c)
    scans_query = (
        scans_query
        .order_by(order_direction(sort_column))
        .offset(skip)
        .limit(limit)
    )

    results = scans_query.all()
    scan_ids = [r.id for r in results]

    # Batch port stats for all scans at once (avoids N+1)
    port_stats_map = {}
    if scan_ids:
        port_stats_rows = (
            db.query(
                models.HostScanHistory.scan_id.label('scan_id'),
                func.count(models.Port.id).label('total_ports'),
                func.sum(case((models.Port.state == 'open', 1), else_=0)).label('open_ports'),
                func.count(distinct(models.Port.port_number)).label('unique_ports'),
                func.sum(
                    case(
                        (and_(models.Port.state == 'open', models.Port.protocol == 'tcp'), 1),
                        else_=0,
                    )
                ).label('open_tcp_ports'),
                func.sum(
                    case(
                        (and_(models.Port.state == 'open', models.Port.protocol == 'udp'), 1),
                        else_=0,
                    )
                ).label('open_udp_ports'),
            )
            .select_from(models.Port)
            .join(models.Host, models.Port.host_id == models.Host.id)
            .join(models.HostScanHistory, models.Host.id == models.HostScanHistory.host_id)
            .filter(models.HostScanHistory.scan_id.in_(scan_ids))
            .group_by(models.HostScanHistory.scan_id)
            .all()
        )
        port_stats_map = {row.scan_id: row for row in port_stats_rows}

    # Batch vulnerability stats for nessus scans
    nessus_scan_ids = [
        r.id for r in results
        if "nessus" in (r.tool_name or r.scan_type or "").lower()
    ]
    vuln_stats_map = {}
    if nessus_scan_ids:
        vuln_stats_rows = (
            db.query(
                Vulnerability.scan_id.label('scan_id'),
                func.count(Vulnerability.id).label('total'),
                func.sum(case((Vulnerability.severity == VulnerabilitySeverity.CRITICAL, 1), else_=0)).label('critical'),
                func.sum(case((Vulnerability.severity == VulnerabilitySeverity.HIGH, 1), else_=0)).label('high'),
                func.sum(case((Vulnerability.severity == VulnerabilitySeverity.MEDIUM, 1), else_=0)).label('medium'),
                func.sum(case((Vulnerability.severity == VulnerabilitySeverity.LOW, 1), else_=0)).label('low'),
                func.sum(case((Vulnerability.severity == VulnerabilitySeverity.INFO, 1), else_=0)).label('info'),
            )
            .filter(Vulnerability.scan_id.in_(nessus_scan_ids))
            .group_by(Vulnerability.scan_id)
            .all()
        )
        vuln_stats_map = {row.scan_id: row for row in vuln_stats_rows}

    # Batch-resolve uploader usernames (multi-analyst attribution on the
    # Scans list).  One query keyed by the distinct uploader ids in this
    # page — mirrors the port/vuln batch maps above; avoids per-row joins
    # and keeps uploaded_by_id out of a User-joined GROUP BY.
    uploader_map: Dict[int, str] = {}
    uploader_ids = {r.uploaded_by_id for r in results if r.uploaded_by_id is not None}
    if uploader_ids:
        uploader_map = {
            uid: uname
            for uid, uname in db.query(User.id, User.username).filter(User.id.in_(uploader_ids)).all()
        }

    scan_summaries = []
    for result in results:
        port_stats = port_stats_map.get(result.id)

        port_breakdown = None
        if port_stats:
            port_breakdown = ScanPortBreakdown(
                unique_ports=port_stats.unique_ports or 0,
                open_tcp_ports=port_stats.open_tcp_ports or 0,
                open_udp_ports=port_stats.open_udp_ports or 0,
            )

        vulnerability_summary = None
        vuln_stats = vuln_stats_map.get(result.id)
        if vuln_stats:
            vulnerability_summary = ScanVulnerabilitySummary(
                total=vuln_stats.total or 0,
                critical=vuln_stats.critical or 0,
                high=vuln_stats.high or 0,
                medium=vuln_stats.medium or 0,
                low=vuln_stats.low or 0,
                info=vuln_stats.info or 0,
            )

        scan_summaries.append(ScanSummary(
            id=result.id,
            filename=result.filename,
            scan_type=result.scan_type,
            tool_name=result.tool_name,
            created_at=result.created_at,
            start_time=result.start_time,
            end_time=result.end_time,
            command_line=result.command_line,
            version=result.version,
            total_hosts=result.total_hosts or 0,
            up_hosts=result.up_hosts or 0,
            new_hosts=result.new_hosts or 0,
            updated_hosts=(result.total_hosts or 0) - (result.new_hosts or 0),
            total_ports=port_stats.total_ports if port_stats and port_stats.total_ports else 0,
            open_ports=port_stats.open_ports if port_stats and port_stats.open_ports else 0,
            port_breakdown=port_breakdown,
            vulnerability_summary=vulnerability_summary,
            uploaded_by=uploader_map.get(result.uploaded_by_id),
        ))

    return scan_summaries


@router.get("/summary", response_model=ScanInventorySummary)
def get_scans_summary(
    search: Optional[str] = Query(None, max_length=200),
    tool: Optional[str] = Query(None, max_length=64),
    created_after: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Filter-aware totals for the /scans headline cards.

    Mirrors the list endpoint's per-scan aggregates (total_hosts / up_hosts
    via HostScanHistory; open_ports via Port->Host->HostScanHistory) but
    summed across *every* scan matching the filters, not just the loaded
    page — so the headline reflects reality regardless of pagination.
    """
    # Scan count + host observation sums in one pass over Scan -> history.
    host_agg = (
        db.query(
            func.count(distinct(models.Scan.id)).label("total_scans"),
            func.count(models.HostScanHistory.id).label("total_hosts"),
            func.sum(
                case((models.HostScanHistory.state_at_scan == "up", 1), else_=0)
            ).label("up_hosts"),
        )
        .select_from(models.Scan)
        .outerjoin(models.HostScanHistory, models.Scan.id == models.HostScanHistory.scan_id)
        .filter(models.Scan.project_id == project.id)
    )
    host_agg = _apply_scan_inventory_filters(
        host_agg, search=search, tool=tool, created_after=created_after
    )
    host_row = host_agg.one()

    # Open services — count open ports across the (host, scan) observations
    # of the matching scans.  Matches the list endpoint's per-scan open_ports
    # summed client-side: each scan contributes the open ports on its hosts.
    open_services_query = (
        db.query(func.count(models.Port.id))
        .select_from(models.Port)
        .join(models.Host, models.Port.host_id == models.Host.id)
        .join(models.HostScanHistory, models.Host.id == models.HostScanHistory.host_id)
        .join(models.Scan, models.HostScanHistory.scan_id == models.Scan.id)
        .filter(models.Scan.project_id == project.id, models.Port.state == "open")
    )
    open_services_query = _apply_scan_inventory_filters(
        open_services_query, search=search, tool=tool, created_after=created_after
    )
    open_services = open_services_query.scalar() or 0

    return ScanInventorySummary(
        total_scans=host_row.total_scans or 0,
        total_hosts=host_row.total_hosts or 0,
        up_hosts=host_row.up_hosts or 0,
        open_services=open_services,
    )


@router.get("/out-of-scope", response_model=Paginated[OutOfScopeHost])
def get_all_out_of_scope_hosts(
    # v2.86.8 — pagination caps added.  Pre-fix this returned every
    # out-of-scope host across every scan in the project on a single
    # call; in noisy environments (broad nmap discovery across a /16)
    # the row count grows fast and the response could exhaust memory.
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    search: Optional[str] = Query(
        None,
        max_length=200,
        description=(
            "Case-insensitive substring match on IP / hostname / reason. "
            "Pushed server-side so a noisy out-of-scope list is filterable "
            "without loading every row (v2.86.8)."
        ),
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get all out-of-scope hosts across all scans, paginated."""
    q = db.query(models.OutOfScopeHost).filter(
        models.OutOfScopeHost.project_id == project.id
    )
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        q = q.filter(
            or_(
                models.OutOfScopeHost.ip_address.ilike(like),
                models.OutOfScopeHost.hostname.ilike(like),
                models.OutOfScopeHost.reason.ilike(like),
            )
        )

    # v2.86.13 — envelope shape.  Total reflects the filtered query so
    # an active ``search`` narrows both the rows AND the total.
    total = q.with_entities(func.count(models.OutOfScopeHost.id)).scalar() or 0
    hosts = q.order_by(models.OutOfScopeHost.id.asc()).offset(skip).limit(limit).all()
    return Paginated[OutOfScopeHost].build(
        items=hosts, total=total, skip=skip, limit=limit,
    )


@router.delete(
    "/out-of-scope",
    response_model=PurgeResponse,
    responses=_ADMIN_RESPONSES,
    dependencies=[Depends(require_project_role(ProjectRole.ADMIN))],
    summary="Purge all out-of-scope hosts (admin)",
)
def purge_out_of_scope_hosts(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Delete all records from the out_of_scope_hosts table for this project. Requires admin role."""
    try:
        deleted = db.query(models.OutOfScopeHost).filter(
            models.OutOfScopeHost.project_id == project.id
        ).delete(synchronize_session=False)
        db.commit()
        logger.info("Purged %d out-of-scope hosts for project %d", deleted, project.id)
        return {"purged": deleted}
    except Exception as exc:  # pragma: no cover - defensive cleanup
        db.rollback()
        logger.error("Failed to purge out-of-scope hosts: %s", exc)
        raise HTTPException(status_code=500, detail="Unable to purge out-of-scope records")


_SCAN_DIFF_ROW_CAP = 500


def _scan_side_stats(db: Session, scan: models.Scan) -> ScanDiffSide:
    """Per-scan observed host/port totals from the history tables."""
    host_row = (
        db.query(
            func.count(models.HostScanHistory.host_id),
            func.sum(case((models.HostScanHistory.state_at_scan == "up", 1), else_=0)),
        )
        .filter(models.HostScanHistory.scan_id == scan.id)
        .one()
    )
    port_row = (
        db.query(
            func.count(models.PortScanHistory.port_id),
            func.sum(case((models.PortScanHistory.state_at_scan == "open", 1), else_=0)),
        )
        .filter(models.PortScanHistory.scan_id == scan.id)
        .one()
    )
    return ScanDiffSide(
        scan_id=scan.id,
        filename=scan.filename,
        tool_name=scan.tool_name,
        scan_type=scan.scan_type,
        created_at=scan.created_at,
        total_hosts=int(host_row[0] or 0),
        up_hosts=int(host_row[1] or 0),
        total_ports=int(port_row[0] or 0),
        open_ports=int(port_row[1] or 0),
    )


@router.get(
    "/compare",
    response_model=ScanDiffResponse,
    summary="Attack-surface delta between two scans",
)
def compare_scans(
    a: int = Query(..., description="Baseline scan id"),
    b: int = Query(..., description="Comparison scan id"),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Diff two scans of this project: which hosts/ports appeared, vanished,
    or changed state between baseline ``a`` and comparison ``b``.

    Reconstructed from HostScanHistory / PortScanHistory (per-scan
    observations), so it works across the dedup boundary — a host that
    persists across scans is a single Host row, but each scan's view of
    it is preserved in history.  Result lists are capped at ``row_cap``;
    the ``counts`` block carries exact totals.
    """
    scans = {
        s.id: s
        for s in db.query(models.Scan).filter(
            models.Scan.project_id == project.id,
            models.Scan.id.in_([a, b]),
        )
    }
    scan_a = scans.get(a)
    scan_b = scans.get(b)
    if scan_a is None or scan_b is None:
        missing = ", ".join(str(x) for x in (a, b) if x not in scans)
        raise HTTPException(status_code=404, detail=f"Scan(s) not found in project: {missing}")

    cap = _SCAN_DIFF_ROW_CAP

    # --- Host-level diff (presence + state) ---
    a_host_states = dict(
        db.query(models.HostScanHistory.host_id, models.HostScanHistory.state_at_scan)
        .filter(models.HostScanHistory.scan_id == a)
        .all()
    )
    b_host_states = dict(
        db.query(models.HostScanHistory.host_id, models.HostScanHistory.state_at_scan)
        .filter(models.HostScanHistory.scan_id == b)
        .all()
    )
    a_host_ids, b_host_ids = set(a_host_states), set(b_host_states)
    new_host_ids = b_host_ids - a_host_ids
    dropped_host_ids = a_host_ids - b_host_ids
    changed_host_ids = {
        hid
        for hid in (a_host_ids & b_host_ids)
        if (a_host_states.get(hid) or "") != (b_host_states.get(hid) or "")
    }

    # --- Port-level diff (openness transitions) ---
    # Query only the *changed* port ids via NOT IN subqueries rather than
    # loading both scans' full (port_id, state) maps into Python.  Two
    # broad scans can each observe 100k+ ports; materialising both just to
    # set-diff them was the memory hot spot.  ``port_id NOT IN (open-in-X)``
    # captures both "missing in X" and "present-but-not-open in X", matching
    # the prior dict logic.  (NOT IN subquery is portable to the SQLite test
    # backend, unlike FULL OUTER JOIN.)
    a_open_ports_subq = (
        db.query(models.PortScanHistory.port_id)
        .filter(
            models.PortScanHistory.scan_id == a,
            models.PortScanHistory.state_at_scan == "open",
        )
    )
    b_open_ports_subq = (
        db.query(models.PortScanHistory.port_id)
        .filter(
            models.PortScanHistory.scan_id == b,
            models.PortScanHistory.state_at_scan == "open",
        )
    )
    newly_open_ids = [
        pid for (pid,) in (
            db.query(models.PortScanHistory.port_id)
            .filter(
                models.PortScanHistory.scan_id == b,
                models.PortScanHistory.state_at_scan == "open",
                ~models.PortScanHistory.port_id.in_(a_open_ports_subq),
            )
            .all()
        )
    ]
    closed_ids = [
        pid for (pid,) in (
            db.query(models.PortScanHistory.port_id)
            .filter(
                models.PortScanHistory.scan_id == a,
                models.PortScanHistory.state_at_scan == "open",
                ~models.PortScanHistory.port_id.in_(b_open_ports_subq),
            )
            .all()
        )
    ]

    counts = ScanDiffCounts(
        new_hosts=len(new_host_ids),
        dropped_hosts=len(dropped_host_ids),
        host_state_changes=len(changed_host_ids),
        newly_open_ports=len(newly_open_ids),
        closed_ports=len(closed_ids),
    )

    # Resolve host metadata only for the rows we'll actually return.
    host_ids_needed = set(
        list(new_host_ids)[:cap] + list(dropped_host_ids)[:cap] + list(changed_host_ids)[:cap]
    )
    host_meta = (
        {h.id: h for h in db.query(models.Host).filter(models.Host.id.in_(host_ids_needed))}
        if host_ids_needed
        else {}
    )

    def host_rows(ids) -> List[ScanDiffHostRow]:
        rows = []
        for hid in list(ids)[:cap]:
            h = host_meta.get(hid)
            if h is not None:
                rows.append(ScanDiffHostRow(host_id=hid, ip_address=h.ip_address, hostname=h.hostname))
        return rows

    host_state_change_rows = []
    for hid in list(changed_host_ids)[:cap]:
        h = host_meta.get(hid)
        if h is not None:
            host_state_change_rows.append(ScanDiffHostStateChange(
                host_id=hid,
                ip_address=h.ip_address,
                hostname=h.hostname,
                state_a=a_host_states.get(hid),
                state_b=b_host_states.get(hid),
            ))

    # Resolve port metadata (Port -> Host) only for capped port rows.
    port_ids_needed = set(newly_open_ids[:cap]) | set(closed_ids[:cap])
    port_meta: Dict[int, tuple] = {}
    if port_ids_needed:
        for pid, pnum, proto, svc, hid, ip in (
            db.query(
                models.Port.id,
                models.Port.port_number,
                models.Port.protocol,
                models.Port.service_name,
                models.Host.id,
                models.Host.ip_address,
            )
            .join(models.Host, models.Port.host_id == models.Host.id)
            .filter(models.Port.id.in_(port_ids_needed))
            .all()
        ):
            port_meta[pid] = (pnum, proto, svc, hid, ip)

    # Per-port state in each scan, ONLY for the capped rows we return.
    # The openness diff above uses NOT-IN subqueries (no full state maps),
    # so these were never built — port_rows referenced undefined
    # a_port_states/b_port_states and raised NameError.  Bounded lookup
    # restores accurate state_a/state_b (e.g. a newly-open port shows its
    # prior non-open state in scan A) without materialising every port.
    a_port_states: Dict[int, str] = {}
    b_port_states: Dict[int, str] = {}
    if port_ids_needed:
        a_port_states = dict(
            db.query(models.PortScanHistory.port_id, models.PortScanHistory.state_at_scan)
            .filter(
                models.PortScanHistory.scan_id == a,
                models.PortScanHistory.port_id.in_(port_ids_needed),
            )
            .all()
        )
        b_port_states = dict(
            db.query(models.PortScanHistory.port_id, models.PortScanHistory.state_at_scan)
            .filter(
                models.PortScanHistory.scan_id == b,
                models.PortScanHistory.port_id.in_(port_ids_needed),
            )
            .all()
        )

    def port_rows(ids) -> List[ScanDiffPortChange]:
        rows = []
        for pid in ids[:cap]:
            meta = port_meta.get(pid)
            if meta is None:
                continue
            pnum, proto, svc, hid, ip = meta
            rows.append(ScanDiffPortChange(
                host_id=hid,
                ip_address=ip,
                port_number=pnum,
                protocol=proto,
                service_name=svc,
                state_a=a_port_states.get(pid),
                state_b=b_port_states.get(pid),
            ))
        return rows

    return ScanDiffResponse(
        scan_a=_scan_side_stats(db, scan_a),
        scan_b=_scan_side_stats(db, scan_b),
        counts=counts,
        row_cap=cap,
        new_hosts=host_rows(new_host_ids),
        dropped_hosts=host_rows(dropped_host_ids),
        host_state_changes=host_state_change_rows,
        newly_open_ports=port_rows(newly_open_ids),
        closed_ports=port_rows(closed_ids),
    )


@router.get("/{scan_id}", response_model=Scan)
def get_scan(
    scan_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    scan = db.query(models.Scan).filter(
        models.Scan.id == scan_id,
        models.Scan.project_id == project.id,
    ).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    # Attach accurate per-scan counts so the detail page's title cards
    # match the /scans list badge.  Both derive from HostScanHistory /
    # current Port state — the SAME aggregate the list endpoint uses.
    # Pre-fix the detail page counted the *fetched* host list (capped at
    # limit=1000), so a 1942-host scan rendered "1000/1000 up".
    host_row = (
        db.query(
            func.count(models.HostScanHistory.id),
            func.sum(case((models.HostScanHistory.state_at_scan == "up", 1), else_=0)),
        )
        .filter(models.HostScanHistory.scan_id == scan.id)
        .one()
    )
    port_row = (
        db.query(
            func.count(models.Port.id),
            func.sum(case((models.Port.state == "open", 1), else_=0)),
        )
        .select_from(models.Port)
        .join(models.Host, models.Port.host_id == models.Host.id)
        .join(models.HostScanHistory, models.Host.id == models.HostScanHistory.host_id)
        .filter(models.HostScanHistory.scan_id == scan.id)
        .one()
    )
    # Non-mapped instance attributes — read by the Scan Pydantic schema
    # (from_attributes); they override the schema's 0 defaults.
    scan.total_hosts = int(host_row[0] or 0)
    scan.up_hosts = int(host_row[1] or 0)
    scan.total_ports = int(port_row[0] or 0)
    scan.open_ports = int(port_row[1] or 0)
    return scan

@router.get(
    "/{scan_id}/deletion-impact",
    response_model=ScanDeletionImpact,
    responses={**_ADMIN_RESPONSES, 404: {"description": "Scan not found"}},
    dependencies=[Depends(require_project_role(ProjectRole.ADMIN))],
    summary="Preview what deleting a scan removes (admin)",
)
def get_scan_deletion_impact(
    scan_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Compute exactly what `DELETE /scans/{scan_id}` would remove, without
    deleting anything. Powers the delete-confirmation modal.

    Mirrors the delete endpoint's orphan rule: a host is removed only if this
    scan is the *only* scan that ever observed it. Vulnerabilities and web
    interfaces are scan-scoped (FK ``ON DELETE CASCADE`` on ``scan_id``), so
    their counts key off ``scan_id`` directly; ports are host-owned, so only
    ports on orphan hosts are removed.
    """
    scan = db.query(models.Scan).filter(
        models.Scan.id == scan_id,
        models.Scan.project_id == project.id,
    ).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    # Orphan host ids + their IPs — same NOT EXISTS rule the delete uses.
    orphan_rows = db.execute(
        text("""
            SELECT h.host_id, hv.ip_address
            FROM host_scan_history h
            JOIN hosts_v2 hv ON hv.id = h.host_id
            WHERE h.scan_id = :scan_id
              AND NOT EXISTS (
                  SELECT 1 FROM host_scan_history h2
                  WHERE h2.host_id = h.host_id AND h2.scan_id != :scan_id
              )
            ORDER BY hv.ip_address
        """),
        {"scan_id": scan_id},
    ).fetchall()
    orphan_host_ids = [r[0] for r in orphan_rows]
    sample_removed_ips = [str(r[1]) for r in orphan_rows[:10] if r[1] is not None]

    # Total distinct hosts this scan observed — the rest (non-orphans) are kept.
    hosts_observed = db.execute(
        text("SELECT COUNT(DISTINCT host_id) FROM host_scan_history WHERE scan_id = :scan_id"),
        {"scan_id": scan_id},
    ).scalar() or 0

    ports_removed = 0
    if orphan_host_ids:
        ports_removed = db.execute(
            text("SELECT COUNT(*) FROM ports_v2 WHERE host_id = ANY(:ids)"),
            {"ids": orphan_host_ids},
        ).scalar() or 0

    vulnerabilities_removed = db.execute(
        text("SELECT COUNT(*) FROM vulnerabilities WHERE scan_id = :scan_id"),
        {"scan_id": scan_id},
    ).scalar() or 0
    web_interfaces_removed = db.execute(
        text("SELECT COUNT(*) FROM web_interfaces WHERE scan_id = :scan_id"),
        {"scan_id": scan_id},
    ).scalar() or 0

    return ScanDeletionImpact(
        scan_id=scan_id,
        filename=scan.filename,
        hosts_removed=len(orphan_host_ids),
        hosts_kept=max(0, hosts_observed - len(orphan_host_ids)),
        sample_removed_ips=sample_removed_ips,
        ports_removed=ports_removed,
        vulnerabilities_removed=vulnerabilities_removed,
        web_interfaces_removed=web_interfaces_removed,
    )


@router.delete(
    "/{scan_id}",
    response_model=DeleteScanResponse,
    responses={**_ADMIN_RESPONSES, 404: {"description": "Scan not found"}},
    dependencies=[Depends(require_project_role(ProjectRole.ADMIN))],
    summary="Delete scan (admin)",
)
def delete_scan(
    scan_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Delete a scan and all dependent records atomically. Requires admin role."""
    scan = db.query(models.Scan).filter(
        models.Scan.id == scan_id,
        models.Scan.project_id == project.id,
    ).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    try:
        # All steps run inside a single transaction — if any step fails,
        # the entire operation is rolled back and no data is lost.

        # DB-level ON DELETE actions (migration f1a9c7e3b528) now cascade a
        # scan's / host's / port's owned children and SET NULL the nullable
        # provenance pointers, so the former pg_constraint-reflection
        # workaround (_clear_fk_refs) is gone: deleting the orphan hosts and
        # the scan lets the database clean up every dependent row.  (Steps
        # 2-3 still re-point surviving rows' last_updated_scan_id BEFORE the
        # delete so they keep a meaningful scan instead of the bare NULL the
        # SET NULL cascade would leave.)

        # 1. Identify hosts only seen by this scan (orphans to remove)
        orphan_host_ids = [
            r[0]
            for r in db.execute(
                text("""
                    SELECT h.host_id
                    FROM host_scan_history h
                    WHERE h.scan_id = :scan_id
                      AND NOT EXISTS (
                          SELECT 1 FROM host_scan_history h2
                          WHERE h2.host_id = h.host_id AND h2.scan_id != :scan_id
                      )
                """),
                {"scan_id": scan_id},
            ).fetchall()
        ]

        # 2. Bulk-update surviving hosts — set last_updated_scan_id to
        #    the most recent remaining scan, or NULL if none remain.
        db.execute(text("""
            UPDATE hosts_v2 h
            SET last_updated_scan_id = sub.new_scan_id
            FROM (
                SELECT DISTINCT ON (hsh.host_id)
                    hsh.host_id,
                    hsh.scan_id AS new_scan_id
                FROM host_scan_history hsh
                WHERE hsh.scan_id != :scan_id
                ORDER BY hsh.host_id, hsh.discovered_at DESC
            ) sub
            WHERE h.id = sub.host_id
              AND h.last_updated_scan_id = :scan_id
              AND h.id != ALL(:orphan_ids)
        """), {"scan_id": scan_id, "orphan_ids": orphan_host_ids or []})
        db.execute(text("""
            UPDATE hosts_v2
            SET last_updated_scan_id = NULL
            WHERE last_updated_scan_id = :scan_id
              AND id != ALL(:orphan_ids)
        """), {"scan_id": scan_id, "orphan_ids": orphan_host_ids or []})

        # 3. Bulk-update surviving ports — same pattern.
        db.execute(text("""
            UPDATE ports_v2 p
            SET last_updated_scan_id = sub.new_scan_id
            FROM (
                SELECT DISTINCT ON (psh.port_id)
                    psh.port_id,
                    psh.scan_id AS new_scan_id
                FROM port_scan_history psh
                WHERE psh.scan_id != :scan_id
                ORDER BY psh.port_id, psh.discovered_at DESC
            ) sub
            WHERE p.id = sub.port_id
              AND p.last_updated_scan_id = :scan_id
              AND p.host_id != ALL(:orphan_host_ids)
        """), {"scan_id": scan_id, "orphan_host_ids": orphan_host_ids or []})
        db.execute(text("""
            UPDATE ports_v2
            SET last_updated_scan_id = NULL
            WHERE last_updated_scan_id = :scan_id
              AND host_id != ALL(:orphan_host_ids)
        """), {"scan_id": scan_id, "orphan_host_ids": orphan_host_ids or []})

        # 4. Delete orphaned hosts — their ports/scripts/vulns/confidence/
        #    history and the ports' own children all cascade from the host
        #    (FK ON DELETE CASCADE), so a single DELETE suffices.
        if orphan_host_ids:
            db.execute(text('DELETE FROM "hosts_v2" WHERE id = ANY(:ids)'), {"ids": orphan_host_ids})

        # 5. Delete the scan — its owned children cascade and the nullable
        #    provenance pointers (last_updated_scan_id, conflict_history.*) are
        #    SET NULL by the FK actions.  Raw DELETE (rather than db.delete(scan))
        #    so the database does the cascade in one statement instead of
        #    SQLAlchemy loading every child of the scan's delete-orphan
        #    relationships (vulnerabilities, web_interfaces) into memory first.
        db.execute(text('DELETE FROM "scans" WHERE id = :sid'), {"sid": scan_id})

        # Single commit — all or nothing
        db.commit()

        return {
            "message": "Scan deleted successfully",
            "hosts_removed": len(orphan_host_ids),
        }

    except Exception as e:
        db.rollback()
        logger.exception("Failed to delete scan %s", scan_id)
        raise HTTPException(status_code=500, detail="Error deleting scan")

@router.get("/{scan_id}/dns-records", response_model=Paginated[DNSRecord])
def get_scan_dns_records(
    scan_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """DNS records produced by a scan (e.g. dnsx resolution output).

    A DNS-resolution scan persists one DNSRecord per (record_type, domain,
    value, resolver) tuple, but only A/AAAA answers create host inventory
    rows — so CNAME/MX/NS/TXT/etc. records were stored yet had no surface.
    This lists every record for the scan so the operator can see the full
    answer set, not just the hosts it produced.

    CR5-C3 — returns a ``Paginated`` envelope (``items`` + ``total`` +
    ``has_more``) so the UI reports the TRUE record count, not just the size
    of the first page; large dnsx scans routinely exceed one page.
    """
    scan = db.query(models.Scan).filter(
        models.Scan.id == scan_id,
        models.Scan.project_id == project.id,
    ).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    base = db.query(models.DNSRecord).filter(models.DNSRecord.scan_id == scan_id)
    total = base.count()
    items = (
        base.order_by(
            models.DNSRecord.record_type.asc(),
            models.DNSRecord.domain.asc(),
            models.DNSRecord.value.asc(),
            models.DNSRecord.id.asc(),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )
    return Paginated.build(items=items, total=total, skip=skip, limit=limit)


@router.get("/{scan_id}/out-of-scope", response_model=List[OutOfScopeHost])
def get_scan_out_of_scope_hosts(
    scan_id: int,
    # v2.86.4 — pagination caps added.
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get out-of-scope hosts for a specific scan with pagination"""
    scan = db.query(models.Scan).filter(
        models.Scan.id == scan_id,
        models.Scan.project_id == project.id,
    ).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    hosts = db.query(models.OutOfScopeHost).filter(
        models.OutOfScopeHost.scan_id == scan_id
    ).order_by(models.OutOfScopeHost.ip_address)\
     .offset(skip).limit(limit).all()

    return hosts

@router.get(
    "/{scan_id}/command-explanation",
    response_model=CommandExplanationResponse,
    responses={**_AUTH_RESPONSES, 404: {"description": "Scan not found"}},
    summary="Explain scan command",
)
def get_scan_command_explanation(
    scan_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get detailed explanation of the scan command and its arguments."""
    scan = db.query(models.Scan).filter(
        models.Scan.id == scan_id,
        models.Scan.project_id == project.id,
    ).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    # If no command line available, return basic info
    if not scan.command_line:
        return {
            "has_command": False,
            "tool": scan.tool_name or "Unknown",
            "message": "No command line information available for this scan"
        }

    # Analyze the command
    explanation_service = CommandExplanationService()
    analysis = explanation_service.analyze_command(scan.command_line, scan.tool_name)

    if not analysis:
        return {
            "has_command": True,
            "tool": scan.tool_name or "Unknown",
            "command": scan.command_line,
            "message": "Unable to parse command line arguments"
        }

    # Convert the analysis to a dictionary format for JSON response
    return {
        "has_command": True,
        "tool": analysis.tool,
        "command": analysis.command,
        "target": analysis.target,
        "scan_type": analysis.scan_type,
        "summary": analysis.summary,
        "risk_assessment": analysis.risk_assessment,
        "arguments": [
            {
                "arg": arg.arg,
                "description": arg.description,
                "category": arg.category,
                "risk_level": arg.risk_level,
                "examples": arg.examples
            }
            for arg in analysis.arguments
        ]
    }

@router.get(
    "/{scan_id}/hosts/count",
    response_model=CountResponse,
    responses={**_AUTH_RESPONSES, 404: {"description": "Scan not found"}},
    summary="Count hosts in scan",
)
def get_scan_hosts_count(
    scan_id: int,
    state: Optional[str] = None,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get total count of hosts for a scan (for pagination)."""
    scan = db.query(models.Scan).filter(
        models.Scan.id == scan_id,
        models.Scan.project_id == project.id,
    ).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    query = db.query(models.Host).join(models.HostScanHistory, models.Host.id == models.HostScanHistory.host_id).filter(models.HostScanHistory.scan_id == scan_id)
    if state:
        query = query.filter(models.Host.state == state)

    count = query.count()
    return {"total": count}

@router.get(
    "/{scan_id}/out-of-scope/count",
    response_model=CountResponse,
    responses={**_AUTH_RESPONSES, 404: {"description": "Scan not found"}},
    summary="Count out-of-scope hosts in scan",
)
def get_scan_out_of_scope_count(
    scan_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get total count of out-of-scope hosts for a scan (for pagination)."""
    scan = db.query(models.Scan).filter(
        models.Scan.id == scan_id,
        models.Scan.project_id == project.id,
    ).first()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    count = db.query(models.OutOfScopeHost).filter(
        models.OutOfScopeHost.scan_id == scan_id
    ).count()
    return {"total": count}
