import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from pydantic import BaseModel

from app.db.session import get_db
from app.db import models
from app.schemas.ingestion_results import IngestionResultItem
from app.services import ingestion_results_service
from app.services.host_query_common import escape_like
from app.schemas.schemas import ParseErrorSummary
from app.api.deps import get_current_user
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
            "operator) — v2.408.0. `failed` excludes those two: nothing failed in them. "
            "`failed_dismissed` is the part of `failed` someone has dismissed."
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
    Requires analyst role.  ``ingestion_results_service`` is the one
    implementation; the agents' ``/agent/assist/ingestion-issues`` reads it too.
    """
    items, total = ingestion_results_service.list_results(
        db, project.id, status=status, tool=tool, search=search,
        sort_by=sort_by, sort_order=sort_order, skip=skip, limit=limit,
    )
    summary = ingestion_results_service.summary(db, project.id)
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
    except Exception:  # pragma: no cover - defensive
        db.rollback()
        logger.exception("Failed to delete parse error %d", error_id)
        raise HTTPException(status_code=500, detail="Failed to delete parse error")
