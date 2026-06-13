from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db import models
from app.core.config import settings
from app.api.v1.endpoints.auth import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.db.models_project import Project
from app.api.v1.endpoints.hosts import HostFilterParams
from app.db.models import ReportJob
# ReportGenerator now lives in the service layer; re-exported here so the
# endpoints (and existing test imports of `from ...reports import ReportGenerator`)
# keep working.
from app.services.report_generator import ReportGenerator, _id_chunks
from app.services.report_job_service import ReportJobService
from app.schemas.schemas import ReportJobSchema
from app.services.csv_utils import csv_safe as _csv_safe, safe_csv_row as _safe_csv_row  # noqa: F401
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/hosts/csv")
def generate_hosts_csv_report(
    filters: HostFilterParams = Depends(),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Generate CSV report of hosts based on filters"""
    # The full filter context (incl. has_exploit_available, has_test_execution,
    # has_web_interface, tech, tags, subnet_labels, assigned_to) — derived from
    # the shared HostFilterParams so reports can never narrow to fewer filters
    # than the visible /hosts list.  None-stripped for the html/agent/markdown
    # generators that display the applied filters.
    filter_kwargs = {k: v for k, v in filters.as_builder_kwargs().items() if v is not None}

    generator = ReportGenerator(db, current_user, project_id=project.id)
    # Stream the inventory over a chunked cursor — no host cap, bounded memory,
    # so a project with >cap hosts still gets a complete CSV.
    filename = f"hosts_inventory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        generator.iter_inventory_csv(filter_kwargs),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

@router.get("/hosts/html")
def generate_hosts_html_report(
    filters: HostFilterParams = Depends(),
    report_type: str = Query(
        "comprehensive",
        pattern="^(inventory|comprehensive)$",
        description="'comprehensive' (full security report: findings + hotspots + host detail) or 'inventory' (concise host list, no project-wide roll-ups).",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Generate HTML report of hosts based on filters"""
    # The full filter context (incl. has_exploit_available, has_test_execution,
    # has_web_interface, tech, tags, subnet_labels, assigned_to) — derived from
    # the shared HostFilterParams so reports can never narrow to fewer filters
    # than the visible /hosts list.  None-stripped for the html/agent/markdown
    # generators that display the applied filters.
    filter_kwargs = {k: v for k, v in filters.as_builder_kwargs().items() if v is not None}

    generator = ReportGenerator(db, current_user, project_id=project.id)
    # Stream the dossiers chunk-by-chunk so peak memory ≈ one chunk even at the
    # high cap.  Resolve ids + truncation first: the StreamingResponse flushes
    # headers (incl. X-Report-Truncated) before the body.
    host_ids = generator.resolve_html_host_ids(filter_kwargs)
    filename = f"hosts_{report_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

    return StreamingResponse(
        generator.iter_html_report(host_ids, report_type, filter_kwargs),
        media_type="text/html",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Report-Truncated": "true" if generator.report_truncated else "false",
        },
    )


# ---------------------------------------------------------------------------
# Async report jobs.  The heavy in-memory formats (pdf / json / agent-package /
# markdown-bundle) build the whole document in worker memory, so they run on the
# dedicated report worker instead of this request thread: the dialog enqueues a
# job, polls its status, then downloads the artifact.  CSV + HTML above stream
# synchronously (memory-safe) and are NOT enqueued.
# ---------------------------------------------------------------------------

# PDF removed in v2.196.1 (slow + degraded WeasyPrint render of the screen-oriented
# dossier HTML; the interactive HTML report is the functional handover).
_ASYNC_FORMAT_PATTERN = "^(json|agent-package|markdown-bundle)$"


@router.post("/jobs", response_model=ReportJobSchema, status_code=202)
def enqueue_report_job(
    format: str = Query(..., pattern=_ASYNC_FORMAT_PATTERN, description="Async export format."),
    filters: HostFilterParams = Depends(),
    report_type: str = Query("comprehensive", pattern="^(inventory|comprehensive)$"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Enqueue an async report-generation job (returns it in ``queued`` state).

    The same full filter context the visible /hosts list uses — derived from the
    shared HostFilterParams so a report can never narrow to fewer filters — is
    stored on the job and replayed on the worker.  Poll ``GET /reports/jobs/{id}``
    and download via ``GET /reports/jobs/{id}/download`` once ``completed``.
    """
    filter_kwargs = {k: v for k, v in filters.as_builder_kwargs().items() if v is not None}
    service = ReportJobService()
    job = service.create_job(
        db,
        project_id=project.id,
        requested_by_id=current_user.id,
        format=format,
        report_type=report_type,
        filters=filter_kwargs,
    )
    service.enqueue_job(job.id)
    return job


@router.get("/jobs", response_model=List[ReportJobSchema])
def list_report_jobs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Recent report jobs for this project (newest first), excluding dismissed."""
    return (
        db.query(ReportJob)
        .filter(ReportJob.project_id == project.id, ReportJob.dismissed_at.is_(None))
        .order_by(ReportJob.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/jobs/{job_id}", response_model=ReportJobSchema)
def get_report_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    job = (
        db.query(ReportJob)
        .filter(ReportJob.id == job_id, ReportJob.project_id == project.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found")
    return job


@router.get("/jobs/{job_id}/download")
def download_report_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Stream a completed report job's artifact."""
    from fastapi.responses import FileResponse

    job = (
        db.query(ReportJob)
        .filter(ReportJob.id == job_id, ReportJob.project_id == project.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found")
    if job.status != "completed" or not job.result_path:
        raise HTTPException(status_code=409, detail=f"Report is not ready (status: {job.status}).")
    if not Path(job.result_path).is_file():
        raise HTTPException(status_code=410, detail="Report artifact has expired or been removed.")
    return FileResponse(
        path=job.result_path,
        media_type=job.media_type or "application/octet-stream",
        filename=job.result_filename or f"report_{job.id}",
        headers={"X-Report-Truncated": "true" if job.truncated else "false"},
    )


@router.post("/jobs/{job_id}/dismiss", response_model=ReportJobSchema)
def dismiss_report_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Acknowledge a report job (drops it from the recent-jobs list)."""
    job = (
        db.query(ReportJob)
        .filter(ReportJob.id == job_id, ReportJob.project_id == project.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found")
    job.dismissed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job
