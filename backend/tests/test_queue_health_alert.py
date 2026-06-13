"""The orphan reaper alerts admins about jobs it could NOT recover.

Routine crash-requeues self-heal silently; only a PERMANENT failure (retry cap
exceeded or the uploaded file gone) creates a deployment-wide 'system'
notification for global admins. Closes the loop on the silent-worker incident.
"""
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_project import Notification
from app.services.ingestion_service import IngestionService


def _admin(db, uid=200):
    u = User(
        id=uid, username="sysadmin", email="sysadmin@example.com", full_name="Sys",
        hashed_password="x", role=UserRole.ADMIN, is_active=True, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(u)
    db.flush()
    return u


def _stale_over_cap_job(db, project_id):
    cutoff_s = settings.INGESTION_JOB_TIMEOUT * settings.INGESTION_ORPHAN_CUTOFF_MULTIPLIER
    old = datetime.now(timezone.utc) - timedelta(seconds=cutoff_s + 600)
    # retry_count already at the cap -> the reaper's increment pushes it over,
    # so this orphan is permanently failed (not requeued).
    job = models.IngestionJob(
        project_id=project_id, filename="scan.xml", original_filename="scan.xml",
        storage_path="/tmp/does-not-matter.xml", status="processing",
        started_at=old, last_heartbeat=old, retry_count=settings.INGESTION_MAX_RETRIES,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_permanent_failure_alerts_admins(db_session, test_project):
    admin = _admin(db_session)
    job = _stale_over_cap_job(db_session, test_project.id)

    IngestionService().reap_orphaned_jobs()

    db_session.expire_all()
    assert db_session.query(models.IngestionJob).get(job.id).status == "failed"
    alerts = db_session.query(Notification).filter(
        Notification.user_id == admin.id, Notification.type == "system",
    ).all()
    assert len(alerts) == 1
    assert "could not be recovered" in alerts[0].title
    assert alerts[0].project_id is None  # deployment-wide


def test_requeue_does_not_alert(db_session, test_project, tmp_path):
    """A recoverable orphan (file present, under retry cap) is re-queued, NOT
    alerted — self-healing stays quiet."""
    admin = _admin(db_session)
    f = tmp_path / "scan.xml"
    f.write_text("<nmaprun/>")
    cutoff_s = settings.INGESTION_JOB_TIMEOUT * settings.INGESTION_ORPHAN_CUTOFF_MULTIPLIER
    old = datetime.now(timezone.utc) - timedelta(seconds=cutoff_s + 600)
    job = models.IngestionJob(
        project_id=test_project.id, filename="s.xml", original_filename="s.xml",
        storage_path=str(f), status="processing", started_at=old,
        last_heartbeat=old, retry_count=0,
    )
    db_session.add(job)
    db_session.commit()

    IngestionService().reap_orphaned_jobs()

    db_session.expire_all()
    assert db_session.query(models.IngestionJob).get(job.id).status == "queued"
    assert db_session.query(Notification).filter(
        Notification.user_id == admin.id, Notification.type == "system",
    ).count() == 0
