"""Queue operational metrics (#24) — admin-only JSON snapshot of both queues."""
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db import models


def _ingestion(db, project_id, **kw):
    job = models.IngestionJob(
        project_id=project_id,
        filename="f.xml",
        original_filename="f.xml",
        storage_path="/tmp/f.xml",
        **kw,
    )
    db.add(job)
    db.commit()
    return job


def test_queue_metrics_snapshot(client, db_session, test_project):
    now = datetime.now(timezone.utc)
    stale_cutoff = settings.INGESTION_JOB_TIMEOUT * settings.INGESTION_ORPHAN_CUTOFF_MULTIPLIER

    _ingestion(db_session, test_project.id, status="queued")
    _ingestion(db_session, test_project.id, status="queued")
    # Healthy in-flight job (fresh heartbeat) — not stale.
    _ingestion(db_session, test_project.id, status="processing",
               started_at=now, last_heartbeat=now)
    # Wedged in-flight job (heartbeat older than the reaper cutoff) — stale.
    _ingestion(db_session, test_project.id, status="processing",
               started_at=now - timedelta(seconds=stale_cutoff + 600),
               last_heartbeat=now - timedelta(seconds=stale_cutoff + 600))
    # Completed in the last hour — throughput + mean processing time.
    _ingestion(db_session, test_project.id, status="completed",
               started_at=now - timedelta(seconds=40), completed_at=now - timedelta(seconds=10))
    _ingestion(db_session, test_project.id, status="failed")

    resp = client.get("/api/v1/system/queue-metrics")
    assert resp.status_code == 200
    ing = resp.json()["ingestion"]

    assert ing["queued"] == 2
    assert ing["processing"] == 2
    assert ing["stale_processing"] == 1
    assert ing["failed"] == 1
    assert ing["completed_last_hour"] == 1
    assert ing["avg_processing_seconds"] == 30.0
    assert ing["oldest_queued_age_seconds"] >= 0
    # report queue present + empty in this test.
    assert resp.json()["report"]["queued"] == 0


def test_queue_metrics_requires_admin(client, db_session, test_user):
    from app.db.models_auth import UserRole
    test_user.role = UserRole.MEMBER
    db_session.commit()
    resp = client.get("/api/v1/system/queue-metrics")
    assert resp.status_code == 403
