from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.db.session import disable_statement_timeout, get_db
from app.api.deps import get_current_user
from app.api.deps import get_current_project, require_project_role
from app.core.security import check_permissions
from app.db.models_auth import UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.api.params import HostFilterParams
from app.db.models import ReportJob
# ReportGenerator now lives in the service layer; re-exported here so the
# endpoints (and existing test imports of `from ...reports import ReportGenerator`)
# keep working.
from app.services.report_generator import ReportGenerator
from app.services.report_job_service import PAGE_OWNED_REPORT_TYPES, ReportJobService
from app.schemas.schemas import ReportJobSchema
from app.services.csv_utils import csv_safe as _csv_safe, safe_csv_row as _safe_csv_row  # noqa: F401
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# What this router serves (the HTML host report, the agent package and the
# Markdown bundle were retired with "Export hosts", owner 2026-10-07):
#   - the inventory CSV, streamed (``/hosts/csv``);
#   - the inventory JSON, a report job (``POST /jobs?format=json``);
#   - the systemic executive briefing (``/systemic.html``);
#   - the report-job routes, which client-report previews also poll and
#     download through.
#
# Downloads egress project data (and jobs mutate shared queue state).
# Same policy as /export: VIEWERs are read-only-inventory and cannot pull
# reports; AUDITOR and above may. Gate the whole router — every route is under
# /projects/{project_id}, so the role check reads project_id from the path.
router = APIRouter(dependencies=[
    Depends(get_current_user),
    Depends(require_project_role(ProjectRole.AUDITOR)),
])

# Client-report renders (``client_reports.CLIENT_REPORT_JOB``): listed, polled
# and downloaded here, changed only by the Reports page's own routes.
CLIENT_REPORT_TYPE = "client"


@router.get("/hosts/csv")
def generate_hosts_csv_report(
    filters: HostFilterParams = Depends(),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """The host inventory as CSV — one row per host matching the filters,
    streamed."""
    # A streamed export: its queries run after the response has started and
    # cover every matching host — exempt from the API statement timeout
    # (review 2026-10-01 R23), which is for interactive requests.
    disable_statement_timeout(db)
    # The full filter context (incl. has_exploit_available, has_test_execution,
    # has_web_interface, tech, tags, subnet_labels, assigned_to) — derived from
    # the shared HostFilterParams so a download can never narrow to fewer
    # filters than the visible /hosts list.
    filter_kwargs = {k: v for k, v in filters.as_builder_kwargs().items() if v is not None}

    generator = ReportGenerator(db, current_user, project_id=project.id)
    # Stream the inventory over a chunked cursor — no host cap, bounded memory.
    filename = f"hosts_inventory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        generator.iter_inventory_csv(filter_kwargs),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/systemic.html")
def generate_systemic_executive_report(
    site: Optional[str] = Query(
        None, max_length=255,
        description="Site name: scope the hotspot / outlier / profile sections to this site (estate-wide patterns stay estate-wide).",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Lightweight executive systemic report (standalone HTML).

    The estate-wide systemic patterns + site/subnet hotspots only — nothing
    per host — as a self-contained HTML file for sharing at a high-level
    meeting.  Project-wide (no host filters); the systemic payload is bounded,
    so this renders synchronously rather than via the async report-job
    pipeline."""
    generator = ReportGenerator(db, current_user, project_id=project.id)
    html_doc = generator.generate_systemic_executive_html(site=site or None)
    slug = ""
    if site:
        slug = "_" + "".join(ch if ch.isalnum() else "-" for ch in site)[:40]
    filename = f"systemic_insights{slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    return Response(
        content=html_doc,
        media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Report jobs.  The inventory JSON carries every matching host's full record,
# so it is written by the dedicated report worker instead of this request
# thread: the dialog enqueues a job, polls its status, then downloads the
# artifact.  The CSV above streams synchronously and is NOT enqueued.
# ---------------------------------------------------------------------------

# The one host download that is a job.  (Client-report renders are jobs too;
# they are enqueued by the Reports page's own routes, never here.)
_ASYNC_FORMAT_PATTERN = "^json$"


@router.post("/jobs", response_model=ReportJobSchema, status_code=202)
def enqueue_report_job(
    format: str = Query(..., pattern=_ASYNC_FORMAT_PATTERN, description="The download's format: json."),
    filters: HostFilterParams = Depends(),
    report_type: str = Query(
        "comprehensive", pattern="^(inventory|comprehensive)$",
        description="'comprehensive' adds the project-wide findings, hotspots and systemic "
                    "roll-ups after the hosts; 'inventory' is the hosts alone.",
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Enqueue the inventory JSON download (returns the job in ``queued`` state).

    The same full filter context the visible /hosts list uses — derived from the
    shared HostFilterParams so a download can never narrow to fewer filters — is
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
    service.enqueue_job(job.id, db=db)
    return job


@router.get("/jobs", response_model=List[ReportJobSchema])
def list_report_jobs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Recent report jobs for this project (newest first), excluding dismissed.
    Client-report renders (``report_type='client'``) belong to the Reports
    page and a contact's remediation list (``'remediation'``) to the
    Remediation page — not this list of inventory downloads."""
    return (
        db.query(ReportJob)
        .filter(
            ReportJob.project_id == project.id, ReportJob.dismissed_at.is_(None),
            ReportJob.report_type.notin_(PAGE_OWNED_REPORT_TYPES),
        )
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
    )


def _job_for_change(db: Session, project: Project, user, job_id: int) -> ReportJob:
    """The job, for a route that CHANGES it (dismiss / retry / cancel) —
    review 2026-10-01 R17.

    The router's floor is auditor: reading and downloading exports.  Changing
    a job is a write, so it needs an analyst — or the person who requested
    that export, so an auditor still manages their own.  A client-report job
    is refused here whoever asks: it is half of a report's state (cancelling
    a queued issue render left the report PENDING with no job), and the
    Reports page has its own routes for it.
    """
    job = (
        db.query(ReportJob)
        .filter(ReportJob.id == job_id, ReportJob.project_id == project.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found")
    if job.report_type == CLIENT_REPORT_TYPE:
        raise HTTPException(
            status_code=409,
            detail="This job renders a client report and belongs to the Reports page: "
                   "preview the draft again, or use \"Render again\" on the issued report.",
        )
    if job.requested_by_id is not None and job.requested_by_id == user.id:
        return job
    if not is_project_analyst(db, project.id, user):
        raise HTTPException(
            status_code=403,
            detail="Insufficient project role. Required: analyst (or the person who requested this export).",
        )
    return job


def is_project_analyst(db: Session, project_id: int, user) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    role = (
        db.query(ProjectMembership.role)
        .filter(ProjectMembership.project_id == project_id, ProjectMembership.user_id == user.id)
        .scalar()
    )
    role = getattr(role, "value", role)
    return bool(role) and check_permissions(role, ProjectRole.ANALYST.value)


@router.post("/jobs/{job_id}/dismiss", response_model=ReportJobSchema)
def dismiss_report_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Acknowledge a report job (drops it from the recent-jobs list)."""
    job = _job_for_change(db, project, current_user, job_id)
    job.dismissed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job


@router.post("/jobs/{job_id}/retry", response_model=ReportJobSchema)
def retry_report_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Re-queue a failed report job. 409 if it isn't in a failed state."""
    _job_for_change(db, project, current_user, job_id)
    service = ReportJobService()
    try:
        job = service.retry_job(db, job_id=job_id, project_id=project.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if job is None:
        raise HTTPException(status_code=404, detail="Report job not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=ReportJobSchema)
def cancel_report_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Cancel a queued report job before the worker claims it. 409 if it's
    already processing or in a terminal state."""
    _job_for_change(db, project, current_user, job_id)
    service = ReportJobService()
    try:
        job = service.cancel_job(db, job_id=job_id, project_id=project.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if job is None:
        raise HTTPException(status_code=404, detail="Report job not found")
    return job
