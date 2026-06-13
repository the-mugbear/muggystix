"""POST /upload/jobs/{id}/retry re-queues a FAILED job whose file is on disk.

The orphan reaper already re-queues stuck jobs; this exposes the same path to
the operator so a transient failure (DB blip, since-fixed parser bug) can be
retried without re-uploading a large scan file. Rejects non-failed jobs and
jobs whose upload was already cleaned up.
"""
from datetime import datetime, timezone

from app.db import models


def _failed_job(db, project_id, storage_path, status="failed"):
    job = models.IngestionJob(
        project_id=project_id,
        filename="scan.xml",
        original_filename="scan.xml",
        storage_path=str(storage_path),
        status=status,
        error_message="boom",
        completed_at=datetime.now(timezone.utc),
        retry_count=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _url(pid, job_id):
    return f"/api/v1/projects/{pid}/upload/jobs/{job_id}/retry"


def test_retry_failed_job_with_file_requeues(client, db_session, test_project, tmp_path):
    f = tmp_path / "scan.xml"
    f.write_text("<nmaprun/>")
    job = _failed_job(db_session, test_project.id, f)

    resp = client.post(_url(test_project.id, job.id))
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"

    db_session.expire_all()
    refreshed = db_session.query(models.IngestionJob).filter_by(id=job.id).first()
    assert refreshed.status == "queued"
    assert refreshed.retry_count == 1
    assert refreshed.completed_at is None
    assert refreshed.error_message is None


def test_retry_failed_job_with_missing_file_is_rejected(client, db_session, test_project):
    job = _failed_job(db_session, test_project.id, "/tmp/gone-missing-abc.xml")

    resp = client.post(_url(test_project.id, job.id))
    assert resp.status_code == 409
    assert "re-upload" in resp.json()["detail"].lower()

    db_session.expire_all()
    refreshed = db_session.query(models.IngestionJob).filter_by(id=job.id).first()
    assert refreshed.status == "failed"  # untouched


def test_retry_non_failed_job_is_rejected(client, db_session, test_project, tmp_path):
    f = tmp_path / "scan.xml"
    f.write_text("<nmaprun/>")
    job = _failed_job(db_session, test_project.id, f, status="completed")

    resp = client.post(_url(test_project.id, job.id))
    assert resp.status_code == 409
    assert "failed" in resp.json()["detail"].lower()


def test_retry_unknown_job_404(client, test_project):
    resp = client.post(_url(test_project.id, 999999))
    assert resp.status_code == 404
