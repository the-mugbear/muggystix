"""Staged import (v2.352.0; phase B of the staged-import plan).

An upload can be *staged* instead of queued: the file lands on disk and a job
row exists, but no worker touches it until the operator starts it.  In
between, this service answers "what is this file?" honestly and without
touching the inventory:

* **candidates** — the parsers the dispatcher would try, in its order, each
  with its *basis*: ``structure`` when the file's content alone selects it,
  ``filename`` when only the name does, ``fallback`` when the dispatcher
  would merely TRY it without having recognised anything (the XML branch's
  tail; marked at the source by ``ingestion_service.FallbackAttempt``).  No
  detector was rewritten for this; the dispatcher is run twice, once with a
  neutral filename and once with the real one, and the difference is the
  basis.  Only ``structure`` may make a file ready without the operator.
* **preview** — the first two kilobytes of the file as text, plus a small
  *sample* of interpreted records where an extractor exists (nmap XML, JSON
  tools, CSV, plain text).  The sample is what the reader understood, e.g.
  "10.0.0.5: 3 open ports (22, 80, 443)", not what the real parser would
  write — the real parsers commit as they go and cannot be dry-run.
* **needs_choice** — the operator must pick a format when nothing was
  recognised (no candidate, or only fallbacks), when the only basis is the
  filename, or when several
  detectors fired on a JSON / CSV / text file (an XML file follows the
  dispatcher's ordered decision tree, so its trailing fallbacks are not an
  ambiguity).

``start_staged_job`` records the operator's choice on the job (phase A's
columns) and queues it.  The same call re-queues a *failed* job whose file
is still on disk — "review the format and retry" (phase E) falls out of it.
Staged jobs nobody starts expire after a day.
"""
from __future__ import annotations

import csv
import io
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import IngestionJob
from app.parsers import content_detection as _cd
from app.services.format_registry import FORMATS, format_label

logger = logging.getLogger(__name__)

STAGED_STATUS = "staged"
STAGED_MAX_AGE = timedelta(hours=24)
# The error messages that mark a staged job's two non-import endings. The
# Scans page's batch rows count them by message, so they are named once.
DISCARDED_MESSAGE = "Discarded before import"
EXPIRED_MESSAGE_PREFIX = "Staged upload expired"
_SAMPLE_BYTES = 64 * 1024
_RAW_PREVIEW_BYTES = 2048
_TEXT_EXTENSIONS = {".json", ".jsonl", ".ndjson", ".csv", ".txt"}
_JSON_KEYS = ("ip", "host", "hostname", "name", "url", "target", "input", "port", "ports", "addresses", "a")


def _read_sample(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read(_SAMPLE_BYTES)


def _attempts(service, filename: str, sample: bytes) -> List[tuple]:
    """``(file_type, is_fallback)`` in dispatcher order, each type once.  A
    type the dispatcher both recognised and re-appends as a last-ditch
    fallback (Nessus) keeps its first, recognised, appearance."""
    shadow = SimpleNamespace(original_filename=filename, options={}, format_override=None)
    out: List[tuple] = []
    seen = set()
    for attempt in service._build_parsing_attempts(shadow, sample):
        ft = attempt[0]
        if ft in seen:
            continue
        seen.add(ft)
        out.append((ft, bool(getattr(attempt, "fallback", False))))
    return out


def detect_for_job(job: IngestionJob) -> Dict[str, Any]:
    """Candidates with their basis, a raw preview and an interpreted sample."""
    # v2.354.1 — use the module singleton.  Constructing a new IngestionService
    # per request ran its storage-writability probe, whose test file is named
    # by process id; concurrent detections from a multi-file upload raced on
    # that one name and the loser failed with "storage is not writable".
    from app.services.ingestion_service import ingestion_service as service

    path = Path(job.storage_path)
    if not path.exists():
        raise FileNotFoundError(job.storage_path)
    sample = _read_sample(path)
    name = job.original_filename or "upload"
    ext = Path(name).suffix.lower()

    real = _attempts(service, name, sample)
    # Recognised with a neutral filename AND not as a fallback: only that is
    # "structure".  Surviving the neutral pass alone is not evidence — the
    # XML branch appends its fallback parsers whatever the content is.
    neutral = {ft for ft, is_fallback in _attempts(service, f"upload{ext}", sample) if not is_fallback}

    candidates = []
    for rank, (ft, is_fallback) in enumerate(real):
        if is_fallback:
            basis = "fallback"
        elif ft in neutral:
            basis = "structure"
        else:
            basis = "filename"
        candidates.append({
            "file_type": ft,
            "label": format_label(ft),
            "basis": basis,
            "rank": rank,
        })
    structural = [c for c in candidates if c["basis"] == "structure"]
    primary = candidates[0] if candidates else None
    if primary is None:
        reason = "No distinctive signature was recognised."
    elif primary["basis"] == "fallback":
        reason = (
            "No distinctive signature was recognised. The formats listed are only what "
            f"a {ext or 'file'} is tried against when nothing matches."
        )
    elif primary["basis"] == "filename":
        reason = "Recognised from the filename only; the content did not confirm it."
    elif ext in _TEXT_EXTENSIONS and len(structural) > 1:
        reason = "More than one format matches this content."
    else:
        reason = None

    return {
        "job_id": job.id,
        "filename": name,
        "candidates": candidates,
        "primary": primary["file_type"] if primary else None,
        "needs_choice": reason is not None,
        "reason": reason,
        "preview": {
            "raw": sample[:_RAW_PREVIEW_BYTES].decode("utf-8", errors="replace"),
            "sample": _interpreted_sample(path, sample, ext, primary["file_type"] if primary else None),
        },
        "formats": [
            {"file_type": s.file_type, "label": s.label, "family": s.family}
            for s in FORMATS.values()
        ],
    }


def _interpreted_sample(path: Path, sample: bytes, ext: str, primary: Optional[str]) -> List[str]:
    """A few lines of what the reader understood — best effort, never raises."""
    try:
        root = _cd._xml_root_element(sample)
        if root == "nmaprun":
            return _nmap_sample(path)
        if ext in (".json", ".jsonl", ".ndjson"):
            kind, rec = _cd._peek_json_shape(sample)
            if isinstance(rec, dict):
                fields = [f"{k}={_short(rec[k])}" for k in _JSON_KEYS if k in rec]
                if not fields:
                    fields = [f"{k}={_short(v)}" for k, v in list(rec.items())[:4]]
                return [f"first record ({kind}): " + ", ".join(fields)]
            return []
        if ext == ".csv":
            rows = list(csv.reader(io.StringIO(sample.decode("utf-8", errors="replace"))))
            out = []
            if rows:
                out.append("columns: " + ", ".join(c.strip() for c in rows[0][:8]))
            if len(rows) > 1:
                out.append("first row: " + ", ".join(c.strip() for c in rows[1][:8]))
            return out
        text = sample.decode("utf-8", errors="replace")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        return lines[:5]
    except Exception:  # pragma: no cover — a preview must never break detection
        logger.debug("interpreted sample failed for %s", path, exc_info=True)
        return []


def _short(value: Any, limit: int = 60) -> str:
    s = str(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _nmap_sample(path: Path, max_hosts: int = 2) -> List[str]:
    from app.parsers.xml_stream_helpers import clear_element, iterparse_safe, strip_namespace

    out: List[str] = []
    with path.open("rb") as fh:
        for _event, elem in iterparse_safe(fh, events=("end",)):
            if strip_namespace(elem.tag) != "host":
                continue
            addr = None
            for a in elem.iter():
                if strip_namespace(a.tag) == "address" and a.get("addrtype", "").startswith("ipv"):
                    addr = a.get("addr")
                    break
            open_ports = []
            for p in elem.iter():
                if strip_namespace(p.tag) != "port":
                    continue
                state = next((s for s in p if strip_namespace(s.tag) == "state"), None)
                if state is not None and state.get("state") == "open":
                    open_ports.append(f"{p.get('protocol', 'tcp')}/{p.get('portid')}")
            out.append(
                f"{addr or 'host'}: {len(open_ports)} open port{'' if len(open_ports) == 1 else 's'}"
                + (f" ({', '.join(open_ports[:6])}{'…' if len(open_ports) > 6 else ''})" if open_ports else "")
            )
            clear_element(elem)
            if len(out) >= max_hosts:
                break
    return out


def start_staged_job(
    db: Session, job: IngestionJob, *, format_override: Optional[str], source_tool: Optional[str],
) -> IngestionJob:
    """Record the operator's choice and queue the job.  Allowed from
    ``staged`` and from ``failed`` (the retained file is re-read with the
    corrected format).  Raises ValueError for an unknown format."""
    if format_override is not None and format_override not in FORMATS:
        raise ValueError(f"Unknown format '{format_override}'")
    return queue_job(
        db, job.id, allowed_from=(STAGED_STATUS, "failed"),
        choice={
            "format_override": format_override,
            "source_tool": (source_tool or "").strip()[:64] or None,
        },
    )


def queue_job(
    db: Session, job_id: int, *, allowed_from: tuple, choice: Optional[Dict[str, Any]] = None,
) -> IngestionJob:
    """staged | failed → queued: the ONE path, for the format review's start
    and for a plain retry of a failed job.

    Checked under the row lock (``job_transitions.retry``): the status, the
    file on disk and the duplicate guard.  A double start (two tabs, the
    review dialog plus the results page) therefore cannot reset a job a
    worker already claimed, and a failed job whose file another job has since
    imported is refused as a fresh upload of it would be.

    ``choice`` is the operator's format and tool; None keeps what the job
    already carries (a retry runs the same import again).  A job leaving
    ``failed`` counts one more attempt.

    Raises ``JobNotTransitionable`` (its ``status`` is the state found, or
    "file_missing"), ``DuplicateUploadError``, or ``LookupError`` when the
    row is gone.  Commits."""
    from app.services.ingestion_service import _transitions, ingestion_service

    duplicate: Dict[str, Any] = {}
    found_as: Dict[str, Any] = {}

    def _precondition(locked: IngestionJob) -> Optional[str]:
        found_as["status"] = locked.status
        if not locked.storage_path or not Path(locked.storage_path).exists():
            return "file_missing"
        # The upload-time guard ran when this file arrived; since then an
        # identical file may have been imported or queued (a failed job can
        # sit for days).  Skipped when the operator chose "import anyway" at
        # upload.
        if locked.content_sha256 and not (locked.options or {}).get("allow_duplicate"):
            found = ingestion_service._find_duplicate(
                db, locked.project_id, locked.content_sha256, exclude_job_id=locked.id,
            )
            # A staged twin is not a reason to refuse: one of the two has to be
            # startable, and whichever starts first then blocks the other.
            if found is not None and found.job_status != STAGED_STATUS:
                duplicate["error"] = found
                return "duplicate"
        return None

    try:
        started = _transitions.retry(
            db, job_id,
            allowed_from=allowed_from,
            precondition=_precondition,
            parse_error_id=None,
            **(choice or {}),
        )
    except Exception as exc:
        db.rollback()
        if duplicate:
            raise duplicate["error"] from exc
        raise
    if started is None:  # deleted between the caller's load and the lock
        db.rollback()
        raise LookupError(f"Ingestion job {job_id} no longer exists")
    if found_as.get("status") == "failed":
        started.retry_count = (started.retry_count or 0) + 1
    # The operator queued it: the reaper's budget starts over.
    started.reap_count = 0
    if choice is None:
        started.message = f"Re-queued by user (attempt {started.retry_count})."
    else:
        chosen = choice.get("format_override")
        started.message = "Queued by the operator" + (f" as {format_label(chosen)}" if chosen else "")
    db.commit()
    db.refresh(started)
    return started


def retention_window() -> timedelta:
    from app.core.config import settings
    return timedelta(days=max(int(getattr(settings, "INGESTION_RETAIN_FILES_DAYS", 7)), 0))


def file_retained(job: IngestionJob) -> bool:
    """Whether the job's file is kept, as the ROW says: nothing has recorded
    removing it.  For pages that serialise many jobs — no disk access.  A
    path that is about to read the file asks the disk (``file_on_disk``)."""
    return bool(job.storage_path) and job.file_removed_at is None


def file_on_disk(job: IngestionJob) -> bool:
    return bool(job.storage_path) and Path(job.storage_path).exists()


def _remove_file_of_locked_job(job: IngestionJob, now: datetime) -> bool:
    """Remove a job's upload and record it on the row.  The caller holds the
    row's lock and has already decided (under it) that the file goes; its
    commit makes the stamp durable.  ``/start`` and ``/retry`` check the file
    under the same lock, so neither can pass that check on a file about to
    disappear.  A directory that cannot be removed is logged and left
    unstamped — the retention sweep comes back to it."""
    if job.storage_path:
        shutil.rmtree(Path(job.storage_path).parent, ignore_errors=True)
        if Path(job.storage_path).exists():
            logger.warning(
                "Could not remove the uploaded file of ingestion job %s (%s)",
                job.id, job.storage_path,
            )
            return False
    job.file_removed_at = now
    return True


def retained_until(job: IngestionJob) -> Optional[datetime]:
    """When a finished job's file will be removed, or None while the job is
    still open (or the file is already gone)."""
    if job.status not in ("completed", "failed") or not file_retained(job):
        return None
    base = job.completed_at or job.created_at
    if base is None:
        return None
    base = base if base.tzinfo else base.replace(tzinfo=timezone.utc)
    return base + retention_window()


def expire_retained_files(db: Session, *, now: Optional[datetime] = None) -> int:
    """Remove the files of finished jobs past the retention window (phase E).
    The job rows stay — they are the record; only the bytes go."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - retention_window()
    # The rows stay for ever, so "finished and past the window" only grows;
    # a row whose file is recorded as removed is never looked at again.
    expired = (
        (IngestionJob.status.in_(("completed", "failed")))
        & (
            (IngestionJob.completed_at < cutoff)
            | ((IngestionJob.completed_at.is_(None)) & (IngestionJob.created_at < cutoff))
        )
        & IngestionJob.file_removed_at.is_(None)
    )
    removed = 0
    candidates = db.query(IngestionJob.id, IngestionJob.storage_path).filter(expired).all()
    # Files already gone (removed before the row recorded it, or by hand):
    # stamped in one statement.  No lock needed — nothing re-creates a job's
    # file, so "gone" cannot become untrue.
    already_gone = [jid for jid, path in candidates if not (path and Path(path).exists())]
    for start in range(0, len(already_gone), 1000):
        db.query(IngestionJob).filter(
            IngestionJob.id.in_(already_gone[start:start + 1000]), expired,
        ).update({IngestionJob.file_removed_at: now}, synchronize_session=False)
        db.commit()
    gone = set(already_gone)
    for job_id, _path in candidates:
        if job_id in gone:
            continue
        # Each file goes while its row is LOCKED, with the predicate
        # re-checked under the lock.  Deleting from a snapshot read raced a
        # retry: the retry (which checks the file exists under this same row
        # lock) was accepted, the job re-queued, and then this sweep removed
        # its input.  Now either the retry wins (the job is 'queued', no
        # longer matched) or the sweep does (the retry then reports
        # file_missing).  SKIP LOCKED: a job being retried right now is
        # simply left for the next sweep.
        job = (
            db.query(IngestionJob)
            .filter(IngestionJob.id == job_id, expired)
            .with_for_update(skip_locked=True)
            .populate_existing()
            .one_or_none()
        )
        if job is not None:
            had_file = file_on_disk(job)
            # A file that cannot be removed is tried again next sweep, and
            # said: a sweep that cannot delete is how the uploads disk fills
            # unnoticed.
            if _remove_file_of_locked_job(job, now) and had_file:
                removed += 1
        db.commit()  # release the row lock before the next job
    return removed


def reprocess_job(
    db: Session, job: IngestionJob, *, submitted_by_id: Optional[int],
    format_override: Optional[str], source_tool: Optional[str],
) -> IngestionJob:
    """An explicit re-import of a finished job's retained file as a NEW job
    (phase E).  A new scan record is created when it parses; the prior scan
    and everything it contributed stay until that scan is deleted; the
    duplicate guard is bypassed on purpose — the operator asked for this.
    The file is copied into the new job's own directory so each job's
    retention is independent."""
    from uuid import uuid4
    from app.services.ingestion_service import ingestion_service

    if format_override is not None and format_override not in FORMATS:
        raise ValueError(f"Unknown format '{format_override}'")
    if not file_on_disk(job):
        raise FileNotFoundError(job.storage_path)
    src = Path(job.storage_path)
    job_dir = ingestion_service._storage_root / uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    dst = job_dir / src.name
    shutil.copy2(src, dst)
    options = dict(job.options or {})
    options["reprocess_of_job_id"] = job.id
    options.pop("format_override", None)
    new = IngestionJob(
        filename=dst.name,
        original_filename=job.original_filename,
        storage_path=str(dst),
        status="queued",
        file_size=job.file_size,
        options=options,
        submitted_by_id=submitted_by_id,
        project_id=job.project_id,
        agent_session_id=job.agent_session_id,
        batch_id=job.batch_id,
        content_sha256=job.content_sha256,
        format_override=format_override,
        source_tool=(source_tool or "").strip()[:64] or None,
        message=(
            f"Re-process of job #{job.id}"
            + (f" as {format_label(format_override)}" if format_override else "")
        ),
    )
    db.add(new)
    db.commit()
    db.refresh(new)
    return new


def discard_staged_job(db: Session, job: IngestionJob, *, now: Optional[datetime] = None) -> IngestionJob:
    """v2.355.0 — the operator's way out of a staged job they will not
    start: the file goes, the row becomes a dismissed failure ("Discarded
    before import") so it leaves the queue but stays in Ingestion Results as
    history.  Only a ``staged`` job can be discarded; anything queued or
    later has its own cancel / dismiss."""
    from app.services.ingestion_service import _transitions
    from app.services.job_transitions import JobNotTransitionable

    now = now or datetime.now(timezone.utc)
    # The status is checked under the row lock and the file removed while it
    # is still held.  A start racing this discard waits on the lock and then
    # finds the job no longer staged; removed after the commit instead, the
    # file of a job just re-started from ``failed`` could vanish under it.
    try:
        discarded = _transitions.cancel(
            db, job.id, allowed_from=(STAGED_STATUS,), to_status="failed",
            error_message=DISCARDED_MESSAGE,
            message=DISCARDED_MESSAGE,
            dismissed_at=now,
        )
    except JobNotTransitionable as exc:
        db.rollback()
        raise ValueError(f"Only a staged job can be discarded (current status: {exc.status!r})") from exc
    if discarded is None:
        db.rollback()
        raise ValueError("Only a staged job can be discarded (the job no longer exists)")
    discarded.completed_at = now
    _remove_file_of_locked_job(discarded, now)
    db.commit()
    db.refresh(discarded)
    return discarded


def expire_staged_jobs(db: Session, *, max_age: timedelta = STAGED_MAX_AGE, now: Optional[datetime] = None) -> int:
    """Fail staged jobs nobody started within ``max_age`` and remove their
    files.  Returns how many were expired.  Called from the worker sweep."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - max_age
    # v2.368.0 — FOR UPDATE SKIP LOCKED: a job an operator is starting right now
    # holds its row lock and is simply not expired this sweep (Postgres
    # re-checks ``status = 'staged'`` once it has the lock, so a job that was
    # started a moment ago is not matched either). Unlocked, the sweep could
    # fail a job — and delete its file — between the start's check and write.
    stale = (
        db.query(IngestionJob)
        .filter(IngestionJob.status == STAGED_STATUS, IngestionJob.created_at < cutoff)
        .with_for_update(skip_locked=True)
        .all()
    )
    for job in stale:
        job.status = "failed"
        msg = f"{EXPIRED_MESSAGE_PREFIX}: not started within {int(max_age.total_seconds() // 3600)} hours."
        job.error_message = msg
        job.message = msg
        job.completed_at = now
        # Under the row lock, like every removal of a job's file: after the
        # commit the job is ``failed`` and startable again, and a start could
        # pass its file check just before the file went.
        _remove_file_of_locked_job(job, now)
    if stale:
        db.commit()
    return len(stale)
