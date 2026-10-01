"""Run an import the way the worker does — UNDER AN ACTIVE JOB.

Review 2026-10-01: no test ran a parser with the ingestion job's thread-local
set, so ``report_progress`` was a no-op everywhere in the suite.  Everything
that only happens on a heartbeat — the commit it performs, cancel / timeout /
shutdown reaching the parser, the scan id landing on the job row — was
invisible (C1, R1–R3, R6).

``queue_file`` registers a real ``ingestion_jobs`` row for a file on disk;
``run_next`` drives ``IngestionService.poll_and_run_one`` (real claim, real
dispatcher, real parser, real heartbeats).  ``on_heartbeat`` makes something
happen at the N-th heartbeat of the run:

    svc = IngestionService()
    job_id = queue_file(db_session, test_project.id, path)
    with on_heartbeat(svc, 2, lambda: svc.cancel_job(job_id)):
        run_next(db_session, svc)
    job = job_row(db_session, job_id)

``conftest`` rebinds ``SessionLocal`` onto the test connection, so the
worker's own sessions land in the test's transaction.  The test session is
committed before the run and expired after it, so the two never interleave.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app.db import models
from app.services.ingestion_service import IngestionService


class WorkerKilled(BaseException):
    """A hard kill (SIGKILL / OOM) at a chosen point.  A ``BaseException`` on
    purpose: no ``except Exception`` cleanup runs, which is the whole point —
    the job is left ``processing`` exactly as a dead worker leaves it."""


def queue_file(
    db,
    project_id: int,
    path,
    filename: Optional[str] = None,
    **options,
) -> int:
    """A ``queued`` job for the file at ``path``; returns its id."""
    path = Path(path)
    job = models.IngestionJob(
        project_id=project_id,
        filename=filename or path.name,
        original_filename=filename or path.name,
        storage_path=str(path),
        status="queued",
        retry_count=0,
        options={"project_id": project_id, **options},
    )
    db.add(job)
    db.commit()
    return job.id


def run_next(db, svc: Optional[IngestionService] = None) -> bool:
    """Claim and run the oldest queued job, as the worker loop does."""
    svc = svc or IngestionService()
    db.commit()
    try:
        return svc.poll_and_run_one()
    finally:
        db.expire_all()


def run_file(db, project_id: int, path, filename: Optional[str] = None,
             svc: Optional[IngestionService] = None, **options) -> models.IngestionJob:
    """``queue_file`` + ``run_next``; returns the job row as it ended."""
    job_id = queue_file(db, project_id, path, filename, **options)
    run_next(db, svc)
    return job_row(db, job_id)


def job_row(db, job_id: int) -> models.IngestionJob:
    db.expire_all()
    return db.query(models.IngestionJob).filter_by(id=job_id).one()


@contextmanager
def on_heartbeat(svc: IngestionService, nth: int, action: Callable[[], None]):
    """Run ``action`` just before the ``nth`` heartbeat of ``svc`` (1-based).
    Yields the list of progress strings the run reported, so a test can also
    assert that a parser heartbeats at all."""
    seen: List[Optional[str]] = []
    real = svc.update_heartbeat

    def hooked(db, job_id, progress=None, **kwargs):
        seen.append(progress)
        if len(seen) == nth:
            action()
        return real(db, job_id, progress, **kwargs)

    svc.update_heartbeat = hooked  # type: ignore[method-assign]
    try:
        yield seen
    finally:
        del svc.update_heartbeat


@contextmanager
def active_job(db, project_id: int, svc: Optional[IngestionService] = None):
    """A claimed (``processing``) job with the worker's thread-local set, for
    driving ONE parser object directly — ``report_progress`` heartbeats (and
    commits, and raises on cancel) exactly as under the worker, without the
    dispatcher's format detection.  Yields ``(job_id, svc)``."""
    from datetime import datetime, timezone

    from app.services import ingestion_service as _mod

    svc = svc or IngestionService()
    claim = datetime.now(timezone.utc).replace(microsecond=0)
    job = models.IngestionJob(
        project_id=project_id, filename="direct", original_filename="direct",
        storage_path="/nonexistent/direct", status="processing",
        started_at=claim, last_heartbeat=claim, retry_count=0,
        options={"project_id": project_id},
    )
    db.add(job)
    db.commit()
    job_id = job.id
    _mod._active_job.service = svc
    _mod._active_job.db = db
    _mod._active_job.job_id = job_id
    _mod._active_job.claimed_at = claim
    _mod._active_job.scan_id = None
    try:
        yield job_id, svc
    finally:
        _mod._active_job.service = None
        _mod._active_job.db = None
        _mod._active_job.job_id = None
        _mod._active_job.scan_id = None


def kill_worker() -> None:
    raise WorkerKilled()


def run_until_killed(db, svc: IngestionService) -> None:
    """``run_next`` for a run that a hook ends with ``WorkerKilled``.  Does
    what the process dying would do to the thread-local, nothing more."""
    from app.services import ingestion_service as _mod

    try:
        run_next(db, svc)
    except WorkerKilled:
        pass
    else:  # pragma: no cover — the hook did not fire
        raise AssertionError("the worker was never killed: the hook did not fire")
    finally:
        db.expire_all()
    assert getattr(_mod._active_job, "job_id", None) is None


def scans_of(db, project_id: int) -> List[models.Scan]:
    db.expire_all()
    return db.query(models.Scan).filter(models.Scan.project_id == project_id).order_by(models.Scan.id).all()


def all_scan_ids(db) -> List[int]:
    """Every scan, whatever its project (NetExec and gnmap create theirs with
    no project until the import succeeds)."""
    db.expire_all()
    return [row[0] for row in db.query(models.Scan.id).order_by(models.Scan.id).all()]


def host_ips(db, project_id: int) -> List[str]:
    db.expire_all()
    return sorted(
        row[0] for row in db.query(models.Host.ip_address).filter(models.Host.project_id == project_id)
    )


def new_host_count(db, scan_id: int) -> int:
    """Hosts the scan INTRODUCED — what /scans reports as "new hosts"."""
    return (
        db.query(models.HostScanHistory)
        .filter(models.HostScanHistory.scan_id == scan_id,
                models.HostScanHistory.host_created.is_(True))
        .count()
    )


def stats_of(job: models.IngestionJob) -> Dict[str, object]:
    return {"status": job.status, "message": job.message, "error": job.error_message,
            "progress": job.progress, "partial": job.partial}
