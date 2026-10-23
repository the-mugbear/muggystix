"""One row per upload: what the Scans page's import history shows of an
upload (2.524.0, owner decision 2026-10-22, G15 — it was the page's
Ingestion results view).  The history decides the ORDER
(``GET /scans/history``, with its status filters) and reads its upload rows
here by id (``?ids=``); analysts and above only.

Served at ``/projects/{id}/ingestion-results``.  Until 2026-10-22 these two
routes lived in ``parse_errors.py`` under ``/parse-errors/ingestion-results``;
the old paths are a 404 (owner decision — no alias).  ``parse_errors.py``
keeps the routes that really are about parse-error rows.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, get_current_user, require_project_role
from app.db import models
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.schemas.ingestion_results import IngestionResultItem
from app.services import ingestion_results_service

router = APIRouter(dependencies=[Depends(get_current_user)])


_ANALYST_RESPONSES = {
    401: {"description": "Not authenticated"},
    403: {"description": "Insufficient permissions — analyst role required"},
}


class IngestionResultsResponse(BaseModel):
    items: List[IngestionResultItem]
    total: int
    summary: Dict[str, Any]  # total_completed, total_failed, total_hosts, total_ports


@router.get(
    "",
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
    ids: Optional[str] = Query(
        None,
        max_length=2000,
        description=(
            "Comma-separated job ids — only these rows (2.524.0). The Scans "
            "page's import history lists its unsuccessful uploads in the "
            "order `GET /scans/history` decides and fetches each page's rows "
            "by id here, as it fetches its scans and batches."
        ),
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
    wanted_ids: Optional[List[int]] = None
    if ids is not None:
        try:
            wanted_ids = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=422, detail="ids must be comma-separated integers")
    items, total = ingestion_results_service.list_results(
        db, project.id, status=status, tool=tool, search=search,
        sort_by=sort_by, sort_order=sort_order, skip=skip, limit=limit, ids=wanted_ids,
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
    "/{job_id:int}/uninterpreted",
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
