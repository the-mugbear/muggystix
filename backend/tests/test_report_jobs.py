"""Async report job pipeline — the report worker's service.

Covers the queue lifecycle (create → claim+run → completed artifact) for the
inventory JSON, plus the dead-letter mechanics (failure, stall reaper, expiry
cleanup).  Runs the service in-process (no worker container needed) against the
test DB; ``poll_and_run_one`` opens its own ``SessionLocal``, which the conftest
rebinds onto the test connection.

The agent package and the Markdown bundle were retired with "Export hosts"
(owner, 2026-10-07): their cases here became "a job that names one fails and
says so".  The client report's renders run on the same queue and are covered
in test_client_report_render.py.
"""
import json
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


def test_report_job_generates_artifact(db_session, test_project, test_user):
    _make_host(db_session, test_project.id)
    db_session.commit()

    service = ReportJobService()
    job = service.create_job(
        db_session, project_id=test_project.id, requested_by_id=test_user.id,
        format="json", report_type="comprehensive", filters={},
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

    assert done.result_filename.startswith("hosts_comprehensive_") and done.result_filename.endswith(".json")
    assert done.media_type == "application/json"
    payload = json.loads(Path(done.result_path).read_bytes())
    assert "hosts" in payload and payload["hosts"]
    assert "canonical_findings" in payload["hosts"][0]

    # Clean up the artifact this test wrote.
    service._remove_artifact(done)


@pytest.mark.parametrize("fmt", ["agent-package", "markdown-bundle"])
def test_a_job_for_a_retired_format_fails_and_says_so(fmt, db_session, test_project, test_user):
    """A row queued before the bundles were retired (or retried since) is
    never rendered as something else: it fails, names the format, and leaves
    no artifact directory behind."""
    _make_host(db_session, test_project.id)
    db_session.commit()
    service = ReportJobService()
    before = set(service._storage_root.iterdir()) if service._storage_root.exists() else set()
    job = service.create_job(
        db_session, project_id=test_project.id, requested_by_id=test_user.id,
        format=fmt, report_type="comprehensive", filters={},
    )
    assert service.poll_and_run_one() is True

    failed = db_session.get(ReportJob, job.id)
    db_session.refresh(failed)
    assert failed.status == "failed"
    assert fmt in failed.error_message and "CSV and JSON" in failed.error_message
    assert failed.result_path is None
    after = set(service._storage_root.iterdir()) if service._storage_root.exists() else set()
    assert after == before


def test_report_job_failure_sets_last_error(db_session, test_project, test_user, monkeypatch):
    service = ReportJobService()
    job = service.create_job(
        db_session, project_id=test_project.id, requested_by_id=test_user.id,
        format="json", report_type="comprehensive", filters={},
    )

    def _boom(*a, **k):
        raise RuntimeError("render exploded")

    from app.services.report_generator import ReportGenerator
    monkeypatch.setattr(ReportGenerator, "write_json_report", _boom, raising=True)
    before = set(service._storage_root.iterdir()) if service._storage_root.exists() else set()
    assert service.poll_and_run_one() is True

    failed = db_session.get(ReportJob, job.id)
    db_session.refresh(failed)
    assert failed.status == "failed"
    assert "render exploded" in (failed.last_error or "")
    assert "render exploded" in (failed.error_message or "")
    # The half-written streamed artifact's directory is removed.
    assert set(service._storage_root.iterdir()) == before


def test_the_json_covers_every_host_across_chunks(db_session, test_project, test_user, monkeypatch):
    """Review 2026-09-23 B-Ops-6 — the JSON stopped at an in-memory cap of
    2,000 hosts, so a whole engagement could not be exported.  It streams
    every matching host, chunk by chunk, and nothing is capped."""
    from app.core.config import settings
    from app.services.report_generator import ReportGenerator

    monkeypatch.setattr(settings, "REPORT_STREAM_CHUNK", 2)
    for i in range(5):
        host = _make_host(db_session, test_project.id, ip=f"10.0.1.{i + 1}")
    scan = models.Scan(project_id=test_project.id, filename="smb.xml", scan_type="nmap", tool_name="nmap")
    db_session.add(scan)
    db_session.flush()
    db_session.add(models.HostScript(
        host_id=host.id, scan_id=scan.id, script_id="smb-os-discovery", output="Windows 10",
    ))
    db_session.commit()

    service = ReportJobService()

    def run(fmt):
        job = service.create_job(
            db_session, project_id=test_project.id, requested_by_id=test_user.id,
            format=fmt, report_type="comprehensive", filters={},
        )
        assert service.poll_and_run_one() is True
        done = db_session.get(ReportJob, job.id)
        db_session.refresh(done)
        assert done.status == "completed", done.error_message
        return done

    host_id = host.id
    done = run("json")
    payload = json.loads(Path(done.result_path).read_bytes())
    assert [h["identity"]["ip_address"] for h in payload["hosts"]] == [f"10.0.1.{i}" for i in range(1, 6)]
    assert payload["summary"]["total_hosts"] == 5
    assert payload["summary"]["total_open_ports"] == 5
    assert payload["summary"]["truncated"] is False and payload["summary"]["host_cap"] is None
    assert {"findings", "hotspots", "systemic"} <= set(payload)
    # The chunked path writes the same records the agents' report-context
    # stream yields for these hosts (one builder, two readers), in one chunk.
    gen = ReportGenerator(db_session, test_user, project_id=test_project.id)
    whole = list(gen.iter_host_records(
        db_session.query(models.Host.id).filter(models.Host.project_id == test_project.id),
        chunk_size=50,
    ))
    roundtrip = lambda v: json.loads(json.dumps(v, default=str))  # noqa: E731
    assert payload["hosts"] == roundtrip(whole)
    # A script's output is not in the record; ``output_ref`` says it had one.
    (script,) = payload["hosts"][-1]["host_scripts"]
    assert script["script_id"] == "smb-os-discovery"
    assert script["output_ref"] == f"artifacts/hosts/{host_id}/host_scripts/smb-os-discovery.txt"
    assert "output" not in script
    service._remove_artifact(done)

    # ``inventory`` is the hosts alone: no project-wide roll-ups.
    job = service.create_job(
        db_session, project_id=test_project.id, requested_by_id=test_user.id,
        format="json", report_type="inventory", filters={},
    )
    assert service.poll_and_run_one() is True
    done = db_session.get(ReportJob, job.id)
    db_session.refresh(done)
    assert done.status == "completed", done.error_message
    assert done.result_filename.startswith("hosts_inventory_")
    payload = json.loads(Path(done.result_path).read_bytes())
    assert payload["report_type"] == "inventory" and len(payload["hosts"]) == 5
    assert not {"findings", "hotspots", "systemic"} & set(payload)
    service._remove_artifact(done)


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


def test_completion_is_fenced_against_a_reclaimed_lease(
    db_session, test_project, test_user
):
    """A-Ref-2: a worker whose lease was reaped and re-claimed by a peer must not
    publish its result over the new owner.

    The completion write is fenced on ``started_at`` (the claim token), so a run
    carrying a stale token matches zero rows and leaves the peer's row intact —
    no duplicate 'completed', no last-writer-wins, and the orphaned artifact is
    discarded rather than dangling."""
    _make_host(db_session, test_project.id)
    db_session.commit()

    service = ReportJobService()
    job = service.create_job(
        db_session, project_id=test_project.id, requested_by_id=test_user.id,
        format="json", report_type="comprehensive", filters={},
    )
    # Model the row as a PEER now owns it: processing under a fresh started_at.
    peer_started = datetime.now(timezone.utc)
    job.status = "processing"
    job.started_at = peer_started
    db_session.commit()

    # A stale worker runs the same job carrying an OLDER claim token.
    service._run_job(job.id, peer_started - timedelta(minutes=10))

    db_session.refresh(job)
    assert job.status == "processing", "a stale worker clobbered the peer's row"
    # tz-naive compare: SQLite hands the column back naive, Postgres aware.
    assert job.started_at.replace(tzinfo=None) == peer_started.replace(tzinfo=None)
    assert job.result_path is None, "a stale worker published a result over the peer"


def test_a_job_response_does_not_say_truncated(client, db_session, test_project, test_user):
    """Nothing can be capped any more, so the job no longer carries a flag
    that could only ever say "no" (``GET /reports/limits`` and the
    ``X-Report-Truncated`` header went with it)."""
    job = _job(db_session, test_project.id, "queued", requested_by_id=test_user.id)
    body = client.get(f"/api/v1/projects/{test_project.id}/reports/jobs/{job.id}").json()
    assert body["id"] == job.id and "truncated" not in body


# ---------------------------------------------------------------------------
# Completion notification — the JSON download outlives its dialog, so the
# requester is told when one finishes.  Fenced: a stale attempt never notifies.
# ---------------------------------------------------------------------------
def _report_notifications(db, job_id):
    from app.db.models_project import Notification
    return (
        db.query(Notification)
        .filter(Notification.source_type == "report_job", Notification.source_id == job_id)
        .all()
    )


def test_completion_notifies_the_requester_once(db_session, test_project, test_user):
    _make_host(db_session, test_project.id)
    db_session.commit()
    service = ReportJobService()
    job = service.create_job(
        db_session, project_id=test_project.id, requested_by_id=test_user.id,
        format="json", report_type="comprehensive", filters={},
    )
    assert service.poll_and_run_one() is True

    rows = _report_notifications(db_session, job.id)
    assert len(rows) == 1
    n = rows[0]
    assert n.user_id == test_user.id
    assert n.type == "report_ready"
    assert n.project_id == test_project.id
    assert n.title.startswith("Report ready: ")
    assert "comprehensive" in n.body and "json" in n.body
    assert n.actor_id is None

    done = db_session.get(ReportJob, job.id)
    db_session.refresh(done)
    service._remove_artifact(done)


def test_stale_completion_does_not_notify(db_session, test_project, test_user):
    _make_host(db_session, test_project.id)
    db_session.commit()
    service = ReportJobService()
    job = service.create_job(
        db_session, project_id=test_project.id, requested_by_id=test_user.id,
        format="json", report_type="comprehensive", filters={},
    )
    peer_started = datetime.now(timezone.utc)
    job.status = "processing"
    job.started_at = peer_started
    db_session.commit()

    service._run_job(job.id, peer_started - timedelta(minutes=10))

    assert _report_notifications(db_session, job.id) == []


def test_failure_notifies_the_requester(db_session, test_project, test_user, monkeypatch):
    service = ReportJobService()
    job = service.create_job(
        db_session, project_id=test_project.id, requested_by_id=test_user.id,
        format="json", report_type="comprehensive", filters={},
    )

    def _boom(*a, **k):
        raise RuntimeError("render exploded")

    from app.services.report_generator import ReportGenerator
    monkeypatch.setattr(ReportGenerator, "write_json_report", _boom, raising=True)
    assert service.poll_and_run_one() is True

    rows = _report_notifications(db_session, job.id)
    assert len(rows) == 1
    assert rows[0].type == "report_failed"
    assert rows[0].user_id == test_user.id
    assert "render exploded" in rows[0].body
