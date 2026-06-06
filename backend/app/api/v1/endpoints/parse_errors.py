import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, case, or_
from pydantic import BaseModel

from app.db.session import get_db
from app.db import models
from app.schemas.schemas import ParseError, ParseErrorSummary, ParseErrorCreate
from app.api.v1.endpoints.auth import get_current_user, require_role
from app.db.models_auth import User, UserRole
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project, ProjectRole

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

_ANALYST_RESPONSES = {
    401: {"description": "Not authenticated"},
    403: {"description": "Insufficient permissions — analyst role required"},
}


class MessageResponse(BaseModel):
    message: str


class DeleteParseErrorResponse(BaseModel):
    message: str
    jobs_updated: int = 0


class ParseErrorStatsResponse(BaseModel):
    total_errors: int = 0
    unresolved: int = 0
    reviewed: int = 0
    fixed: int = 0
    ignored: int = 0


# --- Ingestion Results schemas ---

class IngestionResultStats(BaseModel):
    hosts_parsed: int = 0
    hosts_up: int = 0
    ports_found: int = 0
    open_ports: int = 0
    services_detected: int = 0


class IngestionResultError(BaseModel):
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    user_message: Optional[str] = None


class IngestionResultItem(BaseModel):
    id: int
    original_filename: str
    status: str  # queued, processing, completed, failed
    file_size: Optional[int] = None
    tool_name: Optional[str] = None
    scan_type: Optional[str] = None
    scan_id: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    progress: Optional[str] = None
    # Stats (populated for completed jobs)
    stats: Optional[IngestionResultStats] = None
    # Error info (populated for failed jobs)
    error: Optional[IngestionResultError] = None


class IngestionResultsResponse(BaseModel):
    items: List[IngestionResultItem]
    total: int
    summary: Dict[str, Any]  # total_completed, total_failed, total_hosts, total_ports


class ParseErrorSafe(BaseModel):
    """Parse error detail with sensitive fields stripped for non-admin users."""
    id: int
    filename: str
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    error_type: str
    error_message: str
    error_details: Optional[dict] = None
    file_preview: Optional[str] = None
    user_message: Optional[str] = None
    status: Optional[str] = "unresolved"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@router.get(
    "/ingestion-results",
    response_model=IngestionResultsResponse,
    responses=_ANALYST_RESPONSES,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="List all ingestion results with statistics",
)
def get_ingestion_results(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(
        None,
        description="Filter by IngestionJob.status (queued, processing, completed, failed) — v2.86.2.",
    ),
    tool: Optional[str] = Query(
        None,
        max_length=64,
        description="Case-insensitive exact match on IngestionJob.tool_name — v2.86.2.",
    ),
    search: Optional[str] = Query(
        None,
        max_length=200,
        description=(
            "Case-insensitive substring match across original_filename, "
            "error_message, and last_error (v2.86.2).  Pushed server-side so "
            "the page no longer filters the partial slice it loaded."
        ),
    ),
    sort_by: str = Query(
        "created_at",
        pattern="^(created_at|original_filename|status|tool_name|file_size)$",
        description="Sort key (v2.86.2).",
    ),
    sort_order: str = Query(
        "desc",
        pattern="^(asc|desc)$",
        description="Sort direction (v2.86.2).",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get all ingestion jobs (successful and failed) with detailed statistics.

    For completed jobs, includes host/port counts from scan history.
    For failed jobs, includes error details from parse_errors.
    Requires analyst role.
    """
    # Base query — total count uses the SAME predicates as the page
    # query so the pagination math reflects the filtered set, not the
    # raw row count of the table.
    base = db.query(models.IngestionJob).filter(models.IngestionJob.project_id == project.id)
    if status:
        base = base.filter(models.IngestionJob.status == status)
    if tool:
        base = base.filter(func.lower(models.IngestionJob.tool_name) == tool.lower())
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        base = base.filter(
            or_(
                models.IngestionJob.original_filename.ilike(like),
                models.IngestionJob.error_message.ilike(like),
                models.IngestionJob.last_error.ilike(like),
            )
        )

    total = base.with_entities(func.count(models.IngestionJob.id)).scalar() or 0

    # v2.86.2 — sort by selectable column.  Map of allowed keys to ORM
    # columns; the regex on `sort_by` already restricts to this set.
    sort_col_map = {
        "created_at": models.IngestionJob.created_at,
        "original_filename": models.IngestionJob.original_filename,
        "status": models.IngestionJob.status,
        "tool_name": models.IngestionJob.tool_name,
        "file_size": models.IngestionJob.file_size,
    }
    sort_col = sort_col_map[sort_by]
    order_clause = sort_col.asc() if sort_order == "asc" else sort_col.desc()

    jobs = base.order_by(order_clause).offset(skip).limit(limit).all()

    # Collect scan_ids from completed jobs for batch stats query
    scan_ids = [j.scan_id for j in jobs if j.scan_id is not None]

    # Build stats lookup: scan_id -> {hosts_parsed, hosts_up, ports_found, open_ports, services_detected}
    stats_by_scan: Dict[int, IngestionResultStats] = {}
    if scan_ids:
        # Host stats per scan
        host_stats = (
            db.query(
                models.HostScanHistory.scan_id,
                func.count(models.HostScanHistory.id).label("hosts_parsed"),
                func.count(
                    case(
                        (models.HostScanHistory.state_at_scan == "up", 1),
                    )
                ).label("hosts_up"),
            )
            .filter(models.HostScanHistory.scan_id.in_(scan_ids))
            .group_by(models.HostScanHistory.scan_id)
            .all()
        )
        host_lookup = {row.scan_id: row for row in host_stats}

        # Port stats per scan (join through host_scan_history to get scan-scoped ports)
        port_stats = (
            db.query(
                models.PortScanHistory.scan_id,
                func.count(models.PortScanHistory.id).label("ports_found"),
                func.count(
                    case(
                        (models.PortScanHistory.state_at_scan == "open", 1),
                    )
                ).label("open_ports"),
            )
            .filter(models.PortScanHistory.scan_id.in_(scan_ids))
            .group_by(models.PortScanHistory.scan_id)
            .all()
        )
        port_lookup = {row.scan_id: row for row in port_stats}

        # Services detected per scan: count distinct service_name on ports
        # Join PortScanHistory -> Port to get service_name
        service_stats = (
            db.query(
                models.PortScanHistory.scan_id,
                func.count(func.distinct(models.Port.service_name)).label("services_detected"),
            )
            .join(models.Port, models.PortScanHistory.port_id == models.Port.id)
            .filter(
                models.PortScanHistory.scan_id.in_(scan_ids),
                models.Port.service_name.isnot(None),
                models.Port.service_name != "",
            )
            .group_by(models.PortScanHistory.scan_id)
            .all()
        )
        service_lookup = {row.scan_id: row.services_detected for row in service_stats}

        for sid in scan_ids:
            h = host_lookup.get(sid)
            p_row = port_lookup.get(sid)
            stats_by_scan[sid] = IngestionResultStats(
                hosts_parsed=h.hosts_parsed if h else 0,
                hosts_up=h.hosts_up if h else 0,
                ports_found=p_row.ports_found if p_row else 0,
                open_ports=p_row.open_ports if p_row else 0,
                services_detected=service_lookup.get(sid, 0),
            )

    # Collect parse_error_ids for batch error lookup
    error_ids = [j.parse_error_id for j in jobs if j.parse_error_id is not None]
    error_lookup: Dict[int, models.ParseError] = {}
    if error_ids:
        errors = (
            db.query(models.ParseError)
            .filter(models.ParseError.id.in_(error_ids))
            .all()
        )
        error_lookup = {e.id: e for e in errors}

    # Scan type lookup
    scan_type_lookup: Dict[int, str] = {}
    if scan_ids:
        scan_rows = (
            db.query(models.Scan.id, models.Scan.scan_type, models.Scan.tool_name)
            .filter(models.Scan.id.in_(scan_ids))
            .all()
        )
        scan_type_lookup = {r.id: r.scan_type for r in scan_rows}
        # Also use scan tool_name as fallback
        scan_tool_lookup = {r.id: r.tool_name for r in scan_rows}

    # Build response items
    items: List[IngestionResultItem] = []
    for job in jobs:
        duration = None
        if job.started_at and job.completed_at:
            duration = (job.completed_at - job.started_at).total_seconds()

        tool = job.tool_name
        scan_type = None
        if job.scan_id:
            scan_type = scan_type_lookup.get(job.scan_id)
            if not tool:
                tool = scan_tool_lookup.get(job.scan_id) if scan_ids else None

        item = IngestionResultItem(
            id=job.id,
            original_filename=job.original_filename,
            status=job.status,
            file_size=job.file_size,
            tool_name=tool,
            scan_type=scan_type,
            scan_id=job.scan_id,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_seconds=duration,
            progress=job.progress,
        )

        # Attach stats for completed jobs
        if job.scan_id and job.scan_id in stats_by_scan:
            item.stats = stats_by_scan[job.scan_id]

        # Attach error info for failed jobs
        if job.parse_error_id and job.parse_error_id in error_lookup:
            pe = error_lookup[job.parse_error_id]
            item.error = IngestionResultError(
                error_type=pe.error_type,
                error_message=pe.error_message,
                user_message=pe.user_message,
            )
        elif job.status == "failed" and job.error_message:
            item.error = IngestionResultError(
                error_type="processing_error",
                error_message=job.error_message,
                user_message=job.error_message,
            )

        items.append(item)

    # Summary across all completed jobs in the project
    summary_host = (
        db.query(
            func.count(models.HostScanHistory.id).label("total_hosts"),
            func.count(
                case((models.HostScanHistory.state_at_scan == "up", 1))
            ).label("total_hosts_up"),
        )
        .join(models.IngestionJob, models.IngestionJob.scan_id == models.HostScanHistory.scan_id)
        .filter(
            models.IngestionJob.project_id == project.id,
            models.IngestionJob.status == "completed",
        )
        .first()
    )

    summary_port = (
        db.query(
            func.count(models.PortScanHistory.id).label("total_ports"),
            func.count(
                case((models.PortScanHistory.state_at_scan == "open", 1))
            ).label("total_open_ports"),
        )
        .join(models.IngestionJob, models.IngestionJob.scan_id == models.PortScanHistory.scan_id)
        .filter(
            models.IngestionJob.project_id == project.id,
            models.IngestionJob.status == "completed",
        )
        .first()
    )

    status_counts = (
        db.query(
            models.IngestionJob.status,
            func.count(models.IngestionJob.id),
        )
        .filter(models.IngestionJob.project_id == project.id)
        .group_by(models.IngestionJob.status)
        .all()
    )
    status_map = dict(status_counts)

    summary = {
        "total_completed": status_map.get("completed", 0),
        "total_failed": status_map.get("failed", 0),
        "total_queued": status_map.get("queued", 0),
        "total_processing": status_map.get("processing", 0),
        "total_hosts": summary_host.total_hosts if summary_host else 0,
        "total_hosts_up": summary_host.total_hosts_up if summary_host else 0,
        "total_ports": summary_port.total_ports if summary_port else 0,
        "total_open_ports": summary_port.total_open_ports if summary_port else 0,
    }

    return IngestionResultsResponse(items=items, total=total, summary=summary)


@router.get(
    "/",
    response_model=List[ParseErrorSummary],
    responses=_ANALYST_RESPONSES,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="List parse errors (analyst)",
)
def get_parse_errors(
    # v2.86.4 — pagination caps added.
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    status: str = Query(None, description="Filter by status: unresolved, reviewed, fixed, ignored"),
    error_type: str = Query(
        None,
        description="Filter by error_type (parsing_error, validation_error, format_error, …) — v2.86.2.",
    ),
    file_type: str = Query(
        None,
        description="Filter by file_type (nmap_xml, eyewitness_json, masscan_xml, …) — v2.86.2.",
    ),
    search: str = Query(
        None,
        max_length=200,
        description=(
            "Case-insensitive substring match across filename, error_message, "
            "and user_message (v2.86.2).  Pushed server-side so the page no "
            "longer client-filters a partial slice."
        ),
    ),
    sort_by: str = Query(
        "created_at",
        pattern="^(created_at|filename|status|error_type)$",
        description="Sort key (v2.86.2).",
    ),
    sort_order: str = Query(
        "desc",
        pattern="^(asc|desc)$",
        description="Sort direction (v2.86.2).",
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get list of parsing errors. Requires analyst role.

    Returns summary data only (no file_preview or traceback)."""
    query = db.query(models.ParseError).filter(models.ParseError.project_id == project.id)

    if status:
        query = query.filter(models.ParseError.status == status)
    if error_type:
        query = query.filter(models.ParseError.error_type == error_type)
    if file_type:
        query = query.filter(models.ParseError.file_type == file_type)
    if search:
        # Escape SQL LIKE metacharacters in user input so a literal "%"
        # in a filename matches its literal form, not "any prefix".
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        query = query.filter(
            or_(
                models.ParseError.filename.ilike(like),
                models.ParseError.error_message.ilike(like),
                models.ParseError.user_message.ilike(like),
            )
        )

    # v2.86.2 — sort by selectable column, default created_at desc.
    sort_col_map = {
        "created_at": models.ParseError.created_at,
        "filename": models.ParseError.filename,
        "status": models.ParseError.status,
        "error_type": models.ParseError.error_type,
    }
    sort_col = sort_col_map[sort_by]
    if sort_order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    errors = query.offset(skip).limit(limit).all()
    return errors


@router.get(
    "/stats/summary",
    response_model=ParseErrorStatsResponse,
    responses=_ANALYST_RESPONSES,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Parse error statistics (analyst)",
)
def get_parse_error_stats(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get parse error statistics by status category. Requires analyst role."""
    status_counts = (
        db.query(models.ParseError.status, func.count(models.ParseError.id))
        .filter(models.ParseError.project_id == project.id)
        .group_by(models.ParseError.status)
        .all()
    )
    counts = dict(status_counts)
    total = sum(counts.values())

    return {
        "total_errors": total,
        "unresolved": counts.get("unresolved", 0),
        "reviewed": counts.get("reviewed", 0),
        "fixed": counts.get("fixed", 0),
        "ignored": counts.get("ignored", 0),
    }


@router.get(
    "/{error_id}",
    response_model=ParseErrorSafe,
    responses={**_ANALYST_RESPONSES, 404: {"description": "Parse error not found"}},
    summary="Get parse error details (analyst)",
)
def get_parse_error(
    error_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_project_role(ProjectRole.ANALYST)),
    project: Project = Depends(get_current_project),
):
    """Get detailed parse error by ID. Requires analyst role.

    Non-admin users receive the record with traceback details stripped from
    error_details and file_preview redacted to prevent information leakage."""
    error = db.query(models.ParseError).filter(
        models.ParseError.id == error_id,
        models.ParseError.project_id == project.id,
    ).first()
    if not error:
        raise HTTPException(status_code=404, detail="Parse error not found")

    result = {
        "id": error.id,
        "filename": error.filename,
        "file_type": error.file_type,
        "file_size": error.file_size,
        "error_type": error.error_type,
        "error_message": error.error_message,
        "user_message": error.user_message,
        "status": error.status,
        "created_at": error.created_at.isoformat() if error.created_at else None,
        "updated_at": error.updated_at.isoformat() if error.updated_at else None,
    }

    # Only admins see raw traceback and file preview
    if current_user.role == UserRole.ADMIN:
        result["error_details"] = error.error_details
        result["file_preview"] = error.file_preview
    else:
        # Strip traceback from error_details, keep other diagnostic info
        if error.error_details and isinstance(error.error_details, dict):
            safe_details = {k: v for k, v in error.error_details.items() if k != "traceback"}
            result["error_details"] = safe_details if safe_details else None
        else:
            result["error_details"] = None
        result["file_preview"] = None

    return result


@router.put(
    "/{error_id}/status",
    response_model=MessageResponse,
    responses={**_ANALYST_RESPONSES, 404: {"description": "Parse error not found"}},
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Update parse error status (analyst)",
)
def update_parse_error_status(
    error_id: int,
    status: str = Query(..., description="New status", pattern="^(unresolved|reviewed|fixed|ignored)$"),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Update parse error status. Requires analyst role."""
    error = db.query(models.ParseError).filter(
        models.ParseError.id == error_id,
        models.ParseError.project_id == project.id,
    ).first()
    if not error:
        raise HTTPException(status_code=404, detail="Parse error not found")

    error.status = status
    db.commit()
    db.refresh(error)

    return {"message": f"Parse error status updated to {status}"}


@router.delete(
    "/{error_id}",
    response_model=DeleteParseErrorResponse,
    responses={**_ANALYST_RESPONSES, 404: {"description": "Parse error not found"}},
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Delete parse error (analyst)",
)
def delete_parse_error(
    error_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Delete a parse error record. Requires analyst role."""
    error = db.query(models.ParseError).filter(
        models.ParseError.id == error_id,
        models.ParseError.project_id == project.id,
    ).first()
    if not error:
        raise HTTPException(status_code=404, detail="Parse error not found")

    try:
        jobs_cleared = (
            db.query(models.IngestionJob)
            .filter(models.IngestionJob.parse_error_id == error_id)
            .update({"parse_error_id": None}, synchronize_session=False)
        )

        db.delete(error)
        db.commit()

        return {
            "message": "Parse error deleted successfully",
            "jobs_updated": jobs_cleared,
        }
    except Exception as exc:  # pragma: no cover - defensive
        db.rollback()
        logger.exception("Failed to delete parse error %d", error_id)
        raise HTTPException(status_code=500, detail="Failed to delete parse error")
