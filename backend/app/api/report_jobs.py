"""Finding a report job for a route, and serving its file — one implementation
for every door (review 2026-10-23, H-RPT1).

A report job is read through exactly one door, decided by its
``report_type``:

- the generic ``/reports/jobs`` routes serve the Hosts page's inventory
  downloads only.  A PAGE-OWNED job (``PAGE_OWNED_REPORT_TYPES``: a client
  report's render, a contact's remediation list) is a 404 there, as the list
  already left it out (owner, 2026-10-23) — through them a contact's
  document stayed readable, ``contact_email`` included, with remediation
  tracking switched off;
- its own page's routes (``owner=`` the type) serve it: a draft's preview
  under ``/client-reports/{id}/previews/{job}``, a contact's document under
  ``/remediation/contact-report/{job}``, which keeps that router's
  ``enabled`` gate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.models import ReportJob
from app.services.report_job_service import PAGE_OWNED_REPORT_TYPES


def job_or_404(db: Session, project_id: int, job_id: int, *, owner: Optional[str] = None,
               not_found: str = "Report job not found") -> ReportJob:
    """The project's job ``job_id`` as the door asking may see it.

    ``owner=None`` is the generic door: any job but a page-owned one.
    ``owner="<report_type>"`` is that page's door: only jobs of that type."""
    query = db.query(ReportJob).filter(ReportJob.id == job_id, ReportJob.project_id == project_id)
    if owner is None:
        query = query.filter(ReportJob.report_type.notin_(PAGE_OWNED_REPORT_TYPES))
    else:
        query = query.filter(ReportJob.report_type == owner)
    job = query.first()
    if job is None:
        raise HTTPException(status_code=404, detail=not_found)
    return job


def job_file_response(job: ReportJob, *, not_ready: str = "Report is not ready",
                      expired: str = "Report artifact has expired or been removed.",
                      fallback_name: str = "report") -> FileResponse:
    """A finished job's file: 409 until it is ``completed``, 410 once the file
    has expired (``REPORT_ARTIFACT_TTL_HOURS``)."""
    if job.status != "completed" or not job.result_path:
        raise HTTPException(status_code=409, detail=f"{not_ready} (status: {job.status}).")
    if not Path(job.result_path).is_file():
        raise HTTPException(status_code=410, detail=expired)
    return FileResponse(
        path=job.result_path,
        media_type=job.media_type or "application/octet-stream",
        filename=job.result_filename or f"{fallback_name}_{job.id}",
    )
