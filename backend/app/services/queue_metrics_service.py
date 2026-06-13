"""Operational metrics for the durable job queues (ingestion + report).

A lightweight JSON snapshot for monitoring/ops — queue depth, the age of the
oldest waiting job, how many in-flight jobs the reaper would consider stale,
the failed backlog, and recent throughput + mean processing time. Computed
with SQL aggregates (no row materialisation) so it's cheap to poll.

"Stale" mirrors each reaper's own cutoff exactly, so a non-zero stale count
means "jobs the reaper will reclaim on its next pass" — i.e. a worker is
wedged or gone, not just slow.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models


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
    failed = int(by_status.get("failed", 0))

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


def queue_metrics(db: Session) -> dict:
    """Snapshot both job queues. Stale cutoffs match each reaper's own."""
    ingestion_cutoff = (
        settings.INGESTION_JOB_TIMEOUT * settings.INGESTION_ORPHAN_CUTOFF_MULTIPLIER
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ingestion": _queue_snapshot(db, models.IngestionJob, ingestion_cutoff),
        "report": _queue_snapshot(
            db, models.ReportJob, settings.REPORT_JOB_TIMEOUT_SECONDS
        ),
    }
