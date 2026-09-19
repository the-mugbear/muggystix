"""Staged import (v2.352.0; phase B of the staged-import plan).

An upload can be *staged* instead of queued: the file lands on disk and a job
row exists, but no worker touches it until the operator starts it.  In
between, this service answers "what is this file?" honestly and without
touching the inventory:

* **candidates** — the parsers the dispatcher would try, in its order, each
  with its *basis*: ``structure`` when the file's content alone selects it,
  ``filename`` when only the name does.  No detector was rewritten for this;
  the dispatcher is run twice, once with a neutral filename and once with
  the real one, and the difference is the basis.
* **preview** — the first two kilobytes of the file as text, plus a small
  *sample* of interpreted records where an extractor exists (nmap XML, JSON
  tools, CSV, plain text).  The sample is what the reader understood, e.g.
  "10.0.0.5: 3 open ports (22, 80, 443)", not what the real parser would
  write — the real parsers commit as they go and cannot be dry-run.
* **needs_choice** — the operator must pick a format when nothing was
  recognised, when the only basis is the filename, or when several
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
_SAMPLE_BYTES = 64 * 1024
_RAW_PREVIEW_BYTES = 2048
_TEXT_EXTENSIONS = {".json", ".jsonl", ".ndjson", ".csv", ".txt"}
_JSON_KEYS = ("ip", "host", "hostname", "name", "url", "target", "input", "port", "ports", "addresses", "a")


def _read_sample(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read(_SAMPLE_BYTES)


def _attempt_types(service, filename: str, sample: bytes) -> List[str]:
    shadow = SimpleNamespace(original_filename=filename, options={}, format_override=None)
    return [ft for ft, _cls, _desc in service._build_parsing_attempts(shadow, sample)]


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

    real = _attempt_types(service, name, sample)
    neutral = set(_attempt_types(service, f"upload{ext}", sample))

    candidates = []
    for rank, ft in enumerate(real):
        candidates.append({
            "file_type": ft,
            "label": format_label(ft),
            "basis": "structure" if ft in neutral else "filename",
            "rank": rank,
        })
    structural = [c for c in candidates if c["basis"] == "structure"]
    primary = candidates[0] if candidates else None
    if primary is None:
        reason = "No distinctive signature was recognised."
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
    job.format_override = format_override
    job.source_tool = (source_tool or "").strip()[:64] or None
    job.status = "queued"
    job.error_message = None
    job.last_error = None
    job.parse_error_id = None
    job.started_at = None
    job.completed_at = None
    job.message = "Queued by the operator" + (f" as {format_label(format_override)}" if format_override else "")
    db.commit()
    db.refresh(job)
    return job


def retention_window() -> timedelta:
    from app.core.config import settings
    return timedelta(days=max(int(getattr(settings, "INGESTION_RETAIN_FILES_DAYS", 7)), 0))


def file_retained(job: IngestionJob) -> bool:
    return bool(job.storage_path) and Path(job.storage_path).exists()


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
    removed = 0
    finished = (
        db.query(IngestionJob)
        .filter(IngestionJob.status.in_(("completed", "failed")))
        .filter(
            (IngestionJob.completed_at < cutoff)
            | ((IngestionJob.completed_at.is_(None)) & (IngestionJob.created_at < cutoff))
        )
        .all()
    )
    for job in finished:
        if not file_retained(job):
            continue
        shutil.rmtree(Path(job.storage_path).parent, ignore_errors=True)
        if not Path(job.storage_path).exists():
            removed += 1
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
    if not file_retained(job):
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
        recon_session_id=job.recon_session_id,
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


def expire_staged_jobs(db: Session, *, max_age: timedelta = STAGED_MAX_AGE, now: Optional[datetime] = None) -> int:
    """Fail staged jobs nobody started within ``max_age`` and remove their
    files.  Returns how many were expired.  Called from the worker sweep."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - max_age
    stale = (
        db.query(IngestionJob)
        .filter(IngestionJob.status == STAGED_STATUS, IngestionJob.created_at < cutoff)
        .all()
    )
    for job in stale:
        job.status = "failed"
        msg = f"Staged upload expired: not started within {int(max_age.total_seconds() // 3600)} hours."
        job.error_message = msg
        job.message = msg
        job.completed_at = now
        try:
            shutil.rmtree(Path(job.storage_path).parent, ignore_errors=True)
        except Exception:  # pragma: no cover
            logger.debug("could not remove staged dir for job %s", job.id, exc_info=True)
    if stale:
        db.commit()
    return len(stale)
