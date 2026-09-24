import logging
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Query
from pathlib import Path
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user, require_role
from app.api.deps import get_current_project, require_project_role
from app.db.models import IngestionJob, ScanBatch
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.schemas.schemas import FileUploadResponse, IngestionJobSchema
from app.services.staged_import_service import (
    detect_for_job, discard_staged_job, reprocess_job, start_staged_job,
)
from app.services.import_attention_service import annotate_jobs, superseding_job_ids
from app.services.ingestion_service import (
    ALLOWED_UPLOAD_EXTENSIONS,
    DuplicateUploadError,
    _transitions,
    ingestion_service,
)
from app.services.job_transitions import JobNotTransitionable

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
        404: {"description": "batch_id is not a batch in this project"},
        409: {"description": "duplicate_scan — this exact file is already a scan (or queued) in this project"},
    },
    summary="Upload scan file (analyst)",
)
async def upload_scan_file(
    file: UploadFile = File(...),
    batch_id: Optional[int] = Form(
        None, description="Upload batch (POST /scans/batches) this file belongs to (v2.335.0).",
    ),
    allow_duplicate: bool = Form(
        False,
        description=(
            "Import even when this exact file is already a scan in the project — "
            "a deliberate re-import, e.g. to re-parse after a parser fix."
        ),
    ),
    skip_informational: Optional[bool] = Form(
        None,
        description=(
            "Nessus only (v2.341.0): drop severity-0 (informational) report items "
            "instead of storing a vulnerability row each; ports are still derived "
            "from them. Omit to use the project's setting (which falls back to the "
            "deployment default)."
        ),
    ),
    stage: bool = Form(
        False,
        description=(
            "v2.352.0: store the file and register the job as 'staged' instead of "
            "queuing it. Review GET /upload/jobs/{id}/detection, then "
            "POST /upload/jobs/{id}/start (optionally with a format override). "
            "A staged job nobody starts expires after 24 hours."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Upload a scan file for background ingestion. Requires analyst role.

    Supported file types: .xml, .json, .csv, .txt, .gnmap, .nessus

    BlueStick never originates network queries: DNS (and all other) data is
    ingested from files the operator produced on their own host. There is no
    server-side DNS enrichment — run your lookups locally and upload the output
    (dnsx JSON, DNS CSV).
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

    from app.db.models_project import resolve_skip_informational

    options = {
        "project_id": project.id,
        # Resolved here, not in the worker: the worker sees a job row, and the
        # project's setting may change between upload and parse — the value
        # the operator saw on the switch when they dropped the file is the
        # one that should apply.
        "skip_informational": resolve_skip_informational(project, skip_informational),
    }

    if batch_id is not None:
        in_project = (
            db.query(ScanBatch.id)
            .filter(ScanBatch.id == batch_id, ScanBatch.project_id == project.id)
            .first()
        )
        if in_project is None:
            raise HTTPException(status_code=404, detail="Scan batch not found in this project")

    try:
        job = await ingestion_service.create_job(
            db=db,
            upload=file,
            submitted_by_id=current_user.id if current_user else None,
            options=options,
            batch_id=batch_id,
            allow_duplicate=allow_duplicate,
            stage=stage,
        )
    except DuplicateUploadError as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
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
        message=(
            "File staged — review its format, then start the import"
            if stage else "File queued for background processing"
        ),
        scan_id=None,
    )

    if not stage:
        ingestion_service.enqueue_job(job.id, db=db)

    return response


# ---------------------------------------------------------------------------
# Staged import (v2.352.0): what is this file, and start it on the operator's
# terms.  See app/services/staged_import_service.py.
# ---------------------------------------------------------------------------

class DetectionCandidate(BaseModel):
    file_type: str
    label: str
    # structure | filename | fallback.  Only ``structure`` is recognition;
    # ``fallback`` is a parser the dispatcher would merely try.
    basis: str
    rank: int


class DetectionPreview(BaseModel):
    raw: str
    sample: List[str] = []


class FormatOption(BaseModel):
    file_type: str
    label: str
    family: str


class DetectionResponse(BaseModel):
    job_id: int
    filename: str
    candidates: List[DetectionCandidate]
    primary: Optional[str] = None
    needs_choice: bool
    reason: Optional[str] = None
    preview: DetectionPreview
    formats: List[FormatOption]


class StartJobRequest(BaseModel):
    format_override: Optional[str] = Field(None, max_length=64)
    source_tool: Optional[str] = Field(None, max_length=64)


def _load_job(db: Session, job_id: int, project: Project, current_user: User) -> IngestionJob:
    job = db.query(IngestionJob).filter(
        IngestionJob.id == job_id, IngestionJob.project_id == project.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    _require_job_access(job, current_user)
    return job


@router.get(
    "/formats",
    response_model=List[FormatOption],
    summary="Every format an operator can choose — independent of any one file's detection",
)
def list_formats(
    _user: User = Depends(get_current_user),
    _project: Project = Depends(get_current_project),
):
    """The chooser's list.  It also rides on every detection response, but a
    failed inspection returns nothing — and that is exactly when the operator
    needs to pick a format by hand."""
    from app.services.format_registry import FORMATS
    return [
        {"file_type": s.file_type, "label": s.label, "family": s.family}
        for s in FORMATS.values()
    ]


@router.get(
    "/jobs/{job_id}/detection",
    response_model=DetectionResponse,
    responses={
        403: {"description": "Not authorized for this job"},
        404: {"description": "Job not found"},
        409: {"description": "The uploaded file is no longer on disk"},
    },
    summary="What is this file? Detected formats with their basis, a preview and a sample",
)
def get_job_detection(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Runs the same detection the worker will run, and says whether each
    candidate came from the file's content or only its name.  Nothing is
    written; the real parsers are never run here."""
    job = _load_job(db, job_id, project, current_user)
    try:
        return detect_for_job(job)
    except FileNotFoundError:
        raise HTTPException(status_code=409, detail="The uploaded file is no longer on disk — re-upload it.")


@router.post(
    "/jobs/{job_id}/start",
    response_model=IngestionJobSchema,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    responses={
        403: {"description": "Not authorized for this job"},
        404: {"description": "Job not found"},
        409: {"description": "Job is not staged or failed, or its file is gone"},
        422: {"description": "Unknown format"},
    },
    summary="Start a staged (or retry a failed) import, optionally as a chosen format",
)
def start_ingestion_job(
    job_id: int,
    body: StartJobRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """With ``format_override`` the worker runs exactly that parser and no
    other; a wrong choice fails visibly.  From ``failed`` this is "review the
    format and retry" on the retained file."""
    job = _load_job(db, job_id, project, current_user)
    if job.status not in ("staged", "failed"):
        raise HTTPException(
            status_code=409,
            detail=f"Only a staged or failed job can be started (current status: {job.status!r})",
        )
    if not job.storage_path or not Path(job.storage_path).exists():
        raise HTTPException(status_code=409, detail="The uploaded file is no longer on disk — re-upload it.")
    # The checks above are the friendly early answer; the service repeats them
    # UNDER the row lock (v2.368.0), which is the one that counts when two
    # starts race.
    from app.services.ingestion_service import DuplicateUploadError
    from app.services.job_transitions import JobNotTransitionable
    try:
        job = start_staged_job(
            db, job, format_override=body.format_override, source_tool=body.source_tool,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except DuplicateUploadError as exc:
        raise HTTPException(status_code=409, detail=exc.detail())
    except JobNotTransitionable as exc:
        if exc.status == "file_missing":
            raise HTTPException(status_code=409, detail="The uploaded file is no longer on disk — re-upload it.")
        raise HTTPException(
            status_code=409,
            detail=f"Only a staged or failed job can be started (current status: {exc.status!r})",
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Job not found")
    # On the request's own session: a second pooled connection per request
    # deadlocked the pool when the review dialog started every file at once.
    ingestion_service.enqueue_job(job.id, db=db)
    return job


class DiscardStagedRequest(BaseModel):
    # The jobs the operator was SHOWN and confirmed.  Required: the page lists
    # only its most recent jobs, so "every staged job" deleted more files than
    # the count in the confirmation — for an admin, other people's too.
    job_ids: List[int] = Field(..., min_length=1, max_length=500)


class DiscardStagedResponse(BaseModel):
    discarded: int
    job_ids: List[int] = []


@router.post(
    "/jobs/discard-staged",
    response_model=DiscardStagedResponse,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Discard the named staged jobs (files removed, rows kept as dismissed)",
)
def discard_all_staged_jobs(
    body: DiscardStagedRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Clears staged uploads nobody will start — exactly the ids given, and of
    those only the ones still staged and visible to the caller (admins the
    project's; everyone else their own).  An id that is no longer staged, or
    not the caller's, is skipped, and the response says what was discarded.
    Declared before ``/jobs/{job_id}`` routes so "discard-staged" is never
    parsed as an id."""
    query = db.query(IngestionJob).filter(
        IngestionJob.project_id == project.id, IngestionJob.status == "staged",
        IngestionJob.id.in_(body.job_ids),
    )
    if current_user.role != UserRole.ADMIN:
        query = query.filter(IngestionJob.submitted_by_id == current_user.id)
    ids = []
    for job in query.all():
        discard_staged_job(db, job)
        ids.append(job.id)
    return DiscardStagedResponse(discarded=len(ids), job_ids=ids)


class DismissSupersededRequest(BaseModel):
    # The superseded jobs the operator was SHOWN and confirmed.
    job_ids: List[int] = Field(..., min_length=1, max_length=500)


class DismissSupersededResponse(BaseModel):
    dismissed: int
    job_ids: List[int] = []


@router.post(
    "/jobs/dismiss-superseded",
    response_model=DismissSupersededResponse,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    summary="Dismiss the named superseded failures (a later job imported the same file)",
)
def dismiss_superseded_jobs(
    body: DismissSupersededRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """v2.403.0 — a failed or partial import whose file a later job of the
    project imported cleanly is SUPERSEDED: it no longer needs attention, but
    it still sat undismissed.  Dismisses exactly the ids given, and of those
    only the ones still superseded, undismissed and visible to the caller
    (admins the project's; everyone else their own) — each checked under its
    row lock through ``job_transitions``.  The response says which."""
    query = db.query(IngestionJob.id).filter(
        IngestionJob.project_id == project.id,
        IngestionJob.id.in_(body.job_ids),
        IngestionJob.dismissed_at.is_(None),
    )
    if current_user.role != UserRole.ADMIN:
        query = query.filter(IngestionJob.submitted_by_id == current_user.id)
    candidate_ids = sorted(jid for (jid,) in query.all())

    def _still_superseded(job: IngestionJob) -> Optional[str]:
        return None if superseding_job_ids(db, [job]) else "not_superseded"

    done: List[int] = []
    for jid in candidate_ids:
        try:
            job = _transitions.acknowledge(db, jid, precondition=_still_superseded)
        except JobNotTransitionable:
            db.rollback()
            continue
        if job is None:
            db.rollback()
            continue
        db.commit()
        done.append(jid)
    return DismissSupersededResponse(dismissed=len(done), job_ids=done)


@router.post(
    "/jobs/{job_id}/discard",
    response_model=IngestionJobSchema,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    responses={
        403: {"description": "Not authorized for this job"},
        404: {"description": "Job not found"},
        409: {"description": "Only a staged job can be discarded"},
    },
    summary="Discard a staged job: remove its file, keep the row as a dismissed failure",
)
def discard_ingestion_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    job = _load_job(db, job_id, project, current_user)
    try:
        return discard_staged_job(db, job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post(
    "/jobs/{job_id}/reprocess",
    response_model=IngestionJobSchema,
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
    responses={
        403: {"description": "Not authorized for this job"},
        404: {"description": "Job not found"},
        409: {"description": "Job is not finished, or its retained file is gone"},
        422: {"description": "Unknown format"},
    },
    summary="Re-process a finished job's retained file as a new import (explicit)",
)
def reprocess_ingestion_job(
    job_id: int,
    body: StartJobRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """v2.354.0 — a new job over the same bytes.  A new scan record is
    created when it parses; the prior scan and its contributions stay until
    that scan is deleted; the duplicate guard is bypassed on purpose."""
    job = _load_job(db, job_id, project, current_user)
    if job.status not in ("completed", "failed"):
        raise HTTPException(
            status_code=409,
            detail=f"Only a finished job can be re-processed (current status: {job.status!r})",
        )
    try:
        new = reprocess_job(
            db, job, submitted_by_id=current_user.id,
            format_override=body.format_override, source_tool=body.source_tool,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=409, detail="The uploaded file is no longer retained — re-upload it.")
    ingestion_service.enqueue_job(new.id, db=db)
    return new


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
    ids: str | None = Query(
        None,
        description=(
            "Comma-separated job ids (v2.370.0, at most 200). Returns exactly "
            "those jobs the caller may see — dismissed ones included, status "
            "and pagination ignored. The Scans page follows the files it "
            "started with ONE request per tick instead of one per file."
        ),
    ),
    batch_id: int | None = Query(
        None,
        description=(
            "v2.403.0 — the jobs of this upload batch that did NOT import "
            "(any status but completed; dismissed ones included; at most 500), "
            "so an expanded batch on /scans can list which files failed and why. "
            "Status and pagination are ignored."
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
    if ids is not None:
        try:
            wanted = sorted({int(part) for part in ids.split(",") if part.strip()})
        except ValueError:
            raise HTTPException(status_code=422, detail="ids must be comma-separated integers")
        if len(wanted) > 200:
            raise HTTPException(status_code=422, detail="At most 200 ids per request")
        if not wanted:
            return []
        # Same project + visibility rule as above; an id the caller may not
        # see, or that no longer exists, is simply absent from the answer.
        return annotate_jobs(db, (
            query.filter(IngestionJob.id.in_(wanted))
            .order_by(desc(IngestionJob.created_at))
            .all()
        ))
    if batch_id is not None:
        return annotate_jobs(db, (
            query.filter(IngestionJob.batch_id == batch_id, IngestionJob.status != "completed")
            .order_by(IngestionJob.original_filename, IngestionJob.id)
            .limit(500)
            .all()
        ))
    if status:
        query = query.filter(IngestionJob.status == status)
    if not include_dismissed:
        query = query.filter(IngestionJob.dismissed_at.is_(None))
    jobs = query.order_by(desc(IngestionJob.created_at)).offset(skip).limit(limit).all()
    # v2.403.0 — each job says whether a later job imported its file
    # (superseded) and the specific reason it failed.
    return annotate_jobs(db, jobs)


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
    for the audit trail / debugging.  ``failed`` jobs are dismissable, and
    since v2.363.0 so is a job that finished ``partial``: Operations lists
    both as blocked until someone deals with them, and a blocker nobody can
    clear is a permanent banner.  The row stays visibly partial everywhere —
    dismissal acknowledges it, it does not make it clean.  Queued/processing
    rows would be hiding live state, and a clean completed row has nothing
    to dismiss.  Non-admins can only dismiss jobs they submitted, mirroring
    the list endpoint's visibility rule above.
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
    # v2.403.0 — through job_transitions: the status is checked under the
    # row lock (a retry could be re-queuing it right now).
    try:
        dismissed = _transitions.acknowledge(db, job.id, precondition=_not_failed_or_partial)
    except JobNotTransitionable as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"Only failed or partial jobs can be dismissed (current status: {exc.status!r})",
        )
    if dismissed is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    db.commit()
    db.refresh(dismissed)
    return annotate_jobs(db, [dismissed])[0]


def _not_failed_or_partial(job: IngestionJob) -> Optional[str]:
    if job.status == "failed" or (job.status == "completed" and job.partial):
        return None
    return job.status
