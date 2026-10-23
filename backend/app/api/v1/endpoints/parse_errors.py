"""Parse-error rows (``/projects/{id}/parse-errors``).

The Ingestion results view is ``endpoints/ingestion_results.py``, at
``/ingestion-results`` (it was ``/parse-errors/ingestion-results`` until
2026-10-22).  ``{error_id}`` is matched as an integer only, so a word after
``/parse-errors/`` — the old view's path among them — is a 404, not a 422.
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from pydantic import BaseModel

from app.db.session import get_db
from app.db import models
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
    # The id breaks ties, as on the results list above: sorted by a column
    # many rows share (status, error type), an order with no tiebreaker is a
    # different order on every page read.  Newest first within a tie.
    order = sort_col.asc() if sort_order == "asc" else sort_col.desc()
    query = query.order_by(order, models.ParseError.id.desc())

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
    "/{error_id:int}",
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
    "/{error_id:int}/status",
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
    "/{error_id:int}",
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
