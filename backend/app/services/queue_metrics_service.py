"""Operational metrics for the durable job queues (ingestion + report).

A lightweight JSON snapshot for monitoring/ops — queue depth, the age of the
oldest waiting job, how many in-flight jobs the reaper would consider stale,
the failed backlog, and recent throughput + mean processing time. Computed
with SQL aggregates (no row materialisation) so it's cheap to poll.

"Stale" mirrors each reaper's own cutoff exactly, so a non-zero stale count
means "jobs the reaper will reclaim on its next pass" — i.e. a worker is
wedged or gone, not just slow.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.services.import_attention_service import superseded_import_condition

logger = logging.getLogger(__name__)


def _queue_snapshot(db: Session, model, stale_cutoff_seconds: int) -> dict:
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=stale_cutoff_seconds)
    throughput_window = now - timedelta(hours=1)

    # status -> count, in one grouped pass.
    by_status = dict(
        db.query(model.status, func.count(model.id)).group_by(model.status).all()
    )
    queued = int(by_status.get("queued", 0))
    processing = int(by_status.get("processing", 0))
    # The failed BACKLOG: failed jobs nobody has dismissed yet.  The health
    # card tells the operator to "review and dismiss them" — counting
    # dismissed ones too meant that advice could never clear the warning.
    failed_q = db.query(func.count(model.id)).filter(model.status == "failed")
    if hasattr(model, "dismissed_at"):
        failed_q = failed_q.filter(model.dismissed_at.is_(None))
    if model is models.IngestionJob:
        # v2.403.0 — nor ones whose file a later job imported (superseded).
        failed_q = failed_q.filter(~superseded_import_condition())
    failed = int(failed_q.scalar() or 0)

    oldest_created: Optional[datetime] = (
        db.query(func.min(model.created_at)).filter(model.status == "queued").scalar()
    )
    oldest_queued_age_seconds = (
        max(0.0, (now - oldest_created).total_seconds()) if oldest_created else 0.0
    )

    # In-flight jobs the reaper would reclaim: heartbeat older than the cutoff,
    # or never heartbeated and started before the cutoff. Identical predicate
    # to reap_orphaned_jobs so the number is actionable, not advisory.
    stale_processing = (
        db.query(func.count(model.id))
        .filter(model.status == "processing")
        .filter(
            (model.last_heartbeat.is_(None) & (model.started_at < stale_cutoff))
            | (model.last_heartbeat < stale_cutoff)
        )
        .scalar()
    )

    # Throughput + mean processing seconds over the last hour (completed only).
    completed_last_hour, avg_processing_seconds = (
        db.query(
            func.count(model.id),
            func.avg(func.extract("epoch", model.completed_at - model.started_at)),
        )
        .filter(model.status == "completed")
        .filter(model.completed_at >= throughput_window)
        .filter(model.started_at.isnot(None))
        .one()
    )

    return {
        "queued": queued,
        "processing": processing,
        "failed": failed,
        "stale_processing": int(stale_processing or 0),
        "oldest_queued_age_seconds": round(oldest_queued_age_seconds, 1),
        "completed_last_hour": int(completed_last_hour or 0),
        "avg_processing_seconds": (
            round(float(avg_processing_seconds), 1)
            if avg_processing_seconds is not None
            else None
        ),
        "stale_cutoff_seconds": stale_cutoff_seconds,
    }


def _failed_ingestion_by_project(db: Session) -> list:
    """The undismissed failed ingestion jobs per project, largest first.

    The queue is deployment-wide but the list that shows those jobs (Ingestion
    Results, ``/parse-errors?status=failed``) is per project, so the health card
    needs to know WHICH projects hold them to link somewhere that lists them.
    One grouped query.
    """
    from app.db.models_project import Project

    Job = models.IngestionJob
    rows = (
        db.query(Job.project_id, Project.name, func.count(Job.id))
        .join(Project, Project.id == Job.project_id)
        .filter(Job.status == "failed", Job.dismissed_at.is_(None), ~superseded_import_condition())
        .group_by(Job.project_id, Project.name)
        .order_by(func.count(Job.id).desc(), Project.name)
        .all()
    )
    return [
        {"project_id": pid, "project_name": name, "count": int(count)}
        for pid, name, count in rows
    ]


def queue_metrics(db: Session) -> dict:
    """Snapshot both job queues. Stale cutoffs match each reaper's own."""
    ingestion_cutoff = (
        settings.INGESTION_JOB_TIMEOUT * settings.INGESTION_ORPHAN_CUTOFF_MULTIPLIER
    )
    ingestion = _queue_snapshot(db, models.IngestionJob, ingestion_cutoff)
    ingestion["failed_by_project"] = _failed_ingestion_by_project(db)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ingestion": ingestion,
        "report": _queue_snapshot(
            db, models.ReportJob, settings.REPORT_JOB_TIMEOUT_SECONDS
        ),
        "disk": disk_snapshot(),
    }


# Free space below either threshold is "low".  Production filled its disk on
# 2026-09-24 (retained uploads, Docker images and build cache) and Postgres
# PANICked — "No space left on device" — until space was freed.
DISK_LOW_FREE_BYTES = 10 * 1024 ** 3
DISK_LOW_FREE_FRACTION = 0.10


def disk_snapshot(path: Optional[str] = None) -> Optional[dict]:
    """Free space on the filesystem holding the uploads (v2.420.0) — on a
    single-host deployment, the disk Postgres and Docker share.  None when it
    cannot be read."""
    import os
    import shutil

    path = path or settings.UPLOAD_DIR
    try:
        usage = shutil.disk_usage(path if os.path.exists(path) else os.path.dirname(path) or "/")
    except OSError:
        return None
    return {
        "total_bytes": usage.total,
        "free_bytes": usage.free,
        "low": usage.free < DISK_LOW_FREE_BYTES or usage.free < usage.total * DISK_LOW_FREE_FRACTION,
    }


def warn_if_ingestion_backlog_high(db: Session) -> Optional[dict]:
    """Log a WARNING when the ingestion queue is steadily backing up.

    Distinct from the orphan reaper, which alerts on *wedged/failed* jobs: this
    catches uploads arriving faster than the single worker drains, or a poison
    job re-queuing — a queue that's deep or has been waiting a long time, with
    no single job yet 'stale'.  Called from the worker's periodic hook (one
    worker process, so no leader election needed).  Returns the triggering
    snapshot dict when it warned, else None — handy for tests/observability.

    Either threshold trips it; both at 0 disables (returns None without
    querying).  Two cheap aggregates, no row materialisation.
    """
    depth_threshold = settings.INGESTION_BACKLOG_WARN_DEPTH
    age_threshold = settings.INGESTION_BACKLOG_WARN_AGE_SECONDS
    if depth_threshold <= 0 and age_threshold <= 0:
        return None

    now = datetime.now(timezone.utc)
    queued = int(
        db.query(func.count(models.IngestionJob.id))
        .filter(models.IngestionJob.status == "queued")
        .scalar()
        or 0
    )
    oldest_created = (
        db.query(func.min(models.IngestionJob.created_at))
        .filter(models.IngestionJob.status == "queued")
        .scalar()
    )
    oldest_age = (
        max(0.0, (now - oldest_created).total_seconds()) if oldest_created else 0.0
    )

    depth_hit = depth_threshold > 0 and queued >= depth_threshold
    age_hit = age_threshold > 0 and oldest_age >= age_threshold
    if not (depth_hit and queued) and not (age_hit and queued):
        # Never warn on an empty queue — a zero-depth queue with a stale
        # oldest_age can't happen (no queued rows → oldest_age 0), but guard
        # the depth=0 edge explicitly so a misconfigured threshold of 0-ish
        # plus an empty queue stays silent.
        return None

    snapshot = {
        "queued": queued,
        "oldest_queued_age_seconds": round(oldest_age, 1),
    }
    logger.warning(
        "Ingestion backlog high: %d job(s) queued, oldest waiting %.0fs "
        "(thresholds: depth>=%s, age>=%ss). The worker is falling behind — "
        "check worker health/throughput or scale ingestion.",
        queued,
        oldest_age,
        depth_threshold or "off",
        age_threshold or "off",
    )
    return snapshot
