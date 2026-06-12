"""Orphan reaper auto-requeue (audit A5).

A worker that dies mid-parse leaves its job stuck in 'processing'. Previously the
reaper dead-ended it straight to 'failed' (operator must re-upload). Now it
re-queues — reusing the stored upload — up to INGESTION_MAX_RETRIES, then fails.
Only requeues when the stored file is still on disk (else it would just refail).
"""
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.db import models
from app.services.ingestion_service import IngestionService


def _stale_processing_job(db, project_id, storage_path, retry_count=0):
    # Heartbeat older than the orphan cutoff (timeout * multiplier).
    cutoff_s = settings.INGESTION_JOB_TIMEOUT * settings.INGESTION_ORPHAN_CUTOFF_MULTIPLIER
    old = datetime.now(timezone.utc) - timedelta(seconds=cutoff_s + 600)
    job = models.IngestionJob(
        project_id=project_id,
        filename="scan.xml",
        original_filename="scan.xml",
        storage_path=str(storage_path),
        status="processing",
        started_at=old,
        last_heartbeat=old,
        retry_count=retry_count,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_orphan_with_file_present_is_requeued(db_session, test_project, tmp_path):
    f = tmp_path / "scan.xml"
    f.write_text("<nmaprun/>")
    job = _stale_processing_job(db_session, test_project.id, f)

    reaped = IngestionService().reap_orphaned_jobs()
    assert reaped == 1

    db_session.expire_all()
    refreshed = db_session.query(models.IngestionJob).filter_by(id=job.id).first()
    assert refreshed.status == "queued"
    assert refreshed.retry_count == 1
    assert refreshed.started_at is None
    assert refreshed.completed_at is None


def test_orphan_over_retry_cap_is_failed(db_session, test_project, tmp_path):
    f = tmp_path / "scan.xml"
    f.write_text("<nmaprun/>")
    # Already at the cap: the increment pushes it over, so it must fail, not loop.
    job = _stale_processing_job(
        db_session, test_project.id, f, retry_count=settings.INGESTION_MAX_RETRIES
    )

    IngestionService().reap_orphaned_jobs()

    db_session.expire_all()
    refreshed = db_session.query(models.IngestionJob).filter_by(id=job.id).first()
    assert refreshed.status == "failed"
    assert refreshed.retry_count == settings.INGESTION_MAX_RETRIES + 1
    assert "auto-retries" in (refreshed.error_message or "")


def test_orphan_with_missing_file_is_failed_not_requeued(db_session, test_project):
    job = _stale_processing_job(db_session, test_project.id, "/tmp/gone-missing-xyz.xml")

    IngestionService().reap_orphaned_jobs()

    db_session.expire_all()
    refreshed = db_session.query(models.IngestionJob).filter_by(id=job.id).first()
    assert refreshed.status == "failed"
    assert "no longer present" in (refreshed.error_message or "")
