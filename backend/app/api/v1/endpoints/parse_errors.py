import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc, func, case, or_
from pydantic import BaseModel

from app.db.session import get_db
from app.db import models
from app.services.format_registry import format_label
from app.services.host_query_common import escape_like
from app.services.import_attention_service import annotate_jobs, superseded_import_condition
from app.services.operations_read_service import blocked_import_condition
from app.services.staged_import_service import (
    DISCARDED_MESSAGE,
    EXPIRED_MESSAGE_PREFIX,
    file_retained,
    retained_until,
)
from app.schemas.schemas import ParseError, ParseErrorSummary, ParseErrorCreate
from app.api.v1.endpoints.auth import get_current_user, require_role
from app.db.models_auth import User, UserRole
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project, ProjectRole

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _expired_job():
    """A staged upload nobody started within the window: its job is written
    ``failed`` (``staged_import_service.expire_staged_jobs``), but nothing
    failed — the Scans page says "expired before review", and so does this
    list (UX review 2026-09-24; it counted them under Failed)."""
    Job = models.IngestionJob
    return and_(Job.status == "failed", Job.error_message.like(f"{EXPIRED_MESSAGE_PREFIX}%"))


def _discarded_job():
    """A staged upload the operator discarded — also written ``failed``."""
    Job = models.IngestionJob
    return and_(Job.status == "failed", Job.error_message == DISCARDED_MESSAGE)

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
    # NB: this is the INGESTION JOB id, not a ParseError id. The two live in
    # separate tables with independent sequences that overlap heavily (most
    # ingestions succeed, so job ids climb past the dense low range of parse
    # error ids), so passing one where the other is expected does not 404 —
    # it silently returns a DIFFERENT file's error. Use `parse_error_id`
    # below for anything that addresses the ParseError itself.
    id: int
    # The ParseError this job produced, when it produced one. Previously the
    # id was resolved server-side and then dropped, leaving callers to guess
    # with `id` (see /parse-errors/{error_id}).
    parse_error_id: Optional[int] = None
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
    # v2.351.0 — the format chain: what was detected first, what the
    # operator chose instead (if anything), what actually parsed the file,
    # and the tool they named.  Keys from app/services/format_registry.py,
    # with their labels so the page need not know the registry.
    detected_file_type: Optional[str] = None
    detected_format_label: Optional[str] = None
    format_override: Optional[str] = None
    format_override_label: Optional[str] = None
    final_file_type: Optional[str] = None
    final_format_label: Optional[str] = None
    source_tool: Optional[str] = None
    # v2.354.0 — whether the uploaded bytes are still on disk (so "review
    # the format and retry" / re-process need no re-upload) and until when.
    file_retained: bool = False
    retained_until: Optional[datetime] = None
    # v2.363.0 — "a partial job must stay visibly partial in every list that
    # shows it" (v2.332.0), and this list did not: a truncated file read
    # "Completed".  With what was lost, and whether someone has dismissed it.
    partial: bool = False
    skipped_count: int = 0
    parser_warnings: Optional[str] = None
    dismissed_at: Optional[datetime] = None
    # v2.403.0 — a failed or partial import whose file a LATER job imported
    # cleanly names that job, and is not "needs attention"; and the specific
    # reason a failed job did not import (the parser's own message, not the
    # generic "Failed to parse the file …" sentence).
    superseded_by_job_id: Optional[int] = None
    failure_reason: Optional[str] = None
    # v2.418.0 — how many lines the parser did not interpret, and in how
    # many shapes; the shapes are GET …/ingestion-results/{id}/uninterpreted.
    uninterpreted_total: int = 0
    uninterpreted_distinct: int = 0
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
        description=(
            "Filter by IngestionJob.status (queued, processing, completed, failed) — v2.86.2; "
            "or a view: `needs_attention` (failed or partial, not dismissed, not superseded) or "
            "`superseded` (failed or partial, not dismissed, file imported by a later job) — v2.403.0; "
            "`expired` (staged, never started, removed) and `discarded` (staged, discarded by an "
            "operator) — v2.408.0. `failed` excludes those two: nothing failed in them."
        ),
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
    if status == "needs_attention":
        # v2.363.0 — not a job status: failed OR finished partial, and not
        # dismissed.  The same condition the Operations blockers count, so the
        # number on "Inspect import errors" and this list cannot disagree.
        base = base.filter(blocked_import_condition())
    elif status == "superseded":
        # v2.403.0 — not a job status either: failed or partial, not
        # dismissed, and the same file imported cleanly by a later job — what
        # "Dismiss N superseded" clears.
        base = base.filter(models.IngestionJob.dismissed_at.is_(None), superseded_import_condition())
    elif status == "expired":
        base = base.filter(_expired_job())
    elif status == "discarded":
        base = base.filter(_discarded_job())
    elif status == "failed":
        # Failed means an import went wrong — not a staged upload that expired
        # or was discarded (those have their own views). error_message may be
        # NULL, which NOT LIKE would drop.
        base = base.filter(
            models.IngestionJob.status == "failed",
            ~and_(models.IngestionJob.error_message.isnot(None), or_(_expired_job(), _discarded_job())),
        )
    elif status:
        base = base.filter(models.IngestionJob.status == status)
    if tool:
        base = base.filter(func.lower(models.IngestionJob.tool_name) == tool.lower())
    if search:
        escaped = escape_like(search)
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

    jobs = annotate_jobs(db, base.order_by(order_clause).offset(skip).limit(limit).all())

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
            parse_error_id=job.parse_error_id,
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
            detected_file_type=job.detected_file_type,
            detected_format_label=format_label(job.detected_file_type),
            format_override=job.format_override,
            format_override_label=format_label(job.format_override),
            final_file_type=job.final_file_type,
            final_format_label=format_label(job.final_file_type),
            source_tool=job.source_tool,
            file_retained=file_retained(job),
            retained_until=retained_until(job),
            partial=bool(job.partial),
            skipped_count=int(job.skipped_count or 0),
            parser_warnings=job.parser_warnings,
            dismissed_at=job.dismissed_at,
            superseded_by_job_id=job.superseded_by_job_id,
            failure_reason=job.failure_reason,
            uninterpreted_total=int((job.uninterpreted_lines or {}).get("total") or 0),
            uninterpreted_distinct=int((job.uninterpreted_lines or {}).get("distinct") or 0),
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
    needs_attention, superseded, expired, discarded = (
        db.query(
            func.count(case((blocked_import_condition(), models.IngestionJob.id))),
            func.count(case((
                and_(models.IngestionJob.dismissed_at.is_(None), superseded_import_condition()),
                models.IngestionJob.id,
            ))),
            func.count(case((_expired_job(), models.IngestionJob.id))),
            func.count(case((_discarded_job(), models.IngestionJob.id))),
        )
        .filter(models.IngestionJob.project_id == project.id)
        .one()
    )
    expired = int(expired or 0)
    discarded = int(discarded or 0)

    summary = {
        "total_needs_attention": int(needs_attention or 0),
        "total_superseded": int(superseded or 0),
        "total_staged": status_map.get("staged", 0),
        "total_completed": status_map.get("completed", 0),
        # Failed imports only; the expired and discarded staged uploads that
        # share the job status are counted apart (they sum to the old figure).
        "total_failed": status_map.get("failed", 0) - expired - discarded,
        "total_expired": expired,
        "total_discarded": discarded,
        "total_queued": status_map.get("queued", 0),
        "total_processing": status_map.get("processing", 0),
        "total_hosts": summary_host.total_hosts if summary_host else 0,
        "total_hosts_up": summary_host.total_hosts_up if summary_host else 0,
        "total_ports": summary_port.total_ports if summary_port else 0,
        "total_open_ports": summary_port.total_open_ports if summary_port else 0,
    }

    return IngestionResultsResponse(items=items, total=total, summary=summary)


class UninterpretedShape(BaseModel):
    kind: str
    shape: str
    count: int


class UninterpretedLinesResponse(BaseModel):
    """What an import's parser did not interpret (v2.418.0), as redacted
    shapes: the structure of each line with its values replaced
    (app/services/line_shapes.py)."""
    job_id: int
    original_filename: str
    tool_name: Optional[str] = None
    total: int = 0
    distinct: int = 0
    shapes: List[UninterpretedShape] = []


@router.get(
    "/ingestion-results/{job_id:int}/uninterpreted",
    response_model=UninterpretedLinesResponse,
    responses=_ANALYST_RESPONSES,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="The lines an import did not interpret, as redacted shapes",
)
def get_uninterpreted_lines(
    job_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    job = (
        db.query(models.IngestionJob)
        .filter(models.IngestionJob.id == job_id, models.IngestionJob.project_id == project.id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Import not found")
    receipt = job.uninterpreted_lines or {}
    return UninterpretedLinesResponse(
        job_id=job.id, original_filename=job.original_filename, tool_name=job.tool_name,
        total=int(receipt.get("total") or 0), distinct=int(receipt.get("distinct") or 0),
        shapes=[UninterpretedShape(**s) for s in receipt.get("shapes") or []],
    )


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
        escaped = escape_like(search)
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
