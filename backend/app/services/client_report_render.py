"""Client-report jobs on the report worker (v2.381.0).

Two job formats, both ``report_type='client'`` with ``filters={"report_id"}``:

* ``report-html`` / ``report-docx`` / ``report-pdf`` — a DRAFT preview: the
  dataset is built from the live findings and rendered as one format; the
  file is an ordinary report-job artifact (expires with the job).
* ``report-issue`` — an ISSUED report's files: every format the template
  produces, rendered from the FROZEN snapshot and kept under
  ``REPORT_FILES_DIR`` with a ``report_files`` row each (sha256 recorded).
  Re-running it (``POST /client-reports/{id}/render``) replaces the files from
  the same snapshot.

Evidence images are read from the note-attachment store at render time.
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

PREVIEW_FORMATS = {"report-html": "html", "report-docx": "docx", "report-pdf": "pdf"}
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


def _render(db: Session, report: Report, dataset: dict, formats, out_dir: Path, basename: str):
    template = templates.get_template(report.template)
    wanted = [f for f in formats if f in template.formats]
    return quarto_render.render(
        template.path, template.entry, dataset, wanted, out_dir,
        basename=basename,
        resolve_evidence=_evidence_resolver(db, report.project_id),
        postprocess=template.postprocess,
        timeout=settings.REPORT_RENDER_TIMEOUT_SECONDS,
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
        try:
            _render_issued(db, report)
        except Exception as exc:
            db.rollback()
            report = db.get(Report, report_id)
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
        files = _render(db, report, dataset, quarto_render.FORMATS, Path(tmp), basename)
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
