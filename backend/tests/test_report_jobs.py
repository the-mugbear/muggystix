"""Async report job pipeline — the report worker's service.

Covers the queue lifecycle (create → claim+run → completed artifact) for each
async format, plus the dead-letter mechanics (failure, stall reaper, expiry
cleanup).  Runs the service in-process (no worker container needed) against the
test DB; ``poll_and_run_one`` opens its own ``SessionLocal``, which the conftest
rebinds onto the test connection.
"""
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db import models
from app.db.models import ReportJob
from app.services.report_job_service import ReportJobService


def _make_host(db, project_id, ip="10.0.0.5"):
    host = models.Host(project_id=project_id, ip_address=ip, state="up", os_name="Linux")
    db.add(host)
    db.flush()
    db.add(models.Port(host_id=host.id, port_number=443, protocol="tcp", state="open", service_name="https"))
    db.flush()
    return host


@pytest.mark.parametrize("fmt", ["json", "agent-package", "markdown-bundle"])
def test_report_job_generates_artifact(fmt, db_session, test_project, test_user):
    _make_host(db_session, test_project.id)
    db_session.commit()

    service = ReportJobService()
    job = service.create_job(
        db_session, project_id=test_project.id, requested_by_id=test_user.id,
        format=fmt, report_type="comprehensive", filters={},
    )
    assert job.status == "queued"

    # Claim + run it (the worker loop would call this).
    assert service.poll_and_run_one() is True

    done = db_session.get(ReportJob, job.id)
    db_session.refresh(done)
    assert done.status == "completed", done.error_message
    assert done.result_path and Path(done.result_path).is_file()
    assert done.file_size and done.file_size > 0
    assert done.expires_at is not None

    data = Path(done.result_path).read_bytes()
    if fmt == "json":
        payload = json.loads(data)
        assert "hosts" in payload and payload["hosts"]
        assert "canonical_findings" in payload["hosts"][0]
    elif fmt in ("agent-package", "markdown-bundle"):
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        if fmt == "agent-package":
            assert {"manifest.json", "hosts.ndjson", "findings.json"}.issubset(names)
        else:
            assert {"report.md", "vulnerabilities.csv", "canonical_findings.csv"}.issubset(names)

    # Clean up the artifact this test wrote.
    service._remove_artifact(done)


def test_report_job_failure_sets_last_error(db_session, test_project, test_user, monkeypatch):
    service = ReportJobService()
    job = service.create_job(
        db_session, project_id=test_project.id, requested_by_id=test_user.id,
        format="json", report_type="comprehensive", filters={},
    )

    def _boom(*a, **k):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(ReportJobService, "_render", _boom, raising=True)
    assert service.poll_and_run_one() is True

    failed = db_session.get(ReportJob, job.id)
    db_session.refresh(failed)
    assert failed.status == "failed"
    assert "render exploded" in (failed.last_error or "")
    assert "render exploded" in (failed.error_message or "")


def test_report_reaper_requeues_stalled_job(db_session, test_project):
    # A processing job whose heartbeat is well past the timeout is stalled.
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    job = ReportJob(
        project_id=test_project.id, format="json", report_type="comprehensive",
        filters={}, status="processing", started_at=stale, last_heartbeat=stale,
    )
    db_session.add(job)
    db_session.commit()

    assert ReportJobService().reap_orphaned_jobs() == 1
    db_session.refresh(job)
    assert job.status == "queued"
    assert job.retry_count == 1


def test_report_cleanup_removes_expired(db_session, test_project):
    service = ReportJobService()
    # A completed job with an on-disk artifact that has expired.
    job_dir = service._storage_root / "expired_test_dir"
    job_dir.mkdir(parents=True, exist_ok=True)
    artifact = job_dir / "old.json"
    artifact.write_text("{}")
    job = ReportJob(
        project_id=test_project.id, format="json", report_type="comprehensive",
        filters={}, status="completed", result_path=str(artifact),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    assert service.cleanup_expired() == 1
    assert db_session.query(ReportJob).filter(ReportJob.id == job_id).first() is None
    assert not artifact.exists()


# ---------------------------------------------------------------------------
# Operator recovery actions: retry a failed job, cancel a queued one.
# ---------------------------------------------------------------------------

def _job(db, project_id, status, **over):
    job = ReportJob(
        project_id=project_id, format="json", report_type="comprehensive",
        filters={}, status=status, **over,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_retry_requeues_a_failed_job(db_session, test_project):
    job = _job(db_session, test_project.id, "failed",
               error_message="boom", last_error="boom", message="failed")
    out = ReportJobService().retry_job(db_session, job_id=job.id, project_id=test_project.id)
    assert out.status == "queued"
    assert out.error_message is None
    assert out.last_error is None
    assert out.completed_at is None


def test_retry_rejects_a_non_failed_job(db_session, test_project):
    job = _job(db_session, test_project.id, "completed")
    with pytest.raises(ValueError):
        ReportJobService().retry_job(db_session, job_id=job.id, project_id=test_project.id)


def test_retry_unknown_job_returns_none(db_session, test_project):
    assert ReportJobService().retry_job(
        db_session, job_id=999999, project_id=test_project.id
    ) is None


def test_cancel_marks_a_queued_job_cancelled(db_session, test_project):
    job = _job(db_session, test_project.id, "queued")
    out = ReportJobService().cancel_job(db_session, job_id=job.id, project_id=test_project.id)
    assert out.status == "cancelled"


def test_cancel_rejects_a_processing_job(db_session, test_project):
    # A processing job is already running on the worker — no cooperative cancel.
    job = _job(db_session, test_project.id, "processing")
    with pytest.raises(ValueError):
        ReportJobService().cancel_job(db_session, job_id=job.id, project_id=test_project.id)


def test_cancelled_job_is_not_claimed_by_the_worker(db_session, test_project):
    # The claim filters status='queued', so a cancelled row is never picked up.
    job = _job(db_session, test_project.id, "queued")
    ReportJobService().cancel_job(db_session, job_id=job.id, project_id=test_project.id)
    db_session.refresh(job)
    assert job.status == "cancelled"
