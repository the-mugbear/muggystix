"""Ingestion pipeline for handling large scan uploads.

The ingestion service streams uploads to disk and registers job metadata
in PostgreSQL.  A separate worker process (``python -m app.worker``) polls
the ``ingestion_jobs`` table using ``SELECT … FOR UPDATE SKIP LOCKED`` and
processes one job at a time, keeping parsing fully isolated from the API.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Type
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.parsers import content_detection as _cd  # v2.27.0 — content sniffers extracted
from app.db.models import Host, HostScanHistory, IngestionJob
from app.db.session import SessionLocal
from app.services.dns_service import DNSService
from app.services.parse_error_service import log_parse_error

logger = logging.getLogger(__name__)

# Thread-local storage for the active ingestion job, enabling parsers to
# report heartbeat/progress without needing a direct reference to the service.
_active_job = threading.local()


def report_progress(progress: str) -> None:
    """Called by parsers to update heartbeat and progress on the active job.

    Safe to call even when no ingestion job is active (e.g. during tests)
    — it simply does nothing.  Raises ``ParseFailure`` if the job has been
    cancelled or timed out, giving the parser a chance to stop early.
    """
    svc = getattr(_active_job, "service", None)
    db = getattr(_active_job, "db", None)
    job_id = getattr(_active_job, "job_id", None)
    if svc is None or db is None or job_id is None:
        return
    svc.update_heartbeat(db, job_id, progress)


ParserDescriptor = Tuple[str, Type, str]


class ParseFailure(RuntimeError):
    """Exception raised when an ingestion job fails due to parsing issues."""

    def __init__(
        self,
        message: str,
        *,
        user_message: Optional[str] = None,
        error_id: Optional[int] = None,
        underlying_error: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.user_message = user_message
        self.error_id = error_id
        self.underlying_error = underlying_error


class IngestionService:
    """Coordinate file storage, job tracking, and background parsing.

    In the API process this class is used only for ``create_job`` (write file
    to disk + insert a ``queued`` row) and ``cancel_job``.  The actual
    parsing is driven by the standalone worker process which calls
    ``poll_and_run_one`` in a loop.
    """

    def __init__(self) -> None:
        self._storage_root = Path(settings.INGESTION_STORAGE_DIR)
        try:
            self._storage_root.mkdir(parents=True, exist_ok=True)
            # Verify we can actually write (directory may exist but be unwritable
            # due to host volume mount ownership).  Use a PID-specific file to
            # avoid races when multiple uvicorn workers start concurrently.
            test_file = self._storage_root / f".write_test_{os.getpid()}"
            test_file.touch()
            test_file.unlink()
        except (PermissionError, OSError) as exc:
            # Refuse to start.  The previous behavior fell back to
            # /tmp/networkmapper_ingestion, but in a container split
            # deployment (API and worker in separate containers) those
            # paths point at *different* tmpfs mounts, so files written
            # by the API would be invisible to the worker.  That turned
            # a fixable misconfiguration into silently broken queues.
            #
            # Hard-fail at startup so the operator sees the problem
            # immediately.  The error message includes the exact fix.
            # CR4-5c — recommend least-privilege ownership, NOT chmod 777.
            # This directory holds uploaded scan data (often sensitive
            # target/host detail); a world-writable mount lets any local
            # account tamper with the ingestion queue.  Fix is to give the
            # container's app UID (999, appuser) ownership at 0750.
            msg = (
                f"Ingestion storage {self._storage_root} is not writable: {exc}. "
                f"This usually means the host volume mount has wrong ownership. "
                f"Fix on the host by giving the container's app user ownership "
                f"(do NOT chmod 777 — this directory holds sensitive scan data):  "
                f"sudo chown -R 999:999 uploads/ingestion_queue && "
                f"sudo chmod 750 uploads/ingestion_queue  "
                f"(999 is the default appuser UID/GID; adjust if you run the "
                f"containers as a different user).  Refusing to start — fix the "
                f"volume and restart the container."
            )
            logger.critical(msg)
            raise RuntimeError(msg) from exc
        # Job IDs that have been requested to cancel.  Checked by
        # update_heartbeat so long-running parsers can bail out early.
        self._cancelled: set[int] = set()
        # v2.22.0: the old IngestionService used to run ad-hoc
        # ALTER TABLE statements on construction to lazily add
        # parse_error_id / last_heartbeat / progress columns and to
        # wire up the parse_errors FK.  All of that now lives in the
        # Alembic baseline (b46cd59c17f5) — startup is intentionally
        # side-effect free, no DDL on import.

    async def create_job(
        self,
        db: Session,
        upload: UploadFile,
        submitted_by_id: Optional[int],
        options: Optional[Dict[str, object]] = None,
    ) -> IngestionJob:
        """Persist an upload to disk and register an ingestion job."""
        job_token = uuid4().hex
        job_dir = self._storage_root / job_token
        job_dir.mkdir(parents=True, exist_ok=True)
        # CR5-C2 — uploaded scans contain sensitive target/host detail.  The
        # storage root may be a shared/world-traversable mount (unprivileged
        # deploys can't chown it), so confidentiality must come from the files
        # themselves: lock the per-job dir to the app user only (0700) — the
        # API writer and the worker reader run as the same UID, so this keeps
        # other local accounts out without breaking ingestion.  mkdir's mode is
        # masked by umask, hence the explicit chmod.
        os.chmod(job_dir, 0o700)

        # Audit finding H5: the previous implementation used
        # ``upload.filename`` verbatim in the filesystem path.  The
        # per-job UUID dir bounds traversal, but a filename containing
        # newlines, null bytes, or ANSI escapes could corrupt log
        # output and audit trails.  We strip the path component, then
        # slugify to a safe character set and cap the length so the
        # filesystem path stays well below any FS max.  The original
        # filename is still kept on ``original_filename`` for the UI.
        raw_name = Path(upload.filename or "upload").name or "upload"
        safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', raw_name)[:120] or "upload"
        destination = job_dir / safe_name
        file_size = await self._write_upload(upload, destination)
        # CR5-C2 — owner-only on the scan file too (umask leaves it 0644 by
        # default); belt-and-suspenders with the 0700 job dir above.
        os.chmod(destination, 0o600)

        # Magic-byte sanity check.  The extension tells us which parser
        # will run; the content should actually look like that format.
        # This is a cheap first line of defense against uploaded binaries
        # disguised as .xml/.json/.nessus that could crash the parser or
        # get mis-routed.  We only peek at the first 1 KB.
        try:
            self._validate_content_matches_extension(destination, raw_name)
        except ValueError as exc:
            destination.unlink(missing_ok=True)
            raise ValueError(str(exc))

        opts = options or {}
        job = IngestionJob(
            filename=destination.name,
            original_filename=upload.filename,
            storage_path=str(destination),
            status="queued",
            file_size=file_size,
            options=opts,
            submitted_by_id=submitted_by_id,
            project_id=opts.get("project_id"),
            # Stamp recon_session_id in the SAME transaction that makes the
            # row visible as ``queued``.  The worker polls for queued rows
            # independently of the pg_notify hint, so any later "set the FK,
            # commit again" step leaves a window where the worker can claim
            # and process the job before it's attributed to its recon
            # session.  Setting it here closes that window.
            recon_session_id=opts.get("recon_session_id"),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        # Log the sanitized filename, not the raw one — prevents log
        # injection via filenames with embedded CR/LF.
        logger.info(
            "Queued ingestion job %s for %s (%d bytes)", job.id, safe_name, file_size
        )
        return job

    def enqueue_job(self, job_id: int) -> None:
        """Mark a job as ready for processing.

        The job is already in ``queued`` status from ``create_job``.  The
        separate worker process polls for queued rows and picks them up
        via ``SELECT … FOR UPDATE SKIP LOCKED``.  This method exists to
        keep the upload endpoint interface unchanged and to send a
        ``pg_notify`` hint so the worker wakes up immediately instead of
        waiting for its next poll cycle.
        """
        self._cancelled.discard(job_id)
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT pg_notify('ingestion_jobs', :jid)"), {"jid": str(job_id)})
                db.commit()
        except Exception:
            # Notification is a performance hint, not required for correctness.
            pass

    def cancel_job(self, job_id: int) -> bool:
        """Request cancellation of a running job.

        Returns True if the job was in a cancellable state.
        """
        self._cancelled.add(job_id)
        with SessionLocal() as db:
            job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
            if not job:
                return False
            if job.status not in ("queued", "processing"):
                return False
            job.status = "failed"
            job.error_message = "Cancelled by user"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return True

    def update_heartbeat(self, db: Session, job_id: int, progress: Optional[str] = None) -> None:
        """Update heartbeat timestamp and optional progress text.

        Raises ``ParseFailure`` if the job has been cancelled or has exceeded
        the configured timeout, giving the active parser a chance to bail out.
        """
        # In-memory check (same process only — e.g. tests or single-process mode)
        if job_id in self._cancelled:
            raise ParseFailure(
                "Job cancelled",
                user_message="Cancelled by user",
            )

        now = datetime.now(timezone.utc)

        db.execute(
            text(
                "UPDATE ingestion_jobs SET last_heartbeat = :hb"
                + (", progress = :progress" if progress is not None else "")
                + " WHERE id = :jid"
            ),
            {"hb": now, "jid": job_id, **({"progress": progress} if progress is not None else {})},
        )
        db.commit()

        # Single DB read for both cancellation (cross-process) and timeout checks
        job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
        if job and job.status == "failed":
            raise ParseFailure(
                "Job cancelled",
                user_message="Cancelled by user",
            )
        if job and job.started_at:
            elapsed = (now.replace(tzinfo=None) - job.started_at.replace(tzinfo=None)).total_seconds()
            if elapsed > settings.INGESTION_JOB_TIMEOUT:
                raise ParseFailure(
                    f"Job timed out after {int(elapsed)}s (limit {settings.INGESTION_JOB_TIMEOUT}s)",
                    user_message=f"Parse timed out after {int(elapsed // 60)} minutes",
                )

    def _validate_content_matches_extension(self, destination: Path, raw_name: str) -> None:
        """Peek at the first 1 KB of the uploaded file and verify it
        looks like its claimed extension.

        This does not validate full parser-level syntax — that happens
        in the worker.  It's a cheap pre-filter that blocks the obvious
        nonsense: a .xml that starts with PK\\x03\\x04 (zip), an .nessus
        that's all binary, a .json that starts with a null byte.
        """
        ext = Path(raw_name.lower()).suffix
        try:
            with destination.open("rb") as f:
                head = f.read(1024)
        except OSError as exc:
            raise ValueError(f"Could not read uploaded file for validation: {exc}")
        if not head:
            raise ValueError("Uploaded file is empty")

        # Strip UTF-8 / UTF-16 BOMs before checking the first real char
        is_utf16 = False
        for bom in (b"\xef\xbb\xbf", b"\xfe\xff", b"\xff\xfe"):
            if head.startswith(bom):
                is_utf16 = bom in (b"\xfe\xff", b"\xff\xfe")
                head = head[len(bom):]
                break

        # Skip leading whitespace — XML and JSON commonly start with it
        stripped = head.lstrip(b" \t\r\n")
        if not stripped:
            raise ValueError("Uploaded file contains only whitespace")

        first = stripped[:1]

        if ext in (".xml", ".nessus"):
            if first != b"<":
                raise ValueError(
                    f"File extension {ext} expects XML but content does not start with '<'. "
                    "Make sure you're uploading the correct file."
                )
        elif ext == ".json":
            if first not in (b"{", b"["):
                raise ValueError(
                    "File extension .json expects an object or array at the root."
                )
        elif ext == ".jsonl":
            # httpx (and similar line-delimited formats) — each line is
            # its own JSON object.  First non-blank line must start
            # with ``{``.
            if first != b"{":
                raise ValueError(
                    ".jsonl expects one JSON object per line, starting with '{'."
                )
        elif ext == ".zip":
            # PK\x03\x04 zip header.  The EyeWitness bundle upload
            # route relies on this — anything else that looks like
            # zip-encoded content is rejected here.
            if not head.startswith(b"PK\x03\x04"):
                raise ValueError(
                    ".zip uploads must carry the PK\\x03\\x04 header (standard zip format)."
                )
        elif ext == ".gnmap":
            # gnmap files start with "# Nmap" or "Host: "
            if not (stripped.startswith(b"#") or stripped.startswith(b"Host:")):
                raise ValueError(
                    ".gnmap files must start with a '# Nmap' header or 'Host:' line."
                )
        elif ext in (".csv", ".txt"):
            # No reliable magic for plain text.  Reject if the first 1 KB
            # contains NUL bytes (strong binary signal) — UNLESS the file
            # is UTF-16 (Windows tools export UTF-16-LE CSVs), where
            # interleaved NULs are expected, not a binary signal.
            if not is_utf16 and b"\x00" in head:
                raise ValueError(
                    f"File extension {ext} expects text content but file contains NUL bytes."
                )
        # Unknown extensions fall through — upload_scan_file already
        # enforces ALLOWED_EXTENSIONS, so anything reaching here is one
        # of the known-good types.

    async def _write_upload(self, upload: UploadFile, destination: Path) -> int:
        """Stream an upload to disk in chunks, returning written size.

        v2.91.4 (third code review #6) — offload each disk write to
        a thread via ``asyncio.to_thread`` so the event loop stays
        responsive during multi-GB uploads on slow / bind-mounted
        storage.  Pre-fix the surrounding ``async def`` plus
        ``await upload.read()`` made the reads non-blocking, but
        ``outfile.write(chunk)`` was a synchronous filesystem
        operation on the event loop — a slow disk during a 2 GB
        upload froze every other request on the same Uvicorn
        worker for the duration.  Reads via ``UploadFile.read`` are
        already off-loop (Starlette uses anyio threads internally),
        so the change is to mirror the same off-loop pattern for
        writes.
        """
        import asyncio

        chunk_size = settings.UPLOAD_CHUNK_SIZE
        total_written = 0

        await upload.seek(0)
        # Open the file in the thread too — `open()` is also a sync
        # filesystem syscall that should not block the loop on slow
        # storage.  Same for `close()` (handled by the `with` exit).
        outfile = await asyncio.to_thread(destination.open, "wb")
        try:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                await asyncio.to_thread(outfile.write, chunk)
                total_written += len(chunk)
                if total_written > settings.MAX_FILE_SIZE:
                    # Close before unlink so the file handle isn't
                    # leaked on the early-return path.
                    await asyncio.to_thread(outfile.close)
                    await asyncio.to_thread(destination.unlink, missing_ok=True)
                    raise ValueError(
                        "File too large. Increase MAX_FILE_SIZE or provide a smaller upload."
                    )
        finally:
            # `outfile.close` is idempotent so the early-return path
            # above is safe to also reach this.
            await asyncio.to_thread(outfile.close)

        await upload.close()
        return total_written

    # ------------------------------------------------------------------
    # Worker-side methods (called from ``python -m app.worker``)

    def reap_orphaned_jobs(self) -> int:
        """Transition stuck 'processing' jobs to 'failed'.

        A job is 'orphaned' if its status is 'processing' but the
        worker that owned it has died without writing a final status.
        We detect this via ``last_heartbeat``: ``update_heartbeat`` is
        called every few seconds by the active parser, so any job
        whose heartbeat is older than 3× the configured timeout is
        almost certainly a dead worker's leftover row.

        Returns the number of jobs reaped — the worker logs this so
        operators can see orphan detection happening.  Called once per
        poll iteration; cheap because the filter uses a partial index
        on ``status = 'processing'`` (see alembic migration).
        """
        cutoff_seconds = settings.INGESTION_JOB_TIMEOUT * 3
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=cutoff_seconds)
        db = SessionLocal()
        try:
            orphans = (
                db.query(IngestionJob)
                .filter(IngestionJob.status == "processing")
                .filter(
                    (IngestionJob.last_heartbeat.is_(None) & (IngestionJob.started_at < cutoff))
                    | (IngestionJob.last_heartbeat < cutoff)
                )
                .all()
            )
            if not orphans:
                return 0
            now = datetime.now(timezone.utc)
            for job in orphans:
                job.status = "failed"
                job.error_message = (
                    f"Orphaned — no heartbeat for >{cutoff_seconds}s; "
                    "worker likely crashed. Re-upload to retry."
                )
                job.message = job.error_message
                job.last_error = job.error_message
                job.retry_count = (job.retry_count or 0) + 1
                job.completed_at = now
                logger.warning(
                    "Reaped orphaned ingestion job %s (started_at=%s, last_heartbeat=%s)",
                    job.id,
                    job.started_at,
                    job.last_heartbeat,
                )
            db.commit()
            return len(orphans)
        except Exception:
            db.rollback()
            logger.exception("Orphan reaper failed")
            return 0
        finally:
            db.close()

    def poll_and_run_one(self) -> bool:
        """Claim the oldest queued job and process it.

        Uses ``SELECT … FOR UPDATE SKIP LOCKED`` so only one worker can
        claim a given row, and other workers (if any) skip it.

        Returns ``True`` if a job was processed (success *or* failure),
        ``False`` if no queued job was available.
        """
        db = SessionLocal()
        try:
            row = db.execute(
                text(
                    "SELECT id FROM ingestion_jobs "
                    "WHERE status = 'queued' "
                    "ORDER BY created_at "
                    "LIMIT 1 "
                    "FOR UPDATE SKIP LOCKED"
                )
            ).fetchone()
            if row is None:
                return False

            job_id = row[0]
            # Transition to processing inside the same transaction that
            # holds the row lock so no other worker can grab it.
            db.execute(
                text(
                    "UPDATE ingestion_jobs "
                    "SET status = 'processing', started_at = :now, last_heartbeat = :now, "
                    "    message = 'Processing queued file' "
                    "WHERE id = :jid"
                ),
                {"now": datetime.now(timezone.utc), "jid": job_id},
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        # Now process outside the row-lock transaction.
        self._run_job(job_id)
        return True

    def _run_job(self, job_id: int) -> None:
        db = SessionLocal()
        try:
            job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
            if not job:
                logger.error("Ingestion job %s not found", job_id)
                return

            if job_id in self._cancelled:
                job.status = "failed"
                job.error_message = "Cancelled before processing started"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            # Status already set to "processing" by poll_and_run_one;
            # set thread-local context so parsers can call report_progress()
            _active_job.service = self
            _active_job.db = db
            _active_job.job_id = job_id

            result = self._process_job(db, job)
            job = db.get(IngestionJob, job_id)  # Refresh job state
            if result:
                job.status = "completed"
                job.completed_at = datetime.now(timezone.utc)
                job.scan_id = result.get("scan_id")
                job.tool_name = result.get("tool_name")
                job.message = result.get("message")
                job.parse_error_id = None
                # Persist parser ingestion quality (#7 from the v2.21.0
                # code review).  Skip-count + a short warning string land
                # on the job row so the UI can show "completed, 12 rows
                # skipped" instead of a silent partial success.
                job.skipped_count = result.get("skipped_count", 0)
                job.parser_warnings = result.get("parser_warnings")
                # Set uploaded_by on the scan record
                if job.scan_id and job.submitted_by_id:
                    from app.db.models import Scan
                    scan = db.get(Scan, job.scan_id)
                    if scan and not scan.uploaded_by_id:
                        scan.uploaded_by_id = job.submitted_by_id
                db.commit()
                # Observability — one structured line per completed job so an
                # ingestion backlog is debuggable from `docker logs` without
                # new infra: how long the job waited for a worker (queue age)
                # and how long the parse took.  The per-job timestamps already
                # exist on the row; nothing aggregated them before.
                queue_age_s = (
                    (job.started_at - job.created_at).total_seconds()
                    if job.started_at and job.created_at else None
                )
                parse_s = (
                    (job.completed_at - job.started_at).total_seconds()
                    if job.completed_at and job.started_at else None
                )
                logger.info(
                    "ingestion job=%s tool=%s scan=%s queue_age_s=%s "
                    "parse_s=%s skipped=%s",
                    job_id, job.tool_name, job.scan_id,
                    f"{queue_age_s:.1f}" if queue_age_s is not None else "n/a",
                    f"{parse_s:.1f}" if parse_s is not None else "n/a",
                    job.skipped_count,
                )
        except ParseFailure as exc:
            db.rollback()
            job = db.get(IngestionJob, job_id)
            if job:
                # Audit finding H4: populate the retry_count + last_error
                # dead-letter columns so the ingestion queue UI can
                # surface repeated failures distinctly from one-off
                # errors.  Parse failures are terminal on the first
                # attempt (the file is structurally invalid, retrying
                # won't help), so we still transition to 'failed' in
                # one hop — retry_count records that this job was
                # attempted once and failed deterministically.
                job.status = "failed"
                job.error_message = exc.user_message or exc.underlying_error or str(exc)
                job.message = job.error_message
                job.retry_count = (job.retry_count or 0) + 1
                job.last_error = f"ParseFailure: {job.error_message}"[:4000]
                if exc.error_id:
                    job.message = f"{job.message} (Error ID: {exc.error_id})"
                job.parse_error_id = exc.error_id
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
            logger.warning(
                "Failed ingestion job %s due to parse error: %s",
                job_id,
                exc.user_message or exc.underlying_error or str(exc),
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            # Unexpected exceptions (something other than a clean
            # ParseFailure) could be transient — DB hiccup, memory
            # pressure, an upstream service that went away mid-parse.
            # We still transition to 'failed' on the first hit because
            # there's no backoff or re-queue path in place, but the
            # retry_count / last_error columns let a future orphan
            # reaper (TODO) distinguish "crashed once" from "crashed
            # many times" and a human operator can re-queue a job by
            # flipping status back to 'queued'.
            import traceback as _tb
            db.rollback()
            job = db.get(IngestionJob, job_id)
            if job:
                job.status = "failed"
                job.error_message = str(exc)
                job.retry_count = (job.retry_count or 0) + 1
                # Keep a trimmed traceback for the UI — full stack
                # would bloat the column for huge parse graphs.
                tb_text = _tb.format_exc()
                job.last_error = (tb_text[-4000:] if len(tb_text) > 4000 else tb_text)
                job.completed_at = datetime.now(timezone.utc)
                job.parse_error_id = None
                db.commit()
            logger.exception("Failed ingestion job %s", job_id)
        finally:
            _active_job.service = None
            _active_job.db = None
            _active_job.job_id = None
            self._cancelled.discard(job_id)
            db.close()

    def _process_job(self, db: Session, job: IngestionJob) -> Optional[Dict[str, object]]:
        """Run parser detection and execute the first successful parser."""
        job_id = job.id
        # Cache scalar attributes before any rollback can expire them
        original_filename = job.original_filename
        storage_path = Path(job.storage_path)
        if not storage_path.exists():
            raise FileNotFoundError(f"Uploaded file missing at {storage_path}")

        job_project_id = (job.options or {}).get("project_id") or job.project_id

        sample = self._read_sample(storage_path)
        parsing_attempts = list(self._build_parsing_attempts(job, sample))
        if not parsing_attempts:
            preview = sample[:4096]
            parse_error = log_parse_error(
                db=db,
                filename=original_filename,
                file_content=preview,
                error_type="format_error",
                file_type="unknown",
                custom_message="Unsupported file type or format.",
                project_id=job_project_id,
            )
            raise ParseFailure(
                "Unsupported file type or format",
                user_message=parse_error.user_message,
                error_id=parse_error.id,
            )

        last_error: Optional[Exception] = None
        for file_type, parser_class, description in parsing_attempts:
            start = time.time()
            try:
                logger.info(
                    "Job %s: attempting parser %s for %s",
                    job_id,
                    parser_class.__name__,
                    job.original_filename,
                )
                result = self._execute_parser(db, job, parser_class, description)
                elapsed = time.time() - start
                logger.info(
                    "Job %s: parser %s succeeded in %.2fs",
                    job_id,
                    parser_class.__name__,
                    elapsed,
                )
                return result
            except Exception as exc:
                db.rollback()
                elapsed = time.time() - start
                logger.warning(
                    "Job %s: parser %s failed after %.2fs: %s",
                    job_id,
                    parser_class.__name__,
                    elapsed,
                    exc,
                )
                last_error = exc
                continue

        preview = sample[:4096]
        parse_error = log_parse_error(
            db=db,
            filename=original_filename,
            file_content=preview,
            error=last_error,
            error_type="parsing_error",
            file_type=parsing_attempts[0][0] if parsing_attempts else "unknown",
            project_id=job_project_id,
        )
        raise ParseFailure(
            "Failed to parse file",
            user_message=parse_error.user_message,
            error_id=parse_error.id,
            underlying_error=str(last_error) if last_error else None,
        )

    def _execute_parser(
        self,
        db: Session,
        job: IngestionJob,
        parser_class: Type,
        description: str,
    ) -> Dict[str, object]:
        from app.parsers.nmap_parser import NmapXMLParser
        from app.parsers.eyewitness_parser import EyewitnessParser
        from app.parsers.masscan_parser import MasscanParser
        from app.parsers.dns_parser import DNSParser
        from app.parsers.netexec_parser import NetexecParser
        from app.parsers.naabu_parser import NaabuParser
        from app.parsers.rustscan_parser import RustScanParser
        from app.parsers.openvas_parser import OpenVASParser
        from app.parsers.amass_parser import AmassParser
        from app.parsers.nikto_parser import NiktoParser
        from app.parsers.smbmap_parser import SMBMapParser
        from app.parsers.bloodhound_parser import BloodHoundParser
        from app.services.nessus_integration_service import NessusIntegrationService

        options = job.options or {}
        storage_path = job.storage_path
        filename = job.original_filename

        project_id = options.get("project_id")
        # v2.28.1 — initialise so every branch (Nessus, generic parser
        # dispatch) hands back a defined value.  Previously only the
        # generic branch assigned `parse_stats`, so the Nessus path
        # crashed with UnboundLocalError at the return statement.
        # Nessus doesn't currently expose ingest-quality stats so the
        # default empty dict is the right floor.
        parse_stats: Dict[str, object] = {}
        # v2.55.1 — same hazard as parse_stats: the tool_name_hint
        # mismatch logic added in v2.55.0 initialised `warnings_parts`
        # inside the generic else branch but referenced it
        # unconditionally at the return statement.  Any Nessus upload
        # therefore crashed with UnboundLocalError before the job was
        # marked completed.  Hoist init here so the Nessus and
        # generic branches share the same warnings list, and run the
        # mismatch check AFTER the if/else convergence so it covers
        # both code paths.
        warnings_parts: List[str] = []

        if parser_class is NessusIntegrationService:
            parser_instance = NessusIntegrationService(db)
            result = parser_instance.process_nessus_file(storage_path, filename, project_id=project_id)
            if not result.get("success"):
                nessus_error_msg = result.get("error") or result.get("message") or "Nessus processing failed"
                parse_error = log_parse_error(
                    db=db,
                    filename=filename,
                    error_type="parsing_error",
                    file_type="nessus_xml",
                    custom_message=result.get("message") or nessus_error_msg,
                    project_id=project_id,
                )
                raise ParseFailure(
                    "Nessus processing failed",
                    user_message=result.get("message") or result.get("error"),
                    error_id=parse_error.id,
                    underlying_error=result.get("error"),
                )
            db.commit()
            scan_id = result.get("scan_id")
            message = result.get("message")
            tool_name = "Nessus"
            # v2.91.3 — surface partial-ingest warnings (per-finding
            # write failures, per-host processing failures) into the
            # standard parser_warnings channel so they reach
            # IngestionJob.warnings and the upload UI's warning panel.
            # Hard truncation lands in the if-not-success branch above;
            # this branch handles soft warnings on a successful import.
            nessus_warnings = result.get("warnings") or []
            for w in nessus_warnings:
                if w:
                    warnings_parts.append(str(w))
        else:
            # Map class references back to callable constructors
            # Lazy-import parsers that may not be in top-level scope
            _extra_parsers: dict = {}
            try:
                from app.parsers.gnmap_parser import GnmapParser as _GnmapParser
                _extra_parsers[_GnmapParser] = _GnmapParser
            except ImportError:
                pass
            try:
                from app.parsers.dirbuster_parser import DirBusterParser as _DirBusterParser
                _extra_parsers[_DirBusterParser] = _DirBusterParser
            except ImportError:
                pass
            # v2.12.0 — register the new httpx parser.  The dispatcher
            # rejects parser_class entries it doesn't know about (raises
            # "Unsupported parser class"), so any new parser added to
            # _build_parsing_attempts must also be registered here.
            try:
                from app.parsers.httpx_parser import HttpxParser as _HttpxParser
                _extra_parsers[_HttpxParser] = _HttpxParser
            except ImportError:
                pass
            # v2.88.0 — register dnsx parser (closes #44).
            try:
                from app.parsers.dnsx_parser import DnsxParser as _DnsxParser
                _extra_parsers[_DnsxParser] = _DnsxParser
            except ImportError:
                pass

            parser_map = {
                NmapXMLParser: NmapXMLParser,
                EyewitnessParser: EyewitnessParser,
                MasscanParser: MasscanParser,
                DNSParser: DNSParser,
                NetexecParser: NetexecParser,
                NaabuParser: NaabuParser,
                RustScanParser: RustScanParser,
                OpenVASParser: OpenVASParser,
                AmassParser: AmassParser,
                NiktoParser: NiktoParser,
                SMBMapParser: SMBMapParser,
                BloodHoundParser: BloodHoundParser,
                **_extra_parsers,
            }
            parser_ctor = parser_map.get(parser_class)
            if parser_ctor is None:
                raise ValueError(f"Unsupported parser class {parser_class}")

            parser = parser_ctor(db)
            scan = parser.parse_file(storage_path, filename, project_id=project_id)
            # Ensure scan and all hosts are tagged with the project
            if project_id and scan:
                scan.project_id = project_id
            # v2.46.4 — provenance: agents pass the exact invocation as
            # `command_run` on /agent/recon/upload, but only self-
            # describing formats (nmap embeds <nmaprun args=...>) leave
            # the parser anything to put on Scan.command_line.  For
            # every other tool (masscan list/json, httpx, naabu,
            # rustscan, netexec, ...) the agent's command_run was
            # captured into IngestionJob.options but never reached the
            # Scan row, so ScanDetail showed "No command line data".
            # Backfill it here: the parser-extracted value wins when
            # present; the agent-supplied one fills the gap otherwise.
            if scan is not None:
                agent_command = (options.get("command_run") or "").strip()
                if agent_command and not (scan.command_line or "").strip():
                    scan.command_line = agent_command
            db.commit()
            scan_id = getattr(scan, "id", None)
            tool_name = getattr(scan, "tool_name", parser_class.__name__)
            message = f"{description} processed successfully"
            # Parsers that track ingestion quality (httpx, eyewitness)
            # expose last_parse_stats; the rest leave it absent and we
            # default to "0 skipped, no warnings".  See completion block
            # in poll_and_run_one for where this lands on the job row.
            parse_stats = getattr(parser, "last_parse_stats", None) or {}

        # tool_name_hint mismatch detection (v2.55.0 review finding M-1,
        # repositioned in v2.55.1 to cover BOTH branches).
        # `/agent/recon/upload` accepts a `tool_name` arg and stores it
        # as `options["tool_name_hint"]`.  If the agent declared one
        # tool but a different parser succeeded, that's worth surfacing
        # — historically the hint was captured and never checked, so an
        # agent claiming "this is naabu" could end up with a
        # `tool_name='masscan'` scan and the operator would never know.
        # Same logic applies to the Nessus path: a non-Nessus file that
        # falls through to the last-ditch nessus_xml attempt should
        # surface "agent declared X, parser detected Nessus" too.
        existing_warnings = parse_stats.get("warnings") if parse_stats else None
        if existing_warnings:
            warnings_parts.append(str(existing_warnings))
        hint_raw = (options.get("tool_name_hint") or "").strip()
        if hint_raw and tool_name:
            hint_norm = hint_raw.lower()
            actual_norm = str(tool_name).lower()
            # "subfinder" parsed by AmassParser tags the scan
            # ``tool_name='subfinder'`` already; equal-strings check is
            # enough.  Use `startswith` either direction so near-matches
            # ("masscan-list" vs "masscan") don't fire.
            if (
                hint_norm != actual_norm
                and not hint_norm.startswith(actual_norm)
                and not actual_norm.startswith(hint_norm)
            ):
                mismatch_msg = (
                    f"Agent declared tool '{hint_raw}' but parser detected "
                    f"'{tool_name}'. The file likely doesn't match the declared "
                    f"tool — verify the upload before relying on the parsed data."
                )
                logger.warning("Job %s: %s", job.id, mismatch_msg)
                warnings_parts.append(mismatch_msg)

        if options.get("enrich_dns") and scan_id:
            dns_server = options.get("dns_server")
            enriched = self._enrich_dns(db, scan_id, dns_server, project_id=project_id)
            if enriched:
                db.commit()
                message = f"{message} (DNS enriched {enriched} hosts)"

        return {
            "scan_id": scan_id,
            "message": message,
            "tool_name": tool_name,
            "skipped_count": int(parse_stats.get("skipped", 0)) if parse_stats else 0,
            "parser_warnings": " | ".join(warnings_parts) if warnings_parts else None,
        }

    def _enrich_dns(self, db: Session, scan_id: int, dns_server: Optional[str], project_id: int = None) -> int:
        dns_service = DNSService(db, custom_dns_server=dns_server if dns_server else None, project_id=project_id)
        hosts = (
            db.query(Host)
            .join(HostScanHistory, Host.id == HostScanHistory.host_id)
            .filter(HostScanHistory.scan_id == scan_id)
            .all()
        )

        enriched_count = 0
        total = len(hosts)
        # Commit in batches rather than per host, and emit progress every
        # batch.  report_progress doubles as the job's timeout/cancel
        # checkpoint (raises ParseFailure if the job has been cancelled or
        # the heartbeat lapsed), so a large enrichment run over a slow
        # resolver is observable and can bail out instead of silently
        # running past the job timeout and looking hung.
        _DNS_COMMIT_BATCH = 100
        for idx, host in enumerate(hosts, start=1):
            try:
                enrichment = dns_service.enrich_host_data(host)
                if enrichment.get("reverse_dns") or enrichment.get("dns_records"):
                    enriched_count += 1
            except Exception as exc:  # pragma: no cover - log and continue
                logger.warning(
                    "DNS enrichment failed for host %s: %s",
                    host.ip_address,
                    exc,
                )
            if idx % _DNS_COMMIT_BATCH == 0:
                db.commit()
                report_progress(f"DNS enrichment: {idx}/{total} hosts")
        db.commit()
        return enriched_count

    # ------------------------------------------------------------------
    # Parser detection helpers

    def _build_parsing_attempts(
        self, job: IngestionJob, sample: bytes
    ) -> Iterable[ParserDescriptor]:
        from app.parsers.nmap_parser import NmapXMLParser
        from app.parsers.eyewitness_parser import EyewitnessParser
        from app.parsers.masscan_parser import MasscanParser
        from app.parsers.dns_parser import DNSParser
        from app.parsers.netexec_parser import NetexecParser
        from app.parsers.naabu_parser import NaabuParser
        from app.parsers.rustscan_parser import RustScanParser
        from app.parsers.openvas_parser import OpenVASParser
        from app.parsers.amass_parser import AmassParser
        from app.parsers.nikto_parser import NiktoParser
        from app.parsers.smbmap_parser import SMBMapParser
        from app.parsers.bloodhound_parser import BloodHoundParser
        from app.parsers.dirbuster_parser import DirBusterParser
        from app.services.nessus_integration_service import NessusIntegrationService

        filename = job.original_filename.lower()
        attempts: List[ParserDescriptor] = []

        if filename.endswith(".nessus") or (
            filename.endswith(".xml") and _cd.is_nessus_sample(sample)
        ):
            attempts.append(("nessus_xml", NessusIntegrationService, "Nessus vulnerability scan"))

        if filename.endswith(".xml"):
            # v2.45.1 — dispatcher ordered by structural specificity.
            # The bug history that drives this ordering:
            #
            #   1. Pre-fix: looks_like_openvas matched "openvas" or
            #      "greenbone" anywhere in the body, so nmap XML
            #      whose NSE script output captured cert subjects
            #      ("Greenbone AG" via ssl-cert) or page titles
            #      ("OpenVAS Scan" via http-title) routed to
            #      OpenVASParser before nmap got a chance.  Operators
            #      had to sanitize their own scan output as a workaround.
            #
            #   2. Masscan emits XML with root element <nmaprun
            #      scanner="masscan">, sharing the root tag with
            #      genuine nmap output.  Root-element check alone
            #      can't distinguish them — must inspect the scanner
            #      attribute via looks_like_masscan_xml.
            #
            # Decision tree:
            #   * is_masscan_xml (scanner="masscan") → masscan first.
            #   * is_openvas (root=<report>/<openvas-results> OR
            #     filename match) → openvas first.
            #   * is_nmap_root (root=<nmaprun> without masscan attr)
            #     → nmap first.
            #   * No structural match → fall through to legacy
            #     "try every parser" order (nmap, openvas, masscan, nessus).
            is_masscan_xml = _cd.looks_like_masscan_xml(sample)
            is_openvas = _cd.looks_like_openvas(sample, filename)
            is_nmap_root = _cd.looks_like_nmap_xml(sample)

            if is_masscan_xml:
                attempts.append(("masscan_xml", MasscanParser, "Masscan XML file"))
                # Nmap parser as fallback — masscan's XML format is a
                # subset of nmap's, so nmap may still produce useful
                # data if masscan parser hiccups on a malformed edge.
                attempts.append(("nmap_xml", NmapXMLParser, "Nmap XML file"))
            elif is_openvas:
                attempts.append(("openvas_xml", OpenVASParser, "OpenVAS/Greenbone XML report"))
                attempts.append(("nmap_xml", NmapXMLParser, "Nmap XML file"))
            elif is_nmap_root:
                attempts.append(("nmap_xml", NmapXMLParser, "Nmap XML file"))
                # Openvas root excludes nmaprun by construction, so
                # don't try openvas here; masscan as last fallback.
                attempts.append(("masscan_xml", MasscanParser, "Masscan XML file"))
            else:
                # No structural signal — keep the pre-v2.45.1 try-everything order.
                attempts.append(("nmap_xml", NmapXMLParser, "Nmap XML file"))
                attempts.append(("openvas_xml", OpenVASParser, "OpenVAS/Greenbone XML report"))
                attempts.append(("masscan_xml", MasscanParser, "Masscan XML file"))

            # Always include Nessus as a last-ditch attempt — covers
            # .xml files that are actually .nessus exports mislabeled.
            attempts.append(("nessus_xml", NessusIntegrationService, "Nessus vulnerability scan"))
        elif filename.endswith(".gnmap"):
            try:
                from app.parsers.gnmap_parser import GnmapParser

                attempts.append(("nmap_gnmap", GnmapParser, "Nmap .gnmap file"))
            except ImportError as exc:
                logger.warning("Gnmap parser unavailable: %s", exc)
        elif filename.endswith(".json") or filename.endswith(".jsonl"):
            # httpx first because it has a very specific content
            # signature (``tech`` + ``webserver`` + ``url``) that rarely
            # false-positives.  Must come before other JSON probes.
            from app.parsers.httpx_parser import HttpxParser, looks_like_httpx
            if looks_like_httpx(sample, filename):
                attempts.append(("httpx_json", HttpxParser, "httpx web fingerprint (JSON/JSONL)"))
            if _cd.looks_like_bloodhound(sample, filename):
                attempts.append(("bloodhound_json", BloodHoundParser, "BloodHound/SharpHound JSON export"))
            if _cd.looks_like_amass(sample, filename):
                attempts.append(("amass_json", AmassParser, "Amass/Subfinder JSON output"))
            # v2.88.0 — dnsx JSON output (closes #44).  Operators run
            # dnsx terminal-side against operator-supplied resolvers;
            # we ingest the resulting records and feed PTR answers
            # back into Host.hostname.
            if _cd.looks_like_dnsx(sample, filename):
                from app.parsers.dnsx_parser import DnsxParser
                attempts.append(("dnsx_json", DnsxParser, "dnsx DNS resolution JSON"))
            if _cd.looks_like_naabu(sample, filename):
                attempts.append(("naabu_json", NaabuParser, "Naabu JSON output"))
            if _cd.looks_like_netexec(sample, filename):
                from app.parsers.netexec_parser import NetexecParser as NetexecJsonParser

                attempts.append(("netexec_json", NetexecJsonParser, "NetExec JSON output"))
            if "masscan" in filename or _cd.looks_like_masscan_json(sample):
                attempts.append(("masscan_json", MasscanParser, "Masscan JSON file"))
            if "eyewitness" in filename or "report" in filename or _cd.looks_like_eyewitness_json(sample):
                attempts.append(("eyewitness_json", EyewitnessParser, "EyeWitness report"))
            if _cd.looks_like_nikto(sample, filename):
                attempts.append(("nikto_json", NiktoParser, "Nikto JSON report"))
            if _cd.looks_like_smbmap(sample, filename):
                attempts.append(("smbmap_json", SMBMapParser, "SMBMap JSON output"))
            if _cd.looks_like_dirbuster(sample, filename):
                attempts.append(("dirbuster_json", DirBusterParser, "Web content discovery JSON"))
        elif filename.endswith(".zip"):
            # EyeWitness bundle — contains report JSON + screenshots.
            # The parser does its own zip extraction; we just route here.
            attempts.append(("eyewitness_zip", EyewitnessParser, "EyeWitness bundle (zip with report + screenshots)"))
        elif filename.endswith(".csv"):
            if "eyewitness" in filename or "report" in filename:
                attempts.append(("eyewitness_csv", EyewitnessParser, "Eyewitness report"))
            if _cd.looks_like_nikto(sample, filename):
                attempts.append(("nikto_csv", NiktoParser, "Nikto CSV report"))
            if _cd.looks_like_dirbuster(sample, filename):
                attempts.append(("dirbuster_csv", DirBusterParser, "Web content discovery CSV"))
            # Gate dns_csv on a positive header heuristic.  The previous
            # unconditional append turned any unrecognised CSV into a
            # silent `tool_name='dns'` scan with zero records — the
            # DNSParser creates the Scan row before validating headers
            # and only raises if the file has NO header at all.  An
            # arbitrary CSV with arbitrary headers therefore passed.
            if _cd.looks_like_dns_csv(sample):
                attempts.append(("dns_csv", DNSParser, "DNS records CSV file"))
        elif filename.endswith(".txt"):
            if _cd.looks_like_rustscan(sample, filename):
                attempts.append(("rustscan_output", RustScanParser, "RustScan output file"))
            if _cd.looks_like_smbmap(sample, filename):
                attempts.append(("smbmap_output", SMBMapParser, "SMBMap output file"))
            if _cd.looks_like_nikto(sample, filename):
                attempts.append(("nikto_output", NiktoParser, "Nikto text report"))
            if _cd.looks_like_amass(sample, filename):
                attempts.append(("amass_output", AmassParser, "Amass/Subfinder output file"))
            if _cd.looks_like_netexec(sample, filename):
                attempts.append(("netexec_output", NetexecParser, "NetExec output file"))
            if _cd.looks_like_naabu(sample, filename):
                attempts.append(("naabu_output", NaabuParser, "Naabu output file"))
            if _cd.looks_like_dirbuster(sample, filename):
                attempts.append(("dirbuster_output", DirBusterParser, "Web content discovery output"))
            if _cd.looks_like_masscan_list(sample):
                attempts.append(("masscan_list", MasscanParser, "Masscan list output file"))
            if _cd.looks_like_gnmap(sample):
                try:
                    from app.parsers.gnmap_parser import GnmapParser
                    attempts.append(("gnmap_txt", GnmapParser, "Greppable scan output (.txt)"))
                except ImportError as exc:
                    logger.warning("Gnmap parser unavailable for .txt greppable detection: %s", exc)
            # Previously a final `if not attempts: append masscan_list`
            # turned any unrecognised .txt into a completed
            # `tool_name='masscan'` scan with zero hosts (MasscanParser
            # treats empty input as success — `db.commit(); return scan`).
            # That fallback is gone: if `looks_like_masscan_list`
            # didn't fire above, this isn't a masscan list.  An empty
            # ``attempts`` list now triggers `_process_job`'s
            # "Unsupported file type or format" parse_error path.

        return attempts

    def _read_sample(self, path: Path, size: int = 64 * 1024) -> bytes:
        with path.open("rb") as handle:
            return handle.read(size)



ingestion_service = IngestionService()

__all__ = ["ingestion_service", "IngestionService"]
