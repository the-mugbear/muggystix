import logging
from typing import List

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user, require_role
from app.api.deps import get_current_project, require_project_role
from app.db.models import IngestionJob
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.schemas.schemas import FileUploadResponse, IngestionJobSchema
from app.services.ingestion_service import ingestion_service, ALLOWED_UPLOAD_EXTENSIONS

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

# Single source of truth lives on the ingestion service (enforced in
# create_job for every caller).  Kept here as an alias so this path can still
# reject early, before touching disk, with a friendly message.
ALLOWED_EXTENSIONS = ALLOWED_UPLOAD_EXTENSIONS


class CancelJobResponse(BaseModel):
    job_id: int
    status: str
    message: str


def _require_job_access(job: IngestionJob, current_user: User) -> None:
    """Ensure the current user owns the job or is an admin."""
    if current_user.role == UserRole.ADMIN:
        return
    if job.submitted_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized for this job")


@router.post(
    "/",
    response_model=FileUploadResponse,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Insufficient permissions — analyst role required"},
    },
    summary="Upload scan file (analyst)",
)
async def upload_scan_file(
    file: UploadFile = File(...),
    enrich_dns: bool = False,
    dns_server: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Upload a scan file for background ingestion. Requires analyst role.

    Supported file types: .xml, .json, .csv, .txt, .gnmap, .nessus
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    if not any(file.filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail=(
                "File type not allowed. Supported types: "
                + ", ".join(sorted(ALLOWED_EXTENSIONS))
            ),
        )

    options = {"enrich_dns": enrich_dns}
    if dns_server:
        from app.services.dns_validation import validate_dns_server
        options["dns_server"] = validate_dns_server(dns_server)

    options["project_id"] = project.id

    try:
        job = await ingestion_service.create_job(
            db=db,
            upload=file,
            submitted_by_id=current_user.id if current_user else None,
            options=options,
        )
    except ValueError as exc:
        logger.warning("Upload rejected: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Failed to queue ingestion job")
        raise HTTPException(status_code=500, detail="Failed to queue ingestion job") from exc

    # Capture values before enqueuing (background thread may touch the row)
    response = FileUploadResponse(
        job_id=job.id,
        filename=job.original_filename,
        status=job.status,
        message="File queued for background processing",
        scan_id=None,
    )

    ingestion_service.enqueue_job(job.id)

    return response


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=CancelJobResponse,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Not authorized for this job"},
        404: {"description": "Job not found"},
        409: {"description": "Job cannot be cancelled in its current state"},
    },
    summary="Cancel ingestion job",
)
def cancel_ingestion_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Cancel a queued or processing ingestion job. Only the job owner or an admin can cancel."""
    job = db.query(IngestionJob).filter(
        IngestionJob.id == job_id,
        IngestionJob.project_id == project.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    _require_job_access(job, current_user)
    if job.status not in ("queued", "processing"):
        raise HTTPException(
            status_code=409,
            detail=f"Job is already {job.status} and cannot be cancelled",
        )
    cancelled = ingestion_service.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(status_code=409, detail="Job could not be cancelled")
    # Re-query instead of refresh — cancel_job uses its own session
    job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
    return {"job_id": job.id, "status": job.status, "message": "Job cancelled"}


@router.post(
    "/jobs/{job_id}/retry",
    response_model=CancelJobResponse,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Not authorized for this job"},
        404: {"description": "Job not found"},
        409: {"description": "Job cannot be retried in its current state"},
    },
    summary="Retry a failed ingestion job (analyst)",
)
def retry_ingestion_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Re-queue a failed ingestion job whose uploaded file is still on disk.

    The file is retained on failure (only successful parses delete it), and
    the worker's orphan reaper already knows how to re-queue, so this just
    exposes that path to the operator: retry a transient failure without
    re-uploading a large scan file. Owner or admin only; analyst role.
    """
    job = db.query(IngestionJob).filter(
        IngestionJob.id == job_id,
        IngestionJob.project_id == project.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    _require_job_access(job, current_user)
    if job.status != "failed":
        raise HTTPException(
            status_code=409,
            detail=f"Only failed jobs can be retried (current status: {job.status!r})",
        )

    result = ingestion_service.requeue_job(job_id)
    if result == "file_missing":
        raise HTTPException(
            status_code=409,
            detail="The uploaded file is no longer on disk — please re-upload to retry.",
        )
    if result != "requeued":
        # not_failed / not_found — lost a race with the reaper or another retry.
        raise HTTPException(status_code=409, detail="Job could not be retried in its current state")
    # requeue_job committed status='queued' in its own session; report that
    # directly rather than re-reading the request session's stale cached row.
    return {"job_id": job_id, "status": "queued", "message": "Job re-queued"}


@router.get(
    "/jobs/{job_id}",
    response_model=IngestionJobSchema,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Not authorized for this job"},
        404: {"description": "Job not found"},
    },
    summary="Get ingestion job details",
)
def get_ingestion_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Get details of an ingestion job. Only the job owner or an admin can view."""
    job = db.query(IngestionJob).filter(
        IngestionJob.id == job_id,
        IngestionJob.project_id == project.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    _require_job_access(job, current_user)
    return job


@router.get(
    "/jobs",
    response_model=List[IngestionJobSchema],
    summary="List ingestion jobs",
)
def list_ingestion_jobs(
    skip: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(25, ge=1, le=100, description="Max jobs to return"),
    status: str | None = Query(None, description="Filter by status (queued, processing, completed, failed)"),
    include_dismissed: bool = Query(
        False,
        description=(
            "If true, also return failed jobs that the operator has "
            "dismissed (v2.86.2).  Default false matches the Scans page "
            "Ingestion Queue, which wants only live + unacked rows."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """List ingestion jobs.  Admins see all jobs in the project; other
    users see only their own.  Supports offset/limit pagination and an
    optional status filter so a future UI can expose a dead-letter
    view (``status=failed``) alongside the live queue.

    Response shape is a plain array for backwards compatibility with
    v2.9.7 clients.  The dead-letter UI surface (deferred to v2.10.0)
    will either switch to an envelope endpoint or read pagination
    metadata from response headers — don't change this shape until
    that frontend work is queued, or the Scans page crashes.
    """
    query = db.query(IngestionJob).filter(IngestionJob.project_id == project.id)
    if current_user.role != UserRole.ADMIN:
        query = query.filter(IngestionJob.submitted_by_id == current_user.id)
    if status:
        query = query.filter(IngestionJob.status == status)
    if not include_dismissed:
        query = query.filter(IngestionJob.dismissed_at.is_(None))
    jobs = query.order_by(desc(IngestionJob.created_at)).offset(skip).limit(limit).all()
    return jobs


@router.post(
    "/jobs/{job_id}/dismiss",
    response_model=IngestionJobSchema,
    summary="Dismiss a failed ingestion job (v2.86.2)",
)
def dismiss_ingestion_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Mark a failed job as dismissed so it stops showing in the
    Ingestion Queue.

    Operator-set "I've seen this" — preserves the row + error message
    for the audit trail / debugging.  Only ``failed`` jobs are
    dismissable; queued/processing rows would be hiding live state, and
    completed rows already don't appear in the queue.  Non-admins can
    only dismiss jobs they submitted, mirroring the list endpoint's
    visibility rule above.
    """
    job = (
        db.query(IngestionJob)
        .filter(IngestionJob.id == job_id, IngestionJob.project_id == project.id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    if current_user.role != UserRole.ADMIN and job.submitted_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot dismiss another user's job")
    if job.status != "failed":
        raise HTTPException(
            status_code=400,
            detail=f"Only failed jobs can be dismissed (current status: {job.status!r})",
        )
    if job.dismissed_at is None:
        from datetime import datetime, timezone
        job.dismissed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)
    return job
