"""Ingestion pipeline for handling large scan uploads.

The ingestion service streams uploads to disk and registers job metadata
in PostgreSQL.  A separate worker process (``python -m app.worker``) polls
the ``ingestion_jobs`` table using ``SELECT … FOR UPDATE SKIP LOCKED`` and
processes one job at a time, keeping parsing fully isolated from the API.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import os
import re
import shutil
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Type
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.parsers import content_detection as _cd  # v2.27.0 — content sniffers extracted
from app.db.models import IngestionJob
# Imported as a module (not `from … import SessionLocal`) so tests can rebind
# app.db.session.SessionLocal onto the test engine and have these background
# sessions land in the test DB — same pattern as agent_api_log_service.
from app.db import session as _session_module
from app.services.parse_error_service import log_parse_error
from app.services.job_transitions import JobNotTransitionable, JobTransitions

logger = logging.getLogger(__name__)


class DuplicateUploadError(Exception):
    """This exact file (same SHA-256) is already in the project — as a scan,
    or as a job still queued/processing. Re-ingesting it would only add a
    second, indistinguishable scan and move every scan counter while adding
    no data. Not a ValueError: callers map it to 409, not 400."""

    def __init__(self, *, scan_id: Optional[int] = None, job_id: Optional[int] = None,
                 filename: Optional[str] = None, job_status: Optional[str] = None):
        self.scan_id = scan_id
        self.job_id = job_id
        self.filename = filename
        self.job_status = job_status
        name = f" ({filename})" if filename else ""
        if scan_id is not None:
            text_ = f"This exact file is already imported as scan #{scan_id}{name}"
        elif job_status == "staged":
            # v2.368.0 — a staged copy counts: two identical files waiting for
            # their format review would otherwise both import.
            text_ = f"This exact file is already uploaded as ingestion job #{job_id}{name}, waiting for its format review"
        else:
            text_ = f"This exact file is already imported as ingestion job #{job_id}, still processing{name}"
        super().__init__(f"{text_}; uploading it again would add nothing.")

    def detail(self) -> Dict[str, object]:
        return {
            "code": "duplicate_scan",
            "message": str(self),
            "scan_id": self.scan_id,
            "job_id": self.job_id,
            # v2.385.0 — "staged" lets the upload dialog offer the waiting
            # copy's format review instead of a dead end.
            "job_status": self.job_status,
        }


# A job in one of these states makes an identical upload a duplicate.
DUPLICATE_BLOCKING_STATUSES = ("staged", "queued", "processing")


def carry_upload_identity(db: Session, job: IngestionJob) -> None:
    """Stamp a completed job's upload identity onto its scan: who sent it,
    the batch it arrived in, and the file's SHA-256 (which the upload-time
    duplicate check reads scans by)."""
    if not job.scan_id:
        return
    from app.db.models import Scan
    scan = db.get(Scan, job.scan_id)
    if scan is None:
        return
    if job.submitted_by_id and not scan.uploaded_by_id:
        scan.uploaded_by_id = job.submitted_by_id
    scan.batch_id = job.batch_id
    scan.content_sha256 = job.content_sha256

# v2.328.0 — every lifecycle write (claim / heartbeat / complete / fail /
# cancel / retry / reap) goes through the shared transition layer so the
# ingestion and report queues cannot drift on fencing or locking again.
_transitions = JobTransitions(IngestionJob, name="ingestion")

# Canonical upload allowlist — the single source of truth for which file
# extensions ingestion accepts.  Enforced inside ``create_job`` so EVERY
# caller passes through it (the JWT /upload path AND the agent recon upload,
# which reaches create_job directly).  ``upload.py`` re-exports this for its
# early, pre-disk 400.
ALLOWED_UPLOAD_EXTENSIONS = frozenset(
    # .ndjson is newline-delimited JSON, identical to .jsonl — the bundled
    # scripts/rdap-lookup.py writes .ndjson, so it must be accepted.
    {".xml", ".json", ".jsonl", ".ndjson", ".csv", ".txt", ".gnmap", ".nessus", ".zip"}
)

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
    svc.update_heartbeat(
        db, job_id, progress, claimed_at=getattr(_active_job, "claimed_at", None),
    )


def note_scan_created(db: Session, scan_id: Optional[int]) -> None:
    """Called by a parser right after it flushed its Scan row (review
    2026-10-01 R1).

    Under an active job this stamps ``ingestion_jobs.in_progress_scan_id`` and
    COMMITS — the job's pointer and the scan row become durable in the same
    transaction, so there is no moment at which a committed scan exists that
    its job does not name.  A worker killed at any later point leaves a row
    the next claim (or the reaper) can clean up from; before this the id lived
    only in the parser object.  Committed at once rather than left for the
    first heartbeat: the UPDATE holds the job's row lock, and an operator's
    cancel (``FOR UPDATE`` on that row) would wait on it until then.

    No active job (a parser driven directly, e.g. tests): does nothing, and
    the caller's transaction is left as it was.  Never call it inside a
    record savepoint — the commit would end it.
    """
    job_id = getattr(_active_job, "job_id", None)
    if scan_id is None or job_id is None or getattr(_active_job, "db", None) is not db:
        return
    _active_job.scan_id = scan_id
    written = _transitions.stamp(
        db, job_id, getattr(_active_job, "claimed_at", None), in_progress_scan_id=scan_id,
    )
    if written == 0:
        # Not this attempt's job any more (cancelled, or reaped and re-claimed):
        # nothing of this parse may be committed.  The caller's rollback
        # discards the scan with everything else.
        raise ParseFailure(
            "Job no longer owned by this attempt",
            user_message="Cancelled or superseded before the import started",
        )
    db.commit()


def note_ports_created(db: Session, port_ids: Sequence[int]) -> None:
    """Called by an import path that writes NO ``PortScanHistory`` (Nessus)
    just before each batch commit, with every port id the attempt has created
    so far (review 2026-10-01 R2).

    Under an active job this stamps ``ingestion_jobs.in_progress_created_port_ids``
    in the CALLER'S transaction and does not commit: the list and the ports it
    names become durable together, in the commit the next heartbeat performs,
    so a hard kill between commits cannot leave a committed port the job does
    not name.  ``delete_partial_scan`` reads it; nothing else does.

    No active job (a service driven directly): does nothing.
    """
    job_id = getattr(_active_job, "job_id", None)
    if job_id is None or getattr(_active_job, "db", None) is not db:
        return
    ids = [int(i) for i in port_ids]
    written = _transitions.stamp(
        db, job_id, getattr(_active_job, "claimed_at", None),
        in_progress_created_port_ids=ids or None,
    )
    if written == 0:
        raise ParseFailure(
            "Job no longer owned by this attempt",
            user_message="Cancelled or superseded before the import finished",
        )


# ---------------------------------------------------------------------------
# Imports and the partial-scan cleanup, per project (review 2026-10-01 S2)
#
# ``delete_partial_scan`` decides with NOT EXISTS ("no other scan saw this
# host") and then deletes.  Under READ COMMITTED a second worker's import that
# is re-observing that host in an open transaction is invisible to the check,
# and the DELETE that then waits on its row lock is not re-evaluated: the
# cleanup deleted a host the other import had just observed.
#
# One advisory lock per project, in two modes:
#
# * every transaction of an import takes it SHARED when it begins
#   (``project_import_lock``) — imports never wait on each other;
# * the cleanup takes it EXCLUSIVE before its first statement, so it starts
#   only when no import of the project has a batch open, sees everything they
#   committed, and holds new batches back for the few statements it runs.
#
# Transaction-level (released by commit / rollback), so it is held per batch,
# never for a whole import.  The two-integer key space is not used anywhere
# else (the upload duplicate guard uses the single-bigint form).
_PROJECT_IMPORT_LOCK_CLASS = 0x42534950  # "BSIP"

_import_lock = threading.local()


def _on_postgres(db: Any) -> bool:
    if not isinstance(db, Session):
        return False
    try:
        bind = db.get_bind()
    except Exception:  # noqa: BLE001 — an unbound session takes no lock
        return False
    return bind is not None and bind.dialect.name == "postgresql"


def _real_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@contextmanager
def project_import_lock(db: Session, project_id: Optional[int]) -> Iterator[None]:
    """While inside, every transaction ``db`` begins takes the project's
    import lock in SHARED mode (see the block comment above).  Savepoints do
    not ask again; nothing happens off PostgreSQL or without a project."""
    if not _real_id(project_id) or not _on_postgres(db):
        yield
        return
    stmt = text("SELECT pg_advisory_xact_lock_shared(:cls, :project)")
    params = {"cls": _PROJECT_IMPORT_LOCK_CLASS, "project": project_id}

    def _take(session, transaction, connection):
        # Paused while THIS session runs the cleanup (it takes the exclusive
        # mode instead; two cleanups each holding the shared one would
        # deadlock on the upgrade).
        if transaction.nested or getattr(_import_lock, "paused", False):
            return
        connection.execute(stmt, params)

    event.listen(db, "after_begin", _take)
    try:
        if db.in_transaction():
            db.execute(stmt, params)
        yield
    finally:
        event.remove(db, "after_begin", _take)


def lock_project_for_cleanup(
    db: Session, project_id: Optional[int], *, lock_timeout_s: Optional[int] = None,
) -> None:
    """Take the project's import lock in EXCLUSIVE mode for the rest of the
    caller's transaction.  ``lock_timeout_s`` bounds the wait (an API request
    must not hang behind a long batch): the statement then fails, and the
    caller's cleanup is retried later."""
    if not _real_id(project_id) or not _on_postgres(db):
        return
    if lock_timeout_s:
        db.execute(text(f"SET LOCAL lock_timeout = '{int(lock_timeout_s)}s'"))
    db.execute(
        text("SELECT pg_advisory_xact_lock(:cls, :project)"),
        {"cls": _PROJECT_IMPORT_LOCK_CLASS, "project": project_id},
    )
    if lock_timeout_s:
        db.execute(text("SET LOCAL lock_timeout TO DEFAULT"))


def _project_of_scan(db: Session, scan_id: int) -> Optional[int]:
    """The project a partial scan belongs to.  NetExec and gnmap create their
    scan with no project until the import succeeds, so the job that names the
    scan — and, failing that, a host it observed — answers too."""
    from sqlalchemy import select

    from app.db import models

    HH = models.HostScanHistory
    for stmt in (
        select(models.Scan.project_id).where(models.Scan.id == scan_id),
        select(IngestionJob.project_id).where(IngestionJob.in_progress_scan_id == scan_id),
        select(models.Host.project_id).join(HH, HH.host_id == models.Host.id).where(HH.scan_id == scan_id),
    ):
        project_id = db.execute(stmt.limit(1)).scalar()
        if _real_id(project_id):
            return project_id
    return None


def _seen_by_another_scan(vuln, scan_id: Optional[int]):
    """A scanner observation that is not the attempt's alone: another scan (or
    no scan — a backfill, a deleted first recorder) first recorded it, or a
    later scan has re-observed it (``last_seen_scan_id``; ``scan_id`` never
    moves).  With no scan id (the attempt's scan row is already gone) every
    observation counts."""
    from sqlalchemy import and_, or_, true

    if scan_id is None:
        return true()
    return or_(
        vuln.scan_id.is_distinct_from(scan_id),
        and_(vuln.last_seen_scan_id.isnot(None), vuln.last_seen_scan_id != scan_id),
    )


def _port_is_only_this_attempts(port_id, scan_id: Optional[int]) -> List[Any]:
    """The guards under which a port an attempt created may be deleted: no
    OTHER scan saw it and nothing else refers to it.  One list for the ports
    found through port history and for the ones a job row remembers.

    "Saw it" is more than port history (S1): Nessus writes none, so a later
    scan's re-observation shows only as ``ports_v2.last_updated_scan_id`` and
    as ``vulnerabilities.last_seen_scan_id`` on a row the attempt first
    recorded.  Both protect the port.

    ``scan_id`` None (the attempt's scan row is already gone): any history,
    observation, script or web row at all protects the port, and so does a
    scan named as its last updater."""
    from sqlalchemy import exists, true
    from sqlalchemy.orm import aliased

    from app.db import models
    from app.db.models_findings import FindingHost
    from app.db.models_vulnerability import Vulnerability

    other_ph = aliased(models.PortScanHistory)
    this_port = aliased(models.Port)

    def another_scan(column):
        return column.is_distinct_from(scan_id) if scan_id is not None else true()

    # A scan NAMED as the last updater that is not this one.  NULL does not
    # protect: it means the scan that last updated the port was deleted.
    updated_by_another = (
        this_port.last_updated_scan_id != scan_id if scan_id is not None
        else this_port.last_updated_scan_id.isnot(None)
    )

    return [
        ~exists().where(other_ph.port_id == port_id, another_scan(other_ph.scan_id)),
        ~exists().where(this_port.id == port_id, updated_by_another),
        ~exists().where(models.Annotation.port_id == port_id),
        ~exists().where(FindingHost.port_id == port_id),
        ~exists().where(Vulnerability.port_id == port_id, _seen_by_another_scan(Vulnerability, scan_id)),
        ~exists().where(models.Script.port_id == port_id, another_scan(models.Script.scan_id)),
        ~exists().where(models.WebInterface.port_id == port_id, another_scan(models.WebInterface.scan_id)),
        ~exists().where(models.WebPath.port_id == port_id, another_scan(models.WebPath.scan_id)),
    ]


_PORT_ID_CHUNK = 5000

# How long a cleanup started from an API request (a cancel) waits for the
# project's import lock before giving up; the reaper's sweep finishes it.
_API_CLEANUP_LOCK_TIMEOUT_S = 15
# How long the reaper's sweep leaves a failed job whose cleanup failed.
_LEFTOVER_RETRY_SECONDS = 600


def delete_recorded_ports(db: Session, port_ids: Sequence[Any], scan_id: Optional[int]) -> int:
    """Delete the ports in ``port_ids`` (a job row's record of what its
    attempt created) that are still only that attempt's.  A port another scan
    has since observed, or that anything refers to, stays.  Ids that no longer
    exist (their host was deleted, a rolled-back record) match nothing."""
    from sqlalchemy import delete

    from app.db import models

    ids = sorted({i for i in port_ids if isinstance(i, int) and not isinstance(i, bool)})
    removed = 0
    for start in range(0, len(ids), _PORT_ID_CHUNK):
        removed += db.execute(
            delete(models.Port)
            .where(models.Port.id.in_(ids[start:start + _PORT_ID_CHUNK]))
            .where(*_port_is_only_this_attempts(models.Port.id, scan_id))
            .execution_options(synchronize_session=False)
        ).rowcount
    return removed


def _delete_job_recorded_ports(db: Session, scan_id: int) -> int:
    """Delete the ports recorded on the job(s) whose in-progress scan is
    ``scan_id`` and clear the record.  Keyed on the SCAN, not on a job id: a
    stale attempt cleaning up after itself must never read the list a newer
    attempt of the same job is writing (that one names a different scan)."""
    from sqlalchemy import select, update

    rows = db.execute(
        select(IngestionJob.id, IngestionJob.in_progress_created_port_ids)
        .where(IngestionJob.in_progress_scan_id == scan_id)
    ).all()
    removed = 0
    for job_id, port_ids in rows:
        if not isinstance(port_ids, list):
            continue
        removed += delete_recorded_ports(db, port_ids, scan_id)
        db.execute(
            update(IngestionJob).where(IngestionJob.id == job_id)
            .values(in_progress_created_port_ids=None)
            .execution_options(synchronize_session=False)
        )
    return removed


def _rehome_rows_a_later_scan_saw(db: Session, scan_id: int) -> int:
    """Move the rows ``scan_id`` first recorded that ANOTHER scan has since
    seen onto that scan, so deleting ``scan_id`` neither deletes them nor
    leaves them naming a scan that is gone (review 2026-10-01 S1).

    A re-observation never moves a row's ``scan_id`` ("first recorded by"),
    so a partial scan that is cleaned up late — its worker was killed, another
    import of the same hosts completed, and only then was the job reaped or
    re-claimed — still owned rows the completed import had reported.  Per
    table:

    * ``vulnerabilities`` (``scan_id`` SET NULL): the re-observation is
      recorded (``last_seen_scan_id``), so the row moves to exactly the scan
      that last saw it.
    * ``scripts_v2`` / ``host_scripts_v2`` / ``host_attributes`` (``scan_id``
      CASCADE, NOT NULL, and no "last seen by" column): the row moves to the
      newest OTHER scan that observed its port / host, when that scan is
      later than this one (a higher id) or the row itself was touched again
      after it was written (``last_seen`` past ``first_seen``).  A row on a
      port only EARLIER scans saw, never touched again, is this attempt's
      alone and cascades with the scan.
    * ``host_confidence`` / ``port_confidence`` (CASCADE; ``scan_id`` moves
      only when a scan's observation wins): to the newest later scan that
      observed the host / port.

    ``web_interfaces``, ``web_paths``, ``netexec_results``, ``scan_info`` and
    the two history tables carry the scan in their identity — a later scan
    writes its own rows — so there is nothing of theirs to move.

    Then the introduction itself: a host or port this scan CREATED and another
    scan has seen is now introduced by the first of those scans
    (``host_created`` / ``port_created``), so the Scans page does not count it
    as "already known" to every scan left.
    """
    from sqlalchemy import exists, func, or_, select, update
    from sqlalchemy.orm import aliased

    from app.db import models
    from app.db.models_confidence import HostConfidence, PortConfidence
    from app.db.models_vulnerability import HostAttribute, Vulnerability

    HH, PH = models.HostScanHistory, models.PortScanHistory
    moved = db.execute(
        update(Vulnerability)
        .where(
            Vulnerability.scan_id == scan_id,
            Vulnerability.last_seen_scan_id.isnot(None),
            Vulnerability.last_seen_scan_id != scan_id,
        )
        .values(scan_id=Vulnerability.last_seen_scan_id)
        .execution_options(synchronize_session=False)
    ).rowcount

    def _to_the_scan_that_saw_its_parent(model, parent, history, history_parent, touched_again=None):
        other = aliased(history)
        seen = [history_parent(other) == parent, other.scan_id != scan_id]
        later = other.scan_id > scan_id
        if touched_again is not None:
            later = or_(later, touched_again)
        target = select(func.max(other.scan_id)).where(*seen, later).correlate(model).scalar_subquery()
        return db.execute(
            update(model)
            .where(model.scan_id == scan_id, exists().where(*seen, later))
            .values(scan_id=target)
            .execution_options(synchronize_session=False)
        ).rowcount

    by_port, by_host = (lambda h: h.port_id), (lambda h: h.host_id)
    moved += _to_the_scan_that_saw_its_parent(
        models.Script, models.Script.port_id, PH, by_port,
        models.Script.last_seen > models.Script.first_seen,
    )
    moved += _to_the_scan_that_saw_its_parent(
        models.HostScript, models.HostScript.host_id, HH, by_host,
        models.HostScript.last_seen > models.HostScript.first_seen,
    )
    # first_seen / last_seen are two Python clock reads at insert, a few
    # microseconds apart: "touched again" needs a real gap.
    moved += _to_the_scan_that_saw_its_parent(
        HostAttribute, HostAttribute.host_id, HH, by_host,
        HostAttribute.last_seen > HostAttribute.first_seen + timedelta(seconds=1),
    )
    moved += _to_the_scan_that_saw_its_parent(HostConfidence, HostConfidence.host_id, HH, by_host)
    moved += _to_the_scan_that_saw_its_parent(PortConfidence, PortConfidence.port_id, PH, by_port)

    for history, parent, flag in ((HH, HH.host_id, HH.host_created), (PH, PH.port_id, PH.port_created)):
        mine, other = aliased(history), aliased(history)
        parent_name, flag_name = parent.key, flag.key
        heirs = (
            select(func.min(other.id))
            .join(mine, getattr(mine, parent_name) == getattr(other, parent_name))
            .where(mine.scan_id == scan_id, getattr(mine, flag_name).is_(True), other.scan_id != scan_id)
            .group_by(getattr(other, parent_name))
        )
        db.execute(
            update(history).where(history.id.in_(heirs)).values({flag_name: True})
            .execution_options(synchronize_session=False)
        )
    return moved


def _host_is_only_this_attempts(host_id, scan_id: int) -> List[Any]:
    """The guards under which a host an attempt created may be deleted: no
    other scan saw it, nobody has worked on it, and nothing else has attached
    to it.  When in doubt the host stays.

    History is the usual witness, but not the only one (S1): a writer that
    attaches without a history row — a re-observed scanner row, a port another
    scan updated, a web row, an operator's correction of the name — protects
    the host as well."""
    from sqlalchemy import exists
    from sqlalchemy.orm import aliased

    from app.db import models
    from app.db.models_confidence import NetexecResult
    from app.db.models_findings import FindingHost
    from app.db.models_host_tests import HostTest
    from app.db.models_proposals import AgentProposal, EvidenceRecord
    from app.db.models_remediation import RemediationEvent
    from app.db.models_vulnerability import HostAttribute, Vulnerability

    other_hh = aliased(models.HostScanHistory)
    this_host = aliased(models.Host)
    host_port = aliased(models.Port)
    port_ph = aliased(models.PortScanHistory)
    host_vuln = aliased(Vulnerability)
    proposed_vuln = aliased(Vulnerability)

    def _no(column):
        return ~exists().where(column == host_id)

    def _none_from_another_scan(model):
        return ~exists().where(model.host_id == host_id, model.scan_id != scan_id)

    return [
        ~exists().where(other_hh.host_id == host_id, other_hh.scan_id != scan_id),
        # Work: never deleted to tidy an import.
        _no(models.Annotation.host_id),
        _no(models.HostFollow.host_id),
        _no(models.HostTagAssignment.host_id),
        _no(FindingHost.host_id),
        _no(HostTest.host_id),
        _no(EvidenceRecord.host_id),
        # A remediation note needs only a host, not a finding on it.
        _no(RemediationEvent.host_id),
        ~exists().where(
            proposed_vuln.host_id == host_id,
            AgentProposal.vulnerability_id == proposed_vuln.id,
        ),
        # A scan named as its last updater that is not this one (the web
        # parsers leave the pointer NULL on a host they create, so NULL does
        # not protect), or a name an operator typed.
        ~exists().where(
            this_host.id == host_id,
            (this_host.last_updated_scan_id != scan_id) | (this_host.hostname_source == "operator"),
        ),
        # Something from another scan or source hangs on it.
        ~exists().where(host_vuln.host_id == host_id, _seen_by_another_scan(host_vuln, scan_id)),
        ~exists().where(host_port.host_id == host_id, host_port.last_updated_scan_id != scan_id),
        ~exists().where(
            host_port.host_id == host_id, port_ph.port_id == host_port.id, port_ph.scan_id != scan_id,
        ),
        _none_from_another_scan(models.HostScript),
        _none_from_another_scan(HostAttribute),
        _none_from_another_scan(models.WebInterface),
        _none_from_another_scan(models.WebPath),
        _none_from_another_scan(NetexecResult),
    ]


def scan_file_paths(scan_id: int) -> List[Path]:
    """What a scan keeps on disk outside the database: the EyeWitness bundle's
    screenshots (``<uploads>/web_screenshots/<scan id>/``) and the report it
    was extracted next to (``report-<scan id>.json`` / ``.csv``)."""
    root = Path(settings.UPLOAD_DIR) / "web_screenshots"
    return [root / str(int(scan_id)), root / f"report-{int(scan_id)}.json", root / f"report-{int(scan_id)}.csv"]


def remove_scan_files(scan_id: int) -> int:
    """Remove a deleted partial scan's files (review 2026-10-01 M4).  Call it
    AFTER the delete has committed.  Never follows a link and never leaves the
    screenshots root: the name is built from the integer id, and anything that
    resolves elsewhere is left alone.  Returns how many entries were removed;
    a failure is logged, not raised."""
    if not _real_id(scan_id):
        return 0
    root = (Path(settings.UPLOAD_DIR) / "web_screenshots").resolve()
    removed = 0
    for path in scan_file_paths(scan_id):
        try:
            if path.is_symlink() or path.resolve().parent != root:
                continue
            if path.is_dir():
                shutil.rmtree(path)
                removed += 1
            elif path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            logger.warning("Could not remove %s of deleted partial scan %s", path, scan_id, exc_info=True)
    return removed


def delete_partial_scan(
    db: Session, scan_id: int, *, lock_timeout_s: Optional[int] = None,
) -> Dict[str, int]:
    """Delete the scan an import attempt left unfinished, and what ONLY that
    attempt created (review 2026-10-01 R2).  The one implementation, used by
    the in-process cleanup (cancel, timeout, shutdown hand-back, parser
    failure) and by the cleanup of a dead attempt's scan (re-claim, reap).

    Deleting the scan alone left every host and port the attempt introduced:
    ``last_updated_scan_id`` is SET NULL, so they stayed in the inventory with
    no scan behind them and the retry then reported "0 new hosts" for hosts
    this very file introduced.  Findings and name observations are SET NULL
    too, so the retry found the attempt's rows "first recorded by" nothing.

    The cleanup of a dead attempt can run long after it died — after another
    import of the same hosts has completed.  So (S1) it deletes only what NO
    other scan has seen, and (S2) it runs alone in its project:

    * first, the project's import lock in exclusive mode
      (``lock_project_for_cleanup``): no import of the project has a batch
      open while the checks below run;
    * rows this scan first recorded that a later scan re-observed are moved
      to that scan (``_rehome_rows_a_later_scan_saw``);

    then removed, in this order:

    * hosts whose history says this scan CREATED them and no other scan saw
      them — unless a person or an agent has worked on the host (a note, a
      follow, a tag, a finding endpoint, a test, an evidence record, a
      proposal about one of its observations) or something else has attached
      to it (``_host_is_only_this_attempts``): work is never deleted to tidy
      an import, and when in doubt the host stays;
    * on the hosts that stay, ports this scan created that no other scan saw
      and nothing else refers to — found through ``port_scan_history``, and,
      for an import that writes none (Nessus), through the ids its job row
      remembers (``in_progress_created_port_ids``); the same guards for both;
    * scanner observations this scan first recorded and no later scan saw,
      unless a finding or a proposal refers to them, and the name
      observations it made;
    * the scan — its history, scripts and web rows cascade.

    Statements only (no rows loaded); the caller commits, then removes the
    scan's files (``remove_scan_files``).  Returns the counts for the log
    line.
    """
    from sqlalchemy import delete, exists, or_, select

    from app.db import models
    from app.db.models_findings import Finding, FindingVulnerability
    from app.db.models_proposals import AgentProposal
    from app.db.models_vulnerability import Vulnerability

    lock_project_for_cleanup(db, _project_of_scan(db, scan_id), lock_timeout_s=lock_timeout_s)
    rehomed = _rehome_rows_a_later_scan_saw(db, scan_id)

    HH, PH = models.HostScanHistory, models.PortScanHistory

    created_hosts = (
        select(HH.host_id)
        .where(HH.scan_id == scan_id, HH.host_created.is_(True))
        .where(*_host_is_only_this_attempts(HH.host_id, scan_id))
    )
    hosts = db.execute(
        delete(models.Host).where(models.Host.id.in_(created_hosts))
        .execution_options(synchronize_session=False)
    ).rowcount

    # Ports of the deleted hosts went with them; these are on hosts that stay.
    created_ports = (
        select(PH.port_id)
        .where(PH.scan_id == scan_id, PH.port_created.is_(True))
        .where(*_port_is_only_this_attempts(PH.port_id, scan_id))
    )
    ports = db.execute(
        delete(models.Port).where(models.Port.id.in_(created_ports))
        .execution_options(synchronize_session=False)
    ).rowcount
    # ...and the ports an attempt that writes no port history (Nessus) said it
    # created, remembered on the job row that names this scan as in progress.
    ports += _delete_job_recorded_ports(db, scan_id)

    # Only what no other scan has seen.  The re-observed ones were moved to
    # the scan that saw them above; the second condition is the same rule
    # stated where the delete happens, so it holds whatever ran before.
    observations = db.execute(
        delete(Vulnerability)
        .where(Vulnerability.scan_id == scan_id)
        .where(or_(
            Vulnerability.last_seen_scan_id.is_(None),
            Vulnerability.last_seen_scan_id == scan_id,
        ))
        .where(~exists().where(Finding.vuln_id == Vulnerability.id))
        .where(~exists().where(FindingVulnerability.vuln_id == Vulnerability.id))
        .where(~exists().where(AgentProposal.vulnerability_id == Vulnerability.id))
        .execution_options(synchronize_session=False)
    ).rowcount
    db.execute(
        delete(models.DNSRecord).where(models.DNSRecord.scan_id == scan_id)
        .execution_options(synchronize_session=False)
    )
    db.execute(
        delete(models.Scan).where(models.Scan.id == scan_id)
        .execution_options(synchronize_session=False)
    )
    return {"hosts": hosts, "ports": ports, "observations": observations, "rehomed": rehomed}


ParserDescriptor = Tuple[str, Type, str]


class FallbackAttempt(tuple):
    """A parser the dispatcher tries WITHOUT having recognised the content —
    the XML branch's "try the others" tail.  Still a plain
    ``(file_type, parser_class, description)`` to every consumer (and to the
    dispatch contract tests); the staged review reads ``fallback`` so an
    attempt nothing recognised is never shown as "recognised by structure"."""
    fallback = True


def _fallback(file_type: str, parser_class: Type, description: str) -> ParserDescriptor:
    return FallbackAttempt((file_type, parser_class, description))


def build_parser_dispatch_map() -> Dict[Type, Type]:
    """Every parser class the dispatcher can construct, keyed by itself.

    This MUST contain every parser class ``_build_parsing_attempts`` can emit
    (except ``NessusIntegrationService``, which the executor dispatches on its
    own path) — a class that is detected but missing here dies at dispatch with
    "Unsupported parser class".  That is exactly how RDAP and testssl broke:
    both were wired into detection but never registered for dispatch.

    Kept as one function so detection and dispatch can be checked against each
    other by ``tests/test_parser_dispatch_contract.py``.  Imports stay lazy
    (this runs at parse time, not import time) so a parser module that fails to
    import degrades to "unavailable" instead of breaking the whole service.
    """
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

    dispatch: Dict[Type, Type] = {
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
    }
    # Optional parsers — same set appended in _build_parsing_attempts' JSON /
    # .gnmap branches.  Keep the two lists in lockstep (the contract test fails
    # the build if they drift).
    for module_path, class_name in (
        ("app.parsers.gnmap_parser", "GnmapParser"),
        ("app.parsers.dirbuster_parser", "DirBusterParser"),
        ("app.parsers.httpx_parser", "HttpxParser"),
        ("app.parsers.dnsx_parser", "DnsxParser"),
        ("app.parsers.whatweb_parser", "WhatwebParser"),
        ("app.parsers.testssl_parser", "TestsslParser"),
        ("app.parsers.rdap_parser", "RdapParser"),
        ("app.parsers.nuclei_parser", "NucleiParser"),
    ):
        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            dispatch[cls] = cls
        except ImportError:
            pass
    return dispatch


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


class ShutdownRequested(ParseFailure):
    """The worker was asked to stop mid-job.  A ParseFailure so every parser
    and the dispatcher stop exactly as for a cancellation (no fallback parser,
    partial scan deleted); ``_run_job`` re-queues the job instead of failing
    it."""


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
            # due to host volume mount ownership).  The name is unique per
            # construction, not just per process: two instances built in one
            # process at the same time (v2.354.1 — concurrent detection
            # requests) raced on a PID-only name, and the loser's unlink hit
            # ENOENT and read as "storage is not writable".
            test_file = self._storage_root / f".write_test_{os.getpid()}_{uuid4().hex}"
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
        # Failed jobs whose leftover cleanup failed: job id -> monotonic time
        # before which the reaper's sweep does not try again.
        self._leftover_retry_at: Dict[int, float] = {}
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
        batch_id: Optional[int] = None,
        allow_duplicate: bool = False,
        stage: bool = False,
    ) -> IngestionJob:
        """Persist an upload to disk and register an ingestion job.

        ``stage`` (v2.352.0): register the job as ``staged`` — on disk, not
        queued.  No worker touches it until ``POST /upload/jobs/{id}/start``;
        the operator reviews its detected format first.

        Raises :class:`DuplicateUploadError` when this exact file (by SHA-256)
        is already a scan in the project or is still queued/processing, unless
        ``allow_duplicate`` — an operator deliberately re-importing, e.g. after
        a parser fix. Nothing is created for a refused duplicate.
        """
        # Allowlist the extension here — not just on the JWT /upload path —
        # so the agent recon upload (which calls create_job directly) can't
        # land an arbitrary-extension file on disk.  ValueError surfaces as a
        # 400 in every caller's handler.
        filename = upload.filename or ""
        if not any(filename.lower().endswith(ext) for ext in ALLOWED_UPLOAD_EXTENSIONS):
            raise ValueError(
                "File type not allowed. Supported types: "
                + ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
            )
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

        # Everything from here to a committed job row is wrapped so a failure
        # can't orphan the upload.  The file (up to MAX_FILE_SIZE) is on disk
        # before the DB row exists, so a transient DB/constraint failure — or a
        # validation reject — would otherwise leave an untracked file with no
        # row to find it by; repeated failures could exhaust the shared storage
        # volume.  On ANY failure: roll back and remove the whole per-job dir,
        # then re-raise the original error.
        try:
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
            file_size, content_sha256 = await self._write_upload(upload, destination)
            # CR5-C2 — owner-only on the scan file too (umask leaves it 0644 by
            # default); belt-and-suspenders with the 0700 job dir above.
            os.chmod(destination, 0o600)

            # Magic-byte sanity check.  The extension tells us which parser
            # will run; the content should actually look like that format.
            # This is a cheap first line of defense against uploaded binaries
            # disguised as .xml/.json/.nessus that could crash the parser or
            # get mis-routed.  We only peek at the first 1 KB.  A reject raises
            # ValueError, caught below to clean up the job dir.
            self._validate_content_matches_extension(destination, raw_name)

            opts = dict(options or {})
            if allow_duplicate:
                # Remembered so a later start of this job (staged → queued, or
                # a retry) does not re-refuse what the operator already chose.
                opts["allow_duplicate"] = True
            if not allow_duplicate:
                duplicate = self._find_duplicate(db, opts.get("project_id"), content_sha256)
                if duplicate is not None:
                    raise duplicate
            job = IngestionJob(
                filename=destination.name,
                original_filename=upload.filename,
                storage_path=str(destination),
                status="staged" if stage else "queued",
                file_size=file_size,
                options=opts,
                submitted_by_id=submitted_by_id,
                project_id=opts.get("project_id"),
                # Stamp the agent attribution in the SAME transaction that
                # makes the row visible as ``queued`` — the worker polls
                # independently of the pg_notify hint, so a later "set the FK,
                # commit again" would leave an unattributed window.
                agent_session_id=opts.get("agent_session_id"),
                batch_id=batch_id,
                content_sha256=content_sha256,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
        except Exception:
            db.rollback()
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        # Log the sanitized filename, not the raw one — prevents log
        # injection via filenames with embedded CR/LF.
        logger.info(
            "Queued ingestion job %s for %s (%d bytes)", job.id, safe_name, file_size
        )
        return job

    @staticmethod
    def _find_duplicate(
        db: Session, project_id: Optional[int], content_sha256: str,
        exclude_job_id: Optional[int] = None,
    ) -> Optional["DuplicateUploadError"]:
        """The scan or in-flight job this exact file already is, if any.

        Checked under a per-(project, digest) transaction lock so two identical
        uploads racing each other can't both pass: the second waits until the
        first has committed its job row, then finds it. Released at the
        caller's commit or rollback. A failed or dismissed job does not count
        (the operator is retrying), nor does a deleted scan.
        """
        if project_id is None:
            return None
        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"upload:{project_id}:{content_sha256}"},
            )
        from app.db.models import Scan
        scan = (
            db.query(Scan.id, Scan.filename)
            .filter(Scan.project_id == project_id, Scan.content_sha256 == content_sha256)
            .order_by(Scan.id)
            .first()
        )
        if scan is not None:
            return DuplicateUploadError(scan_id=scan.id, filename=scan.filename)
        job = (
            db.query(IngestionJob.id, IngestionJob.original_filename, IngestionJob.status)
            .filter(
                IngestionJob.project_id == project_id,
                IngestionJob.content_sha256 == content_sha256,
                # "staged" since v2.368.0: a file waiting for its format
                # review is as much "already here" as one being parsed.
                IngestionJob.status.in_(DUPLICATE_BLOCKING_STATUSES),
                *([IngestionJob.id != exclude_job_id] if exclude_job_id is not None else []),
            )
            .order_by(IngestionJob.id)
            .first()
        )
        if job is not None:
            return DuplicateUploadError(
                job_id=job.id, filename=job.original_filename, job_status=job.status,
            )
        return None

    def enqueue_job(self, job_id: int, db: Optional[Session] = None) -> None:
        """Mark a job as ready for processing.

        The job is already in ``queued`` status from ``create_job``.  The
        separate worker process polls for queued rows and picks them up
        via ``SELECT … FOR UPDATE SKIP LOCKED``.  This method exists to
        keep the upload endpoint interface unchanged and to send a
        ``pg_notify`` hint so the worker wakes up immediately instead of
        waiting for its next poll cycle.

        **Pass the caller's ``db``** (v2.361.1).  Without it this opens a
        SECOND pooled connection while the request still holds its own, so a
        request needs two at once.  The staged-import dialog starts every
        ready file in parallel; with more concurrent starts than the pool has
        connections, each held one and waited for another — a pool deadlock
        that froze the whole API (``/health`` included) for exactly
        ``DB_POOL_TIMEOUT`` seconds, then "succeeded" because the failed
        notify is swallowed below.  On the caller's session the notify rides
        the connection the request already has; NOTIFY is delivered at commit.
        """
        self._cancelled.discard(job_id)
        try:
            if db is not None:
                db.execute(text("SELECT pg_notify('ingestion_jobs', :jid)"), {"jid": str(job_id)})
                db.commit()
                return
            with _session_module.SessionLocal() as own:
                own.execute(text("SELECT pg_notify('ingestion_jobs', :jid)"), {"jid": str(job_id)})
                own.commit()
        except Exception:
            # Notification is a performance hint, not required for correctness
            # (the worker polls regardless) — so swallow.  WARNING, not DEBUG: this
            # never fails on a healthy database, and at DEBUG a pool-exhaustion
            # stall of DB_POOL_TIMEOUT seconds per request left nothing in the
            # logs but slow 200s.
            logger.warning("pg_notify for ingestion job %s failed", job_id, exc_info=True)
            if db is not None:
                # The caller's transaction is aborted by the failed statement;
                # leave their session usable for the response.
                try:
                    db.rollback()
                except Exception:
                    logger.debug("rollback after failed pg_notify also failed", exc_info=True)

    def cancel_job(self, job_id: int) -> bool:
        """Request cancellation of a running job.

        Returns True if the job was in a cancellable state.
        """
        self._cancelled.add(job_id)
        with _session_module.SessionLocal() as db:
            # Locked read-check-write (job_transitions.cancel): a worker's
            # SKIP LOCKED claim cannot land between the status check and
            # the write.  Ingestion has no 'cancelled' state — a cancel is a
            # failure with a reason, and a running parse sees it on its next
            # heartbeat.
            try:
                job = _transitions.cancel(
                    db, job_id,
                    allowed_from=("queued", "processing"),
                    to_status="failed",
                    error_message="Cancelled by user",
                )
            except JobNotTransitionable:
                return False
            if job is None:
                return False
            # R1 — a job cancelled while it WAITED (no claim token) will not
            # be claimed again; if an earlier, dead attempt left a scan on it,
            # nothing else would remove it.  A running parse (token set)
            # deletes its own scan when its next heartbeat sees the cancel —
            # unless its worker is dead (M5): a lease already past the
            # reaper's window has nobody left to see the cancel, so the scan
            # is removed here.  (A worker that dies later, between the cancel
            # and its next heartbeat, is caught by the reaper's sweep of
            # failed jobs — ``_discard_failed_jobs_leftovers``.)
            has_leftover = isinstance(job.in_progress_scan_id, int) or isinstance(
                job.in_progress_created_port_ids, list
            )
            nobody_will_clean = job.started_at is None or self._lease_is_dead(job)
            db.commit()
            if has_leftover and nobody_will_clean:
                # Bounded wait: this runs in an API request.
                self._discard_dead_attempt_scan(db, job_id, lock_timeout_s=_API_CLEANUP_LOCK_TIMEOUT_S)
            return True

    @staticmethod
    def _orphan_cutoff() -> datetime:
        """Leases older than this belong to a worker that is gone."""
        return datetime.now(timezone.utc) - timedelta(
            seconds=settings.INGESTION_JOB_TIMEOUT * settings.INGESTION_ORPHAN_CUTOFF_MULTIPLIER
        )

    def _lease_is_dead(self, job: IngestionJob) -> bool:
        """The claimed attempt has not heartbeated within the reaper's window
        (or never did, and was claimed before it)."""
        last = job.last_heartbeat or job.started_at
        if not isinstance(last, datetime):
            return False
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return last < self._orphan_cutoff()

    def requeue_job(self, job_id: int) -> str:
        """Re-queue a FAILED job whose uploaded file is still on disk.

        Mirrors the orphan reaper's requeue (``reap_orphaned_jobs``): a parse
        that failed for a transient reason (a DB blip, a since-fixed parser
        bug) can be retried without forcing the operator to locate and
        re-upload the original — often multi-GB — scan file, which is still
        on disk because only *successful* parses unlink it.

        Returns ``"requeued"`` on success, or a reason code (``"not_failed"``
        / ``"file_missing"`` / ``"not_found"``) so the endpoint can surface a
        precise error.
        """
        self._cancelled.discard(job_id)
        with _session_module.SessionLocal() as db:
            def _file_present(job) -> Optional[str]:
                return None if (job.storage_path and Path(job.storage_path).exists()) else "file_missing"

            try:
                job = _transitions.retry(
                    db, job_id,
                    allowed_from=("failed",),
                    precondition=_file_present,
                    increment_retry=True,
                )
            except JobNotTransitionable as exc:
                # ``status`` is either the actual state or the precondition's
                # reason code.
                return "file_missing" if exc.status == "file_missing" else "not_failed"
            if job is None:
                return "not_found"
            job.message = f"Re-queued by user (attempt {job.retry_count})."
            retry_count = job.retry_count
            db.commit()
            logger.info("Re-queued failed ingestion job %s (attempt %d)", job_id, retry_count)
            # Wake the worker immediately (same pg_notify hint as the upload
            # path) — on THIS session, not a third connection.
            self.enqueue_job(job_id, db=db)
        return "requeued"

    def update_heartbeat(
        self,
        db: Session,
        job_id: int,
        progress: Optional[str] = None,
        *,
        claimed_at: Optional[datetime] = None,
    ) -> None:
        """Update heartbeat timestamp and optional progress text.

        ``claimed_at`` is the attempt's fencing token (the ``started_at`` the
        claim wrote — see ``poll_and_run_one``).  When given, the write is
        conditioned on ``status = 'processing' AND started_at = :claimed`` so
        a stale attempt — one the orphan reaper re-queued and a peer
        re-claimed — can neither keep the new owner's lease warm nor
        overwrite its progress.  A zero-row update raises ``ParseFailure``:
        this attempt no longer owns the job and must stop writing scan data.

        Raises ``ParseFailure`` if the job has been cancelled or has exceeded
        the configured timeout, giving the active parser a chance to bail out.
        """
        # In-memory check (same process only — e.g. tests or single-process mode)
        if job_id in self._cancelled:
            raise ParseFailure(
                "Job cancelled",
                user_message="Cancelled by user",
            )

        # A long parse keeps the container's liveness file fresh, and a worker
        # asked to stop hands the job back NOW instead of being SIGKILLed
        # mid-parse (review 2026-09-23 R3: the job then sat 'processing' until
        # the reaper's 45-minute window, and its committed partial scan was
        # orphaned when the requeue parsed into a second one).
        from app import worker_loop
        worker_loop.touch_heartbeat()
        if worker_loop.is_shutting_down():
            raise ShutdownRequested(
                "Worker shutting down",
                user_message="Interrupted by a worker restart — re-queued",
            )

        now = datetime.now(timezone.utc)

        written = _transitions.heartbeat(
            db, job_id, claimed_at,
            **({"progress": progress} if progress is not None else {}),
        )
        if written == 0:
            # Not this attempt's job any more.  Decide BEFORE the commit
            # (review 2026-10-01 S3): the commit below is what makes the
            # parser's pending batch durable, and a batch committed by an
            # attempt that no longer owns the job lands after the cleanup that
            # removed its scan — hosts with no scan behind them.  The status
            # is read in this same transaction: a cancel from the API process
            # is a row another connection changed, never the in-memory set.
            from sqlalchemy import select as _select

            status = db.execute(
                _select(IngestionJob.status).where(IngestionJob.id == job_id)
            ).scalar()
            if status == "failed":
                db.rollback()
                raise ParseFailure("Job cancelled", user_message="Cancelled by user")
            if claimed_at is not None:
                db.rollback()
                logger.warning(
                    "Ingestion job %s: stale attempt (claimed %s) — heartbeat skipped, "
                    "pending batch rolled back; another attempt owns the job",
                    job_id, claimed_at,
                )
                raise ParseFailure(
                    "Job re-claimed by another worker",
                    user_message="Superseded by a newer attempt",
                )
        db.commit()

        # Single DB read for both cancellation (cross-process) and timeout
        # checks.  The two columns only: the row also carries the created-port
        # list, megabytes on a large Nessus import, and this runs every batch.
        from sqlalchemy import select as _select

        job = db.execute(
            _select(IngestionJob.status, IngestionJob.started_at).where(IngestionJob.id == job_id)
        ).first()
        if job and job.status == "failed":
            raise ParseFailure(
                "Job cancelled",
                user_message="Cancelled by user",
            )
        if job and job.started_at:
            # Converted, never stripped: a session time zone other than UTC
            # would otherwise shift the elapsed time by its offset.
            started = job.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = (now - started).total_seconds()
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
        elif ext in (".jsonl", ".ndjson"):
            # httpx / rdap (and similar line-delimited formats) — each line is
            # its own JSON object.  First non-blank line must start with ``{``.
            # (.ndjson is the same format under a different name.)
            if first != b"{":
                raise ValueError(
                    f"{ext} expects one JSON object per line, starting with '{{'."
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
        # Unknown extensions fall through — create_job enforces
        # ALLOWED_UPLOAD_EXTENSIONS before we get here, so anything reaching
        # this point is one of the known-good types.

    async def _write_upload(self, upload: UploadFile, destination: Path) -> Tuple[int, str]:
        """Stream an upload to disk in chunks, returning (size, SHA-256 hex).

        The digest is computed over the same chunks as they are written, so
        the duplicate check costs no second read of a multi-GB file.

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
        digest = hashlib.sha256()

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
                digest.update(chunk)
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
        return total_written, digest.hexdigest()

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
        cutoff_seconds = settings.INGESTION_JOB_TIMEOUT * settings.INGESTION_ORPHAN_CUTOFF_MULTIPLIER
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=cutoff_seconds)
        max_retries = settings.INGESTION_MAX_RETRIES
        db = _session_module.SessionLocal()
        try:
            def _decide(job):
                # Auto-requeue a transient crash (OOM/restart) so a flaky parse
                # doesn't dead-end the upload — but only while we're under the
                # retry cap AND the stored upload is still on disk (re-running
                # without it just fails again at the FileNotFoundError guard).
                # ``job`` is locked and its retry_count already incremented.
                file_present = bool(job.storage_path) and Path(job.storage_path).exists()
                if job.retry_count <= max_retries and file_present:
                    logger.warning(
                        "Re-queued orphaned ingestion job %s (attempt %d/%d, started_at=%s, last_heartbeat=%s)",
                        job.id, job.retry_count, max_retries, job.started_at, job.last_heartbeat,
                    )
                    return "requeue", {
                        "last_error": (
                            f"Orphaned (no heartbeat >{int(cutoff_seconds)}s) — worker likely "
                            "crashed; automatically re-queued."
                        ),
                        "message": f"Re-queued after orphan detection (attempt {job.retry_count}/{max_retries}).",
                    }
                reason = (
                    f"exceeded {max_retries} auto-retries"
                    if job.retry_count > max_retries
                    else "uploaded file no longer present"
                )
                err = (
                    f"Orphaned — no heartbeat for >{int(cutoff_seconds)}s and {reason}; "
                    "worker likely crashed. Re-upload to retry."
                )
                logger.warning(
                    "Failed orphaned ingestion job %s (%s, retry_count=%d)",
                    job.id, reason, job.retry_count,
                )
                return "fail", {"error_message": err, "message": err, "last_error": err}

            # Each candidate is re-locked (FOR UPDATE SKIP LOCKED) with the
            # stale predicate re-applied, so a worker that heartbeated in the
            # meantime keeps its job — see job_transitions.requeue_or_fail_stale.
            reaped = _transitions.requeue_or_fail_stale(
                db, cutoff=cutoff, max_retries=max_retries, decide=_decide,
            )
            if not reaped.total:
                db.rollback()
                self._discard_failed_jobs_leftovers(db, cutoff)
                return 0
            permanently_failed = len(reaped.failed)
            db.commit()
            # R1 — a job reaped to 'failed' will not be claimed again, so the
            # dead attempt's partial scan is removed here.  A re-queued one is
            # cleaned when it is claimed (_run_job), by the worker that then
            # owns it.
            for _failed_id in reaped.failed:
                self._discard_dead_attempt_scan(db, _failed_id)
            self._discard_failed_jobs_leftovers(db, cutoff, skip=reaped.failed)
            # Alert admins about jobs the reaper could NOT recover (over retry
            # cap / file gone). Routine crash-requeues stay quiet; only the
            # actionable permanent failures notify. Best-effort — never let a
            # notification error roll back the reap that just succeeded.
            if permanently_failed:
                try:
                    from app.services.notification_service import NotificationService
                    NotificationService(db).notify_queue_unhealthy("Ingestion", permanently_failed)
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.warning("Failed to emit queue-health alert", exc_info=True)
            return reaped.total
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
        db = _session_module.SessionLocal()
        try:
            # Lock + flip to processing in one transaction (job_transitions
            # .claim_oldest_queued).  The returned claimed_at is this
            # attempt's fencing token: rewritten on every claim, nulled by
            # the reaper on requeue.  Heartbeat, completion and failure
            # writes all condition on it, so an attempt whose lease was
            # reaped and re-claimed by a peer cannot renew the new owner's
            # heartbeat, publish over its result, or fail it.
            claimed = _transitions.claim_oldest_queued(db, message="Processing queued file")
            if claimed is None:
                return False
            job_id, claimed_at = claimed
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        # Now process outside the row-lock transaction.
        self._run_job(job_id, claimed_at=claimed_at)
        return True

    def _discard_partial_scan(
        self, db: Session, scan_id: int, *, why: str, lock_timeout_s: Optional[int] = None,
    ) -> bool:
        """Roll back whatever the failed attempt left pending, then delete its
        committed partial scan and what only it created (``delete_partial_scan``),
        and — once that is committed — the files it kept on disk.

        Best-effort: a cleanup that fails is logged and must not replace the
        error that got us here — the scan stays named on the job row, so the
        next claim (or the reaper's sweep of failed jobs) tries again.
        Returns whether the scan is gone; a caller about to parse into a NEW
        scan must not go on when it is not (M1)."""
        # This session stops asking for the import lock's shared mode while
        # it cleans up: the cleanup takes the exclusive one.
        was_paused = getattr(_import_lock, "paused", False)
        _import_lock.paused = True
        try:
            db.rollback()
            removed = delete_partial_scan(db, scan_id, lock_timeout_s=lock_timeout_s)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.warning("Could not delete partial scan %s (%s)", scan_id, why, exc_info=True)
            return False
        finally:
            _import_lock.paused = was_paused
        files = remove_scan_files(scan_id)
        logger.info(
            "Deleted partial scan %s (%s): %d host(s), %d port(s), %d scanner observation(s) "
            "that only it had created; %d row(s) a later scan had re-observed were kept and "
            "moved to that scan; %d file entr%s removed",
            scan_id, why, removed["hosts"], removed["ports"], removed["observations"],
            removed.get("rehomed", 0), files, "y" if files == 1 else "ies",
        )
        return True

    def _discard_attempt_scan(self, db: Session, parser: object) -> Optional[bool]:
        """Delete the scan THIS attempt's parser committed, if it got that
        far.  The id comes from the parser (``_created_scan_id``) or from what
        it announced through ``note_scan_created``.  True when one was
        deleted, False when one exists and could NOT be deleted, None when
        the attempt had not committed a scan."""
        scan_id = getattr(parser, "_created_scan_id", None)
        if not isinstance(scan_id, int):
            scan_id = getattr(_active_job, "scan_id", None)
        _active_job.scan_id = None
        if isinstance(scan_id, int):
            return self._discard_partial_scan(db, scan_id, why="import did not finish")
        return None

    def _discard_dead_attempt_scan(
        self, db: Session, job_id: int, *, lock_timeout_s: Optional[int] = None,
        only_if_failed: bool = False,
    ) -> bool:
        """Delete the scan a previous attempt of ``job_id`` left unfinished
        (review 2026-10-01 R1): the worker died, or was reaped, after the scan
        was committed and before the job finished.  False when something was
        left and could not be removed.

        ``only_if_failed`` is for a caller working from a LIST it read
        earlier (the failed-jobs sweep): the pointer is read together with
        the status, and a job that is no longer ``failed`` — retried and
        claimed by another worker since the list was read — is left alone.
        Its pointer may by now name the new attempt's live scan."""
        job = db.get(IngestionJob, job_id)
        if only_if_failed and (job is None or job.status != "failed"):
            return True
        stale_scan_id = job.in_progress_scan_id if job is not None else None
        if not isinstance(stale_scan_id, int):
            # R2 — the attempt's scan row is already gone (someone deleted the
            # partial scan; the FK nulled the pointer) but the job still
            # remembers ports it created.  Remove the ones nothing at all
            # refers to and drop the record.
            recorded = job.in_progress_created_port_ids if job is not None else None
            if isinstance(recorded, list):
                was_paused = getattr(_import_lock, "paused", False)
                _import_lock.paused = True
                try:
                    project_id = job.project_id
                    db.rollback()
                    lock_project_for_cleanup(db, project_id, lock_timeout_s=lock_timeout_s)
                    removed = delete_recorded_ports(db, recorded, None)
                    db.execute(
                        text("UPDATE ingestion_jobs SET in_progress_created_port_ids = NULL WHERE id = :id"),
                        {"id": job_id},
                    )
                    db.commit()
                    logger.info(
                        "Job %s: removed %d port(s) a dead attempt created (its scan was already gone)",
                        job_id, removed,
                    )
                except Exception:  # noqa: BLE001 — best-effort, like _discard_partial_scan
                    db.rollback()
                    logger.warning("Could not remove a dead attempt's ports for job %s", job_id, exc_info=True)
                    return False
                finally:
                    _import_lock.paused = was_paused
            return True
        if stale_scan_id == job.scan_id:
            # The scan the job FINISHED with — never an attempt's leftover.
            job.in_progress_scan_id = None
            job.in_progress_created_port_ids = None
            db.commit()
            return True
        return self._discard_partial_scan(
            db, stale_scan_id, why=f"left by a dead attempt of job {job_id}",
            lock_timeout_s=lock_timeout_s,
        )

    def _discard_failed_jobs_leftovers(
        self, db: Session, cutoff: datetime, skip: Sequence[int] = (),
    ) -> int:
        """Remove what a FAILED job still names as in progress (M5).

        Nothing claims a failed job again, so a scan left on one stays until
        an operator retries it.  Two ways to get there: a ``processing`` job
        cancelled while its worker was dead (the cancel leaves the cleanup to
        the attempt's next heartbeat, which never comes), and a cleanup that
        itself failed.  Only leases past the reaper's window (``cutoff``) are
        touched — a live attempt that has not yet seen its cancel cleans up
        after itself.  A job whose cleanup fails is tried again in ten
        minutes, not on every poll.  Returns how many were cleaned."""
        from sqlalchemy import or_, select

        try:
            job_ids = db.execute(
                select(IngestionJob.id)
                .where(
                    IngestionJob.status == "failed",
                    or_(
                        IngestionJob.in_progress_scan_id.isnot(None),
                        IngestionJob.in_progress_created_port_ids.isnot(None),
                    ),
                    or_(IngestionJob.last_heartbeat.is_(None), IngestionJob.last_heartbeat < cutoff),
                )
                .order_by(IngestionJob.id)
            ).scalars().all()
            db.rollback()
        except Exception:  # noqa: BLE001 — a sweep, never the reaper's result
            db.rollback()
            logger.warning("Could not list failed ingestion jobs with leftovers", exc_info=True)
            return 0
        cleaned = 0
        now = time.monotonic()
        for job_id in job_ids:
            if job_id in skip or self._leftover_retry_at.get(job_id, 0) > now:
                continue
            if self._discard_dead_attempt_scan(db, job_id, only_if_failed=True):
                self._leftover_retry_at.pop(job_id, None)
                cleaned += 1
            else:
                self._leftover_retry_at[job_id] = now + _LEFTOVER_RETRY_SECONDS
        return cleaned

    def _fail_job_guarded(
        self,
        db: Session,
        job_id: int,
        claimed_at: Optional[datetime],
        *,
        error_message: str,
        message: Optional[str],
        last_error: Optional[str],
        parse_error_id: Optional[str],
    ) -> bool:
        """Transition processing→failed for THIS attempt only.

        Conditioned on the fencing token (when the caller has one) so a
        stale attempt's late failure can't clobber a row the reaper already
        re-queued or a peer re-claimed.  Returns True if the row was written.
        """
        cols = {
            "error_message": error_message,
            "last_error": last_error,
            "parse_error_id": parse_error_id,
        }
        if message is not None:
            cols["message"] = message
        written = _transitions.fail(db, job_id, claimed_at, increment_retry=True, **cols)
        db.commit()
        if written == 0:
            logger.warning(
                "Ingestion job %s: stale attempt (claimed %s) — failure write skipped; "
                "the row is no longer this attempt's to fail",
                job_id, claimed_at,
            )
            return False
        # R1 — the row was this attempt's when it failed, so a scan still
        # named on it is this attempt's too: one the in-process cleanup did
        # not reach (a failure after the parser returned).  Not done on a
        # stale write (above): that row's scan is its new owner's.
        self._discard_dead_attempt_scan(db, job_id)
        return True

    def _run_job(self, job_id: int, claimed_at: Optional[datetime] = None) -> None:
        db = _session_module.SessionLocal()
        import_lock = ExitStack()
        try:
            job = db.query(IngestionJob).filter(IngestionJob.id == job_id).first()
            if not job:
                logger.error("Ingestion job %s not found", job_id)
                return

            if job_id in self._cancelled:
                # Fenced like every other lifecycle write: only THIS attempt's
                # processing row is failed.  A row the reaper re-queued or a
                # peer re-claimed in the meantime is not ours to touch.
                written = _transitions.fail(
                    db, job_id, claimed_at,
                    error_message="Cancelled before processing started",
                )
                db.commit()
                if written == 0:
                    logger.warning(
                        "Ingestion job %s: stale attempt (claimed %s) — "
                        "cancel-before-start write skipped; the row is no longer "
                        "this attempt's", job_id, claimed_at,
                    )
                else:
                    # R1 — nothing will parse this job again; drop what an
                    # earlier dead attempt left.
                    self._discard_dead_attempt_scan(db, job_id)
                return

            # Status already set to "processing" by poll_and_run_one;
            # set thread-local context so parsers can call report_progress()
            _active_job.service = self
            _active_job.db = db
            _active_job.job_id = job_id
            _active_job.claimed_at = claimed_at
            _active_job.scan_id = None

            # R1 — a scan still named on the row belongs to an attempt that
            # died (hard kill, reaped lease) after committing it.  Delete it
            # before parsing, or this attempt imports into a second scan and
            # the first is orphaned.
            # (A real id only: test doubles answer every attribute truthily.)
            if isinstance(job.in_progress_scan_id, int) or isinstance(
                job.in_progress_created_port_ids, list
            ):
                if not self._discard_dead_attempt_scan(db, job_id):
                    # M1 — the dead attempt's scan is still there.  Parsing
                    # now would stamp a NEW scan over the row's pointer and
                    # nothing would name the old one again.  The job fails
                    # with the pointer intact: a retry, or the reaper's sweep
                    # of failed jobs, cleans up first.
                    _left = (
                        "Could not remove what an earlier, interrupted attempt of this import "
                        "left behind, so nothing was imported. Retry the import; if it fails "
                        "again, check the worker log."
                    )
                    written = _transitions.fail(
                        db, job_id, claimed_at, increment_retry=True,
                        error_message=_left, message=_left,
                        last_error="Cleanup of the previous attempt's partial scan failed",
                    )
                    db.commit()
                    logger.error(
                        "Ingestion job %s: the previous attempt's partial scan could not be "
                        "removed — %s", job_id,
                        "job failed, pointer kept" if written else "row no longer this attempt's",
                    )
                    return
                job = db.get(IngestionJob, job_id)

            # S2 — every transaction of this import holds the project's
            # import lock in shared mode; the cleanup of a partial scan takes
            # it exclusively.
            _project_id = (job.options or {}).get("project_id") or job.project_id
            import_lock.enter_context(project_import_lock(db, _project_id))

            result = self._process_job(db, job)
            job = db.get(IngestionJob, job_id)  # Refresh job state
            if result:
                # Cancellation wins (code-review Critical 2): cancel_job runs in
                # the API process, so the in-proc _cancelled set isn't visible
                # here — it flips the row to 'failed' in the DB.  Transition
                # processing→completed ATOMICALLY; a zero-row update means the job
                # was cancelled (or reaped) and must NOT be resurrected as
                # completed, even though the parser's data committed.
                # Fencing token: a re-claimed job belongs to the newer
                # attempt; this one's result must not be published over it.
                # R1 — the scan is finished: it stops being "in progress" in
                # the same statement that completes the job.
                # R2 — and its record of created ports (cleanup only) goes too.
                _claimed = _transitions.complete(
                    db, job_id, claimed_at,
                    in_progress_scan_id=None, in_progress_created_port_ids=None,
                )
                if not _claimed:
                    logger.info(
                        "Ingestion job %s no longer 'processing' (cancelled) — not "
                        "marking completed",
                        job_id,
                    )
                    # v2.419.0 (review H6) — the parser finished its writes
                    # before it saw the cancel: the scan exists and its data is
                    # in the inventory.  Say so on the cancelled job instead of
                    # leaving the scan unlinked.  Only when nothing newer owns
                    # the job (a re-claimed attempt is 'processing' again).
                    db.refresh(job)
                    _scan_id = result.get("scan_id")
                    if job.status == "failed" and job.scan_id is None and _scan_id:
                        job.scan_id = _scan_id
                        # Kept and linked, so no longer a leftover to delete
                        # if the operator re-queues the job (R1).
                        job.in_progress_scan_id = None
                        job.in_progress_created_port_ids = None
                        job.message = (
                            f"Cancelled after the import had written scan #{_scan_id}: "
                            "its data is in the inventory. Delete that scan to undo it."
                        )
                    db.commit()
                    return
                db.refresh(job)
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
                job.partial = bool(result.get("partial", False))
                job.uninterpreted_lines = result.get("uninterpreted_lines")
                # v2.351.0 — the parser that actually produced the scan.
                job.final_file_type = result.get("final_file_type")
                # Final import-count summary (e.g. "6 DNS records").  Only
                # overwrite when the parser supplied one — streaming parsers
                # (nmap) already left a meaningful "N hosts" in progress.
                _progress_summary = result.get("progress_summary")
                if _progress_summary:
                    job.progress = _progress_summary
                carry_upload_identity(db, job)
                # Observability — one structured line per completed job so an
                # ingestion backlog is debuggable from `docker logs` without
                # new infra: how long the job waited for a worker (queue age)
                # and how long the parse took.  The per-job timestamps already
                # exist on the row; nothing aggregated them before.  Snapshot
                # the fields we log BEFORE commit: SessionLocal uses the
                # default expire_on_commit=True, so any attribute access after
                # the commit would trigger a row-reload SELECT.
                _started_at = job.started_at
                _created_at = job.created_at
                _completed_at = job.completed_at
                _tool_name = job.tool_name
                _scan_id = job.scan_id
                _skipped_count = job.skipped_count
                _submitted_by_id = job.submitted_by_id
                db.commit()
                queue_age_s = (
                    (_started_at - _created_at).total_seconds()
                    if _started_at and _created_at else None
                )
                parse_s = (
                    (_completed_at - _started_at).total_seconds()
                    if _completed_at and _started_at else None
                )
                logger.info(
                    "ingestion job=%s tool=%s scan=%s queue_age_s=%s "
                    "parse_s=%s skipped=%s",
                    job_id, _tool_name, _scan_id,
                    f"{queue_age_s:.1f}" if queue_age_s is not None else "n/a",
                    f"{parse_s:.1f}" if parse_s is not None else "n/a",
                    _skipped_count,
                )
                # Best-effort: alert reviewers that hosts they're following got
                # new scan data from this scan. The data already committed above,
                # so a notification failure must never fail the ingest.
                if _scan_id:
                    try:
                        from app.services.notification_service import NotificationService
                        NotificationService(db).notify_followers_of_scan_update(
                            scan_id=_scan_id, actor_id=_submitted_by_id,
                        )
                        db.commit()
                    except Exception:
                        logger.warning(
                            "scan-update follower notify failed for scan %s",
                            _scan_id, exc_info=True,
                        )
                        db.rollback()
        except ShutdownRequested:
            # The worker is stopping.  The parser's partial scan was already
            # deleted on the way out (_execute_parser); the job goes back to
            # the queue for the next worker, fenced like every other write.
            db.rollback()
            released = _transitions.release(
                db, job_id, claimed_at, message="Re-queued: the worker restarted during the import",
            )
            db.commit()
            logger.info(
                "Ingestion job %s handed back to the queue on shutdown%s",
                job_id, "" if released else " (no longer this attempt's — left as is)",
            )
        except ParseFailure as exc:
            db.rollback()
            # Audit finding H4: populate the retry_count + last_error
            # dead-letter columns so the ingestion queue UI can
            # surface repeated failures distinctly from one-off
            # errors.  Parse failures are terminal on the first
            # attempt (the file is structurally invalid, retrying
            # won't help), so we still transition to 'failed' in
            # one hop — retry_count records that this job was
            # attempted once and failed deterministically.
            # Guarded on the fencing token: a stale attempt must not fail
            # a row the reaper re-queued or a peer now owns.
            _err = exc.user_message or exc.underlying_error or str(exc)
            _msg = f"{_err} (Error ID: {exc.error_id})" if exc.error_id else _err
            self._fail_job_guarded(
                db, job_id, claimed_at,
                error_message=_err, message=_msg,
                last_error=f"ParseFailure: {_err}"[:4000],
                parse_error_id=exc.error_id,
            )
            logger.warning(
                "Failed ingestion job %s due to parse error: %s",
                job_id,
                exc.user_message or exc.underlying_error or str(exc),
            )
        except Exception:  # pragma: no cover - defensive logging
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
            # User-facing message stays generic — raw str(exc) on a driver
            # error carries SQL fragments and container paths into the
            # upload UI. The full detail lives in last_error below.
            # Keep a trimmed traceback for the UI — full stack would bloat
            # the column for huge parse graphs.  Guarded on the fencing token
            # like the ParseFailure path.
            tb_text = _tb.format_exc()
            self._fail_job_guarded(
                db, job_id, claimed_at,
                error_message=(
                    "Processing failed unexpectedly. See details or retry; "
                    "if it persists, check the server logs."
                ),
                message=None,
                last_error=(tb_text[-4000:] if len(tb_text) > 4000 else tb_text),
                parse_error_id=None,
            )
            logger.exception("Failed ingestion job %s", job_id)
        finally:
            _active_job.service = None
            _active_job.db = None
            _active_job.job_id = None
            _active_job.scan_id = None
            self._cancelled.discard(job_id)
            import_lock.close()
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
        # v2.351.0 — record the chain.  What the dispatcher would try first
        # is "detected"; an override replaces the attempt list with exactly
        # the chosen parser (see _build_parsing_attempts), so a wrong choice
        # fails visibly instead of another parser quietly taking over.
        override = job.format_override or (job.options or {}).get("format_override")
        job.detected_file_type = (
            self._detect_without_override(job, sample) if override
            else (parsing_attempts[0][0] if parsing_attempts else None)
        )
        db.commit()
        if not parsing_attempts:
            preview = sample[:4096]
            parse_error = log_parse_error(
                db=db,
                filename=original_filename,
                file_content=preview,
                error_type="format_error",
                file_type="unknown",
                custom_message=(
                    f"The chosen format '{override}' is not one this deployment can parse."
                    if override else "Unsupported file type or format."
                ),
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
                result["final_file_type"] = file_type
                return result
            except ParseFailure:
                # Cancellation / timeout is terminal for the whole job — it is
                # NOT a "this parser didn't match, try the next one" failure.
                # Falling through to the next attempt would re-run a cancelled
                # or timed-out job under a second parser. Clean the session and
                # let _run_job's ParseFailure handler mark the job.
                db.rollback()
                raise
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
            custom_message=(
                f"The chosen format '{override}' did not parse this file. "
                "Review the format and retry."
                if override else None
            ),
            project_id=job_project_id,
        )
        raise ParseFailure(
            "Failed to parse file",
            user_message=parse_error.user_message,
            error_id=parse_error.id,
            underlying_error=str(last_error) if last_error else None,
        )

    def _detect_without_override(self, job: IngestionJob, sample: bytes) -> Optional[str]:
        """What the dispatcher would have tried first had the operator not
        chosen a format — recorded as ``detected_file_type`` beside the
        override so the chain shows both."""
        shadow = SimpleNamespace(
            original_filename=job.original_filename, options=dict(job.options or {}),
            format_override=None,
        )
        shadow.options.pop("format_override", None)
        attempts = list(self._build_parsing_attempts(shadow, sample))
        return attempts[0][0] if attempts else None

    @staticmethod
    def _stop_if_not_discarded(discarded: Optional[bool], cause: BaseException) -> None:
        """M1, in the fallback chain: a parser failed after committing a scan
        and that scan could not be deleted.  An ordinary parser error would
        let the dispatcher try the next parser, whose new scan would replace
        the job's pointer and orphan this one — so the failure is made
        terminal.  (A cancel, timeout or shutdown already is; it propagates
        as it was.)"""
        if discarded is False and not isinstance(cause, ParseFailure):
            raise ParseFailure(
                "Partial scan could not be removed",
                user_message=(
                    "The import failed part-way and what it had written could not be removed, "
                    "so no other format was tried. Retry the import; if it fails again, check "
                    "the worker log."
                ),
                underlying_error=str(cause),
            ) from cause

    def _execute_parser(
        self,
        db: Session,
        job: IngestionJob,
        parser_class: Type,
        description: str,
    ) -> Dict[str, object]:
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

        # The scan this attempt creates is announced by the parser
        # (note_scan_created); a fallback attempt must not inherit the
        # previous parser's.
        _active_job.scan_id = None

        if parser_class is NessusIntegrationService:
            parser_instance = NessusIntegrationService(db)
            try:
                result = parser_instance.process_nessus_file(
                    storage_path, filename, project_id=project_id,
                    # v2.341.0 — resolved at upload time (form field, else the
                    # project's setting); the worker only carries it through.
                    skip_informational=bool(options.get("skip_informational", False)),
                )
            except Exception as exc:
                # Review 2026-10-01 C1 — cancel, timeout and shutdown now
                # propagate out of the Nessus import (they used to come back
                # as ``success: False``); its committed batches are removed
                # exactly as for every other streaming parser.
                self._stop_if_not_discarded(self._discard_attempt_scan(db, parser_instance), exc)
                raise
            if not result.get("success"):
                # C1 — a failed Nessus import (an error, a truncated export,
                # no host processed) used to leave the hosts and observations
                # of its committed batches in a scan no job pointed at, and
                # the re-upload the message asks for then made a second scan.
                # A failed import leaves nothing behind, like the others.
                removed = self._discard_attempt_scan(db, parser_instance)
                nessus_error_msg = result.get("error") or result.get("message") or "Nessus processing failed"
                user_msg = result.get("message") or result.get("error")
                if removed and user_msg:
                    # The service's message describes what it had written
                    # ("only N hosts were ingested"); say what is left.
                    user_msg = f"{user_msg} Nothing from this file was kept."
                parse_error = log_parse_error(
                    db=db,
                    filename=filename,
                    error_type="parsing_error",
                    file_type="nessus_xml",
                    custom_message=user_msg or nessus_error_msg,
                    project_id=project_id,
                )
                raise ParseFailure(
                    "Nessus processing failed",
                    user_message=user_msg,
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
            # Map class references back to callable constructors.  The registry
            # lives in build_parser_dispatch_map() so detection and dispatch are
            # one source of truth a contract test can check (a parser detected
            # but absent here dies with "Unsupported parser class").
            parser_map = build_parser_dispatch_map()
            parser_ctor = parser_map.get(parser_class)
            if parser_ctor is None:
                raise ValueError(f"Unsupported parser class {parser_class}")

            parser = parser_ctor(db)
            # v2.419.0 (H4) — JSON lines the shared reader could not decode.
            from app.parsers.streaming_json import begin_rejection_tally, end_rejection_tally
            _tally = begin_rejection_tally()
            try:
                # v2.353.0 — the tool the operator named at import (phase A's
                # column) reaches the parser, so attribution never has to be
                # read off the filename.
                _source_tool = getattr(job, "source_tool", None)
                scan = parser.parse_file(
                    storage_path, filename, project_id=project_id,
                    source_tool=_source_tool if isinstance(_source_tool, str) else None,
                )
                rejected_json_lines = end_rejection_tally(_tally)
            except Exception as exc:
                end_rejection_tally(_tally)
                # Streaming parsers (nmap/gnmap/masscan) commit the Scan row and
                # some hosts incrementally, so a mid-parse failure leaves a
                # committed partial Scan. Without this, the dispatcher's rollback
                # can't reach it and the fallback parser re-ingests the same file
                # under a SECOND Scan, orphaning the first. Delete exactly the id
                # this parser created (race-free — not a project-wide id sweep).
                # Review 2026-10-01 R1/R2/R6: every parser now names its scan
                # (a heartbeat commits, so each of them can leave one), and
                # the hosts and ports only this attempt created go with it.
                self._stop_if_not_discarded(self._discard_attempt_scan(db, parser), exc)
                raise
            # Ensure scan and all hosts are tagged with the project
            if project_id and scan:
                scan.project_id = project_id
            # v2.46.4 — provenance: agents pass the exact invocation as
            # `command_run` on /agent/uploads, but only self-
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
            parse_stats = dict(getattr(parser, "last_parse_stats", None) or {})
            # v2.419.0 (H4) — lines the JSON reader dropped before any parser
            # saw them: records lost, so skipped AND partial.
            if rejected_json_lines:
                parse_stats["skipped"] = int(parse_stats.get("skipped") or 0) + rejected_json_lines
                parse_stats["partial"] = True
                warnings_parts.append(
                    f"{rejected_json_lines} line{'s' if rejected_json_lines != 1 else ''} of the file "
                    "were not valid JSON and were skipped (a truncated or corrupted record)"
                )

        # tool_name_hint mismatch detection (v2.55.0 review finding M-1,
        # repositioned in v2.55.1 to cover BOTH branches).
        # `/agent/uploads` accepts a `tool_name` arg and stores it
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

        return {
            "scan_id": scan_id,
            "message": message,
            "tool_name": tool_name,
            "skipped_count": int(parse_stats.get("skipped", 0)) if parse_stats else 0,
            "parser_warnings": " | ".join(warnings_parts) if warnings_parts else None,
            # v2.332.0 — parsers have published ``partial`` (truncated file /
            # import stopped early) since v2.232.0; nothing read it, so a
            # truncated nmap file reached the UI as "1 record skipped".
            "partial": bool(parse_stats.get("partial")) if parse_stats else False,
            # Final import-count summary for the job's progress column.  Fast
            # parsers (dnsx/httpx/whatweb/eyewitness) never stream report_progress,
            # so without this their completed jobs showed an empty progress
            # column and no record count anywhere.
            "progress_summary": parse_stats.get("summary") if parse_stats else None,
            # v2.418.0 — the lines not interpreted, as redacted shapes.
            "uninterpreted_lines": parse_stats.get("uninterpreted") if parse_stats else None,
        }

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

        # v2.351.0 — an operator override is the whole attempt list: exactly
        # the chosen parser, no fallback.  Falling through to another parser
        # would be the silent format change the override exists to prevent.
        # Only a real string counts: test doubles (MagicMock) answer every
        # attribute with a truthy object, which must not read as an override.
        override = getattr(job, "format_override", None)
        if not isinstance(override, str) or not override:
            opts = getattr(job, "options", None)
            override = opts.get("format_override") if isinstance(opts, dict) else None
        if isinstance(override, str) and override:
            from app.services.format_registry import resolve_parser
            resolved = resolve_parser(str(override))
            if resolved is None:
                logger.warning("Job %s: unknown format override %r", getattr(job, "id", "?"), override)
                return []
            parser_class, description = resolved
            return [(str(override), parser_class, description)]

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
                attempts.append(_fallback("nmap_xml", NmapXMLParser, "Nmap XML file"))
            elif is_openvas:
                attempts.append(("openvas_xml", OpenVASParser, "OpenVAS/Greenbone XML report"))
                attempts.append(_fallback("nmap_xml", NmapXMLParser, "Nmap XML file"))
            elif is_nmap_root:
                attempts.append(("nmap_xml", NmapXMLParser, "Nmap XML file"))
                # Openvas root excludes nmaprun by construction, so
                # don't try openvas here; masscan as last fallback.
                attempts.append(_fallback("masscan_xml", MasscanParser, "Masscan XML file"))
            else:
                # No structural signal — keep the pre-v2.45.1 try-everything
                # order.  Every one of these is a FALLBACK: nothing was
                # recognised, and the staged review must not say otherwise
                # (an unrelated .xml used to read "Nmap XML · recognised by
                # structure" and be marked ready).
                attempts.append(_fallback("nmap_xml", NmapXMLParser, "Nmap XML file"))
                attempts.append(_fallback("openvas_xml", OpenVASParser, "OpenVAS/Greenbone XML report"))
                attempts.append(_fallback("masscan_xml", MasscanParser, "Masscan XML file"))

            # Always include Nessus as a last-ditch attempt — covers
            # .xml files that are actually .nessus exports mislabeled.
            attempts.append(_fallback("nessus_xml", NessusIntegrationService, "Nessus vulnerability scan"))
        elif filename.endswith(".gnmap"):
            try:
                from app.parsers.gnmap_parser import GnmapParser

                attempts.append(("nmap_gnmap", GnmapParser, "Nmap .gnmap file"))
            except ImportError as exc:
                logger.warning("Gnmap parser unavailable: %s", exc)
        elif filename.endswith((".json", ".jsonl", ".ndjson")):
            # httpx first because it has a very specific content
            # signature (``tech`` + ``webserver`` + ``url``) that rarely
            # false-positives.  Must come before other JSON probes.
            from app.parsers.httpx_parser import HttpxParser, looks_like_httpx
            if looks_like_httpx(sample, filename):
                attempts.append(("httpx_json", HttpxParser, "httpx web fingerprint (JSON/JSONL)"))
            # v2.140.0 — whatweb web fingerprint.  Distinct signature
            # (``target`` + ``plugins`` dict) so it sits beside httpx with
            # no risk of cross-matching the other JSON probes.
            from app.parsers.whatweb_parser import WhatwebParser, looks_like_whatweb
            if looks_like_whatweb(sample, filename):
                attempts.append(("whatweb_json", WhatwebParser, "whatweb web fingerprint (JSON/JSONL)"))
            # testssl.sh TLS assessment — a flat findings array (id + finding +
            # severity) that no other JSON probe emits, so it sits beside the
            # web-fingerprint probes with no cross-match risk.
            from app.parsers.testssl_parser import TestsslParser, looks_like_testssl
            if looks_like_testssl(sample, filename):
                attempts.append(("testssl_json", TestsslParser, "testssl.sh TLS assessment (JSON)"))
            # v2.411.0 — Nuclei results (template-id + info / matched-at).
            from app.parsers.nuclei_parser import NucleiParser
            if _cd.looks_like_nuclei(sample, filename):
                attempts.append(("nuclei_json", NucleiParser, "Nuclei results (JSON/JSONL)"))
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
            # RDAP network registration — provenance for a netblock (who it
            # is registered to, which ASN/org/country). Egress happens
            # operator-side via scripts/rdap-lookup.py; we only ingest.
            if _cd.looks_like_rdap(sample, filename):
                from app.parsers.rdap_parser import RdapParser
                attempts.append(("rdap_json", RdapParser, "RDAP network registration (JSON/NDJSON)"))
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
            # v2.353.0 — the header recognises an EyeWitness CSV; the
            # filename is now only a hint.
            if "eyewitness" in filename or "report" in filename or _cd.looks_like_eyewitness_csv(sample):
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
                # Nikto's JSON saved as .txt is labelled as the JSON it is;
                # the parser reads it by content (v2.424.1).
                if sample.lstrip(b"\xef\xbb\xbf").lstrip()[:1] in (b"[", b"{"):
                    attempts.append(("nikto_json", NiktoParser, "Nikto JSON report"))
                else:
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
