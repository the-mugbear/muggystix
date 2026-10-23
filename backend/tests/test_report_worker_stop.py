"""A report worker asked to stop hands its job back (review 2026-10-22, N4).

Issuing renders html, docx and qmd, each a Quarto run plus a post-processor of
up to REPORT_RENDER_TIMEOUT_SECONDS: more than the container's stop grace.
The render never looked at the shutdown flag, so a rebuild SIGKILLed it and
the job sat 'processing' until the reaper.  Now each step boundary is
``worker_loop.render_checkpoint``: on SIGTERM it raises ``WorkerStopping`` (a
BaseException, so no ``except Exception`` turns it into a failure) and the job
runner releases the job to the queue, as the ingestion worker does.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app import worker_loop
from app.core.config import settings
from app.db.models import ReportJob
from app.services.report_job_service import ReportJobService


def test_the_checkpoint_stops_only_a_worker_asked_to(monkeypatch):
    worker_loop.render_checkpoint()                      # running: nothing happens
    monkeypatch.setattr(worker_loop, "_shutdown", True)
    with pytest.raises(worker_loop.WorkerStopping):
        worker_loop.render_checkpoint()
    assert not issubclass(worker_loop.WorkerStopping, Exception)


def test_a_job_stopped_mid_way_goes_back_to_the_queue(db_session, test_project, test_user, monkeypatch):
    service = ReportJobService()
    job = service.create_job(
        db_session, project_id=test_project.id, requested_by_id=test_user.id,
        format="json", report_type="comprehensive", filters={},
    )

    def _stopped(*a, **k):
        worker_loop.render_checkpoint()

    from app.services.report_generator import ReportGenerator
    monkeypatch.setattr(ReportGenerator, "write_json_report", _stopped, raising=True)
    monkeypatch.setattr(worker_loop, "_shutdown", True)
    before = set(service._storage_root.iterdir()) if service._storage_root.exists() else set()

    assert service.poll_and_run_one() is True

    back = db_session.get(ReportJob, job.id)
    db_session.refresh(back)
    assert back.status == "queued", back.error_message
    assert (back.started_at, back.last_heartbeat, back.error_message) == (None, None, None)
    assert not back.retry_count                           # a restart is not a failed attempt
    assert "re-queued" in (back.message or "")
    assert set(service._storage_root.iterdir()) == before  # nothing of the attempt left


TEMPLATES = next(
    (p for p in (Path("/app/report-templates"), Path(__file__).resolve().parents[2] / "report-templates")
     if (p / "pentest" / "template.json").is_file()),
    None,
)


@pytest.mark.skipif(TEMPLATES is None, reason="report-templates/ is not mounted here")
@pytest.mark.skipif(shutil.which("quarto") is None, reason="Quarto is only in the report-worker image")
def test_an_issue_render_stopped_stays_pending_and_renders_on_its_next_run(
    client, db_session, test_project, tmp_path, monkeypatch,
):
    """The issue render's own ``except Exception`` marks the report FAILED;
    being stopped by a restart must not."""
    from app.services.client_report_render import run_client_job

    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(TEMPLATES))
    monkeypatch.setattr(settings, "REPORT_FILES_DIR", str(tmp_path / "client_reports"))
    base = f"/api/v1/projects/{test_project.id}/client-reports"
    draft = client.post(base, json={"kind": "full"}).json()
    assert client.post(f"{base}/{draft['id']}/issue").json()["render_status"] == "pending"

    def _job():
        job = ReportJob(project_id=test_project.id, format="report-issue", report_type="client",
                        filters={"report_id": draft["id"]}, status="processing")
        db_session.add(job)
        db_session.commit()
        return job

    monkeypatch.setattr(worker_loop, "_shutdown", True)
    with pytest.raises(worker_loop.WorkerStopping):
        run_client_job(db_session, _job())
    db_session.rollback()
    db_session.expire_all()
    assert client.get(f"{base}/{draft['id']}").json()["render_status"] == "pending"

    monkeypatch.setattr(worker_loop, "_shutdown", False)
    assert run_client_job(db_session, _job()) is None
    assert client.get(f"{base}/{draft['id']}").json()["render_status"] == "done"
