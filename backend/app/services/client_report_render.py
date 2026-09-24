"""Client-report jobs on the report worker (v2.381.0).

Two job formats, both ``report_type='client'`` with ``filters={"report_id"}``:

* ``report-html`` / ``report-docx`` / ``report-qmd`` — a DRAFT preview: the
  dataset is built from the live findings and rendered as one format; the
  file is an ordinary report-job artifact (expires with the job).
* ``report-issue`` — an ISSUED report's files: every format the template
  produces, rendered from the FROZEN snapshot and kept under
  ``REPORT_FILES_DIR`` with a ``report_files`` row each (sha256 recorded).
  Re-running it (``POST /client-reports/{id}/render``, only after a failed
  render) builds the files from the same snapshot.

Evidence images are read from the note-attachment store at render time.  An
issued render refuses what would make its files differ from what was signed
off (review 2026-09-23 C4): a template whose fingerprint changed since the
issue, or an evidence image that has gone.  Either is a revision, not a
re-render.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import NoteAttachment, ReportJob
from app.db.models_reports import RenderStatus, Report, ReportFile, ReportStatus
from app.services import quarto_render
from app.services import report_template_service as templates
from app.services.client_report_service import ClientReportService

logger = logging.getLogger(__name__)

# No "report-pdf" since v2.407.0 (PDF comes from the Word report).  An issued
# report's stored PDF, from before, still downloads: files are served by row.
PREVIEW_FORMATS = {"report-html": "html", "report-docx": "docx", "report-qmd": "qmd"}
ISSUE_FORMAT = "report-issue"
CLIENT_JOB_FORMATS = tuple(PREVIEW_FORMATS) + (ISSUE_FORMAT,)


def _slug(text: Optional[str]) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return (slug or "report")[:60]


def _evidence_resolver(db: Session, project_id: int):
    base = (Path(settings.UPLOAD_DIR) / "note_attachments").resolve()

    def resolve(item: dict) -> Optional[Path]:
        att = db.get(NoteAttachment, item.get("attachment_id"))
        if att is None or att.project_id != project_id:
            return None
        try:
            target = (base / att.storage_path).resolve()
            target.relative_to(base)
        except (ValueError, OSError):
            return None
        return target if target.is_file() else None

    return resolve


def _lock_report(db: Session, report_id: int) -> Optional[Report]:
    """The report row under FOR UPDATE, re-read — a row already in the
    session would otherwise be checked at its pre-lock status."""
    report = (
        db.query(Report).filter(Report.id == report_id)
        .with_for_update().populate_existing().one_or_none()
    )
    if report is not None:
        db.expire(report, ["files"])
    return report


def _render(db: Session, report: Report, dataset: dict, formats, out_dir: Path, basename: str,
            *, issued: bool = False):
    template = templates.get_template(report.template)
    if issued and report.template_fingerprint and templates.fingerprint(template) != report.template_fingerprint:
        raise ValueError(
            f"The '{report.template}' template has changed since this report was issued, so its "
            "files would no longer be the document that was signed off. Revise the report to "
            "issue it with the current template."
        )
    wanted = [f for f in formats if f in template.formats]
    return quarto_render.render(
        template.path, template.entry, dataset, wanted, out_dir,
        basename=basename,
        resolve_evidence=_evidence_resolver(db, report.project_id),
        postprocess=template.postprocess,
        timeout=settings.REPORT_RENDER_TIMEOUT_SECONDS,
        strict_evidence=issued,
    )


def run_client_job(db: Session, job: ReportJob) -> Optional[Tuple[bytes, str, str]]:
    """Render one client-report job.  Returns ``(bytes, media_type, filename)``
    for a preview (the worker stores it as the job's artifact), ``None`` for
    an issue render (its files are stored with the report)."""
    report_id = (job.filters or {}).get("report_id")
    report = db.get(Report, report_id) if report_id else None
    if report is None or report.project_id != job.project_id:
        raise ValueError("The report for this job no longer exists.")

    if job.format in PREVIEW_FORMATS:
        if report.status != ReportStatus.DRAFT:
            raise ValueError("This report has been issued; download its files instead of a preview.")
        fmt = PREVIEW_FORMATS[job.format]
        dataset, _, _ = ClientReportService(db).build(report)
        basename = f"{_slug(dataset['project']['name'])}-draft-{report.id}"
        with tempfile.TemporaryDirectory(prefix="bs-preview-") as tmp:
            files = _render(db, report, dataset, [fmt], Path(tmp), basename)
            if fmt not in files:
                raise ValueError(f"The '{report.template}' template does not produce {fmt}.")
            path = files[fmt]
            return path.read_bytes(), quarto_render.FORMATS[fmt][2], path.name

    if job.format == ISSUE_FORMAT:
        # A replay (reaped + requeued after the files were published) or a
        # stale concurrent attempt must never touch published files: they are
        # the issued report (remediation review 2026-09-23 finding 1).
        if report.render_status == RenderStatus.DONE:
            return None
        try:
            _render_issued(db, report)
        except Exception as exc:
            db.rollback()
            report = _lock_report(db, report_id)
            if report is not None and report.render_status != RenderStatus.DONE:
                report.render_status = RenderStatus.FAILED
                report.render_error = str(exc)[:4000]
            db.commit()
            raise
        return None

    raise ValueError(f"Unsupported client report job: {job.format!r}")


def _render_issued(db: Session, report: Report) -> None:
    if report.status == ReportStatus.DRAFT or not report.snapshot:
        raise ValueError("Only an issued report has files to render.")
    dataset = report.snapshot["dataset"]
    number = report.number or 0
    basename = f"{_slug(dataset['project']['name'])}-report-{number:02d}"
    root = Path(settings.REPORT_FILES_DIR)
    final_dir = root / str(report.project_id) / str(report.id)
    with tempfile.TemporaryDirectory(prefix="bs-issue-") as tmp:
        files = _render(db, report, dataset, quarto_render.FORMATS, Path(tmp), basename, issued=True)
        # Render outside the lock (Quarto takes a while), publish under it:
        # whichever attempt gets here first publishes, every later one
        # discards its render and leaves the files alone.
        report = _lock_report(db, report.id)
        if report is None or report.render_status == RenderStatus.DONE:
            db.commit()
            logger.info("Client report render: already published, discarding this attempt's files")
            return
        final_dir.mkdir(parents=True, exist_ok=True)
        existing = {f.format: f for f in report.files}
        for fmt, path in files.items():
            data = path.read_bytes()
            target = final_dir / path.name
            shutil.copyfile(path, target)
            record = existing.get(fmt) or ReportFile(report_id=report.id, format=fmt)
            record.filename = path.name
            record.media_type = quarto_render.FORMATS[fmt][2]
            record.size_bytes = len(data)
            record.sha256 = hashlib.sha256(data).hexdigest()
            record.storage_path = str(target.relative_to(root))
            if fmt not in existing:
                db.add(record)
    report.quarto_version = quarto_render.quarto_version()
    report.render_status = RenderStatus.DONE
    report.render_error = None
    db.commit()
    logger.info("Client report %s: rendered %s", report.id, ", ".join(sorted(files)))
