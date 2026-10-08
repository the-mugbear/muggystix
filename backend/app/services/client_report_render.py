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

Evidence images: a draft reads the live note attachments at render time; an
issued report reads ITS OWN copies, made when it was issued
(``client_report_service.freeze_report_images`` → ``report_images``), so an
attachment deleted afterwards cannot fail its render.  The copies are of the
images the report PRINTS: one the dataset marks ``"printed": false`` (its
template shows it nowhere) has no copy and the renderer never asks for it.

The template: an issued report renders from ITS OWN copy of it, made when it
was issued (``client_report_service.freeze_report_template`` — the template's
files, its base's, and every uploaded file in place), so a logo uploaded or a
template folder changed between the issue and its render can neither change
the files nor block them; ``template_fingerprint`` is then only the label of
what was issued.  A report issued before the copy existed renders from the
live template, and only while its fingerprint is still the recorded one.

An issued render refuses what would make its files differ from what was
signed off (review 2026-09-23 C4): a template copy or an image copy that is
gone or no longer matches its recorded hash (for a report without a template
copy: a template whose fingerprint changed since the issue).  That is a
restore from backup or a revision, not a re-render.
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

from app import worker_loop
from app.core.config import settings
from app.db.models import NoteAttachment, ReportJob
from app.db.models_reports import RenderStatus, Report, ReportFile, ReportImage, ReportStatus
from app.services import quarto_render
from app.services import report_template_service as templates
from app.services.client_report_service import ClientReportService, frozen_template_dir

logger = logging.getLogger(__name__)

# No "report-pdf" since v2.407.0 (PDF comes from the Word report).  An issued
# report's stored PDF, from before, still downloads: files are served by row.
PREVIEW_FORMATS = {"report-html": "html", "report-docx": "docx", "report-qmd": "qmd"}
ISSUE_FORMAT = "report-issue"
# One contact's remediation list (``remediation_report``; kept literal so
# neither module imports the other at import time).
CONTACT_JOB_FORMATS = ("contact-html", "contact-docx")
CLIENT_JOB_FORMATS = tuple(PREVIEW_FORMATS) + (ISSUE_FORMAT,) + CONTACT_JOB_FORMATS


def file_slug(text: Optional[str], fallback: str = "report") -> str:
    """``text`` as part of an output file name: ASCII letters and digits,
    everything else a dash (the renderer accepts no other file name)."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return (slug or fallback)[:60]


def live_evidence_resolver(db: Session, project_id: int):
    """The live attachments: a DRAFT's images, and those of a document that
    is never frozen (a contact's remediation list)."""
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


def _issued_evidence_resolver(db: Session, report: Report):
    """An ISSUED report's images: its own copies, made when it was issued
    (``freeze_report_images``) and checked against the hash recorded then —
    never the live attachment, which may have been deleted, un-ticked or
    replaced since.  A report issued before the copies existed has none and
    reads the live attachments, as it always did."""
    copies = {
        row.attachment_id: row
        for row in db.query(ReportImage).filter(ReportImage.report_id == report.id)
    }
    if not copies:
        return live_evidence_resolver(db, report.project_id)
    root = Path(settings.REPORT_FILES_DIR).resolve()

    def resolve(item: dict) -> Optional[Path]:
        row = copies.get(item.get("attachment_id"))
        if row is None:
            return None
        try:
            target = (root / row.storage_path).resolve()
            target.relative_to(root)
            if not target.is_file():
                return None
            if hashlib.sha256(target.read_bytes()).hexdigest() != row.sha256:
                logger.error("Client report %s: its copy of image %s does not match the hash recorded at issue",
                             report.id, row.attachment_id)
                return None
        except (ValueError, OSError):
            return None
        return target

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
    # An issued report renders from ITS OWN copy of the template, made when
    # it was issued: whatever happened to the template folder or its uploaded
    # files since can neither change the files nor block them.
    frozen = frozen_template_dir(report) if issued else None
    if frozen is not None:
        template = templates.load_folder(frozen)
        asset_files = None      # already in place in the copy
    else:
        template = templates.get_template(report.template)
        # A report issued before it kept a copy: the live template, and only
        # while it is still the one that was signed off.
        if issued and report.template_fingerprint and templates.fingerprint(template) != report.template_fingerprint:
            raise ValueError(
                f"The '{report.template}' template has changed since this report was issued, so its "
                "files would no longer be the document that was signed off. Revise the report to "
                "issue it with the current template."
            )
        asset_files = templates.asset_files(template)
    wanted = [f for f in formats if f in template.formats]
    return quarto_render.render(
        template.path, template.entry, dataset, wanted, out_dir,
        basename=basename,
        resolve_evidence=(
            _issued_evidence_resolver(db, report) if issued else live_evidence_resolver(db, report.project_id)
        ),
        postprocess=template.postprocess,
        timeout=settings.REPORT_RENDER_TIMEOUT_SECONDS,
        strict_evidence=issued,
        asset_files=asset_files,
        on_progress=worker_loop.touch_heartbeat,
    )


def run_client_job(db: Session, job: ReportJob) -> Optional[Tuple[bytes, str, str]]:
    """Render one client-report job.  Returns ``(bytes, media_type, filename)``
    for a preview (the worker stores it as the job's artifact), ``None`` for
    an issue render (its files are stored with the report)."""
    if job.format in CONTACT_JOB_FORMATS:
        # v2.463.0 — one contact's remediation list: a template of another
        # kind on the same renderer, with no report row behind it.
        from app.services import remediation_report
        return remediation_report.run_job(db, job)
    report_id = (job.filters or {}).get("report_id")
    report = db.get(Report, report_id) if report_id else None
    if report is None or report.project_id != job.project_id:
        raise ValueError("The report for this job no longer exists.")

    if job.format in PREVIEW_FORMATS:
        if report.status != ReportStatus.DRAFT:
            raise ValueError("This report has been issued; download its files instead of a preview.")
        fmt = PREVIEW_FORMATS[job.format]
        dataset, _, _ = ClientReportService(db).build(report)
        basename = f"{file_slug(dataset['project']['name'])}-draft-{report.id}"
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
    basename = f"{file_slug(dataset['project']['name'])}-report-{number:02d}"
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
