"""Staged import (v2.352.0; phase B of the staged-import plan).

Pins:

* an upload with ``stage=true`` lands as ``staged`` and is not queued;
* detection reports each candidate's basis (structure vs filename), a raw
  preview and an interpreted sample, and says when a choice is needed;
* start records the operator's choice on the job and queues it; an unknown
  format is refused; a queued job cannot be started twice;
* a failed job with its file on disk can be started again (review + retry);
* staged jobs nobody starts expire and their files are removed.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import models
from app.services.staged_import_service import expire_staged_jobs

NMAP_XML = b"""<?xml version="1.0"?>
<nmaprun scanner="nmap" args="nmap -sS 10.9.9.1" start="1700000000" version="7.94">
<host><status state="up"/><address addr="10.9.9.1" addrtype="ipv4"/>
<ports><port protocol="tcp" portid="22"><state state="open"/><service name="ssh"/></port>
<port protocol="tcp" portid="443"><state state="open"/><service name="https"/></port></ports>
</host>
<runstats><finished time="1700000100"/><hosts up="1" down="0" total="1"/></runstats>
</nmaprun>
"""
HOST_PORT_TEXT = b"10.0.0.5:443\n10.0.0.5:22\n10.0.0.6:80\n"


def _upload(client, project, data, name, mime="text/xml", **form):
    return client.post(
        f"/api/v1/projects/{project.id}/upload/",
        files={"file": (name, data, mime)},
        data={k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()},
    )


def _job(db, job_id):
    return db.get(models.IngestionJob, job_id)


def test_staged_upload_is_not_queued(client, db_session, test_project):
    r = _upload(client, test_project, NMAP_XML, "scan.xml", stage=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "staged"
    assert "staged" in body["message"].lower()
    job = _job(db_session, body["job_id"])
    assert job.status == "staged"
    assert Path(job.storage_path).exists()


def test_detection_structural_nmap_is_ready_with_a_sample(client, db_session, test_project):
    job_id = _upload(client, test_project, NMAP_XML, "whatever.xml", stage=True).json()["job_id"]
    r = client.get(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/detection")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["primary"] == "nmap_xml"
    assert d["candidates"][0] == {"file_type": "nmap_xml", "label": "Nmap XML", "basis": "structure", "rank": 0}
    assert d["needs_choice"] is False and d["reason"] is None
    assert d["preview"]["raw"].startswith("<?xml")
    assert d["preview"]["sample"] == ["10.9.9.1: 2 open ports (tcp/22, tcp/443)"]
    assert any(f["file_type"] == "naabu_output" for f in d["formats"])


def test_detection_structural_text_is_ready_under_any_name(client, db_session, test_project):
    """Phase D: host:port text is recognised by its lines, so even a neutral
    filename is ready and the basis is the structure."""
    job_id = _upload(client, test_project, HOST_PORT_TEXT, "results.txt", "text/plain", stage=True).json()["job_id"]
    d = client.get(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/detection").json()
    assert d["primary"] == "naabu_output"
    assert d["candidates"][0]["basis"] == "structure"
    assert d["needs_choice"] is False
    assert d["preview"]["sample"][:2] == ["10.0.0.5:443", "10.0.0.5:22"]


ODD_TEXT = b"10.0.0.5 443\n10.0.0.5 22\n10.0.0.6 80\n"  # no format's shape


def test_detection_filename_only_and_unknown_need_a_choice(client, db_session, test_project):
    # Text no detector recognises, under a name that says naabu: filename only.
    named = _upload(client, test_project, ODD_TEXT, "naabu-results.txt", "text/plain", stage=True).json()["job_id"]
    d = client.get(f"/api/v1/projects/{test_project.id}/upload/jobs/{named}/detection").json()
    assert d["primary"] == "naabu_output"
    assert d["candidates"][0]["basis"] == "filename"
    assert d["needs_choice"] is True
    assert "filename" in d["reason"]
    assert d["preview"]["sample"][:2] == ["10.0.0.5 443", "10.0.0.5 22"]

    # The same bytes under a neutral name: nothing recognised.
    anon = _upload(client, test_project, ODD_TEXT, "results.txt", "text/plain", stage=True, allow_duplicate=True).json()["job_id"]
    d2 = client.get(f"/api/v1/projects/{test_project.id}/upload/jobs/{anon}/detection").json()
    assert d2["candidates"] == [] and d2["primary"] is None
    assert d2["needs_choice"] is True
    assert "No distinctive signature" in d2["reason"]


def test_start_records_the_choice_and_queues(client, db_session, test_project):
    job_id = _upload(client, test_project, HOST_PORT_TEXT, "results.txt", "text/plain", stage=True).json()["job_id"]
    r = client.post(
        f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/start",
        json={"format_override": "naabu_output", "source_tool": "naabu 2.3"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    assert r.json()["format_override"] == "naabu_output"
    assert r.json()["source_tool"] == "naabu 2.3"
    job = _job(db_session, job_id)
    db_session.refresh(job)
    assert (job.status, job.format_override, job.source_tool) == ("queued", "naabu_output", "naabu 2.3")

    # Already queued: cannot be started again.
    again = client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/start", json={})
    assert again.status_code == 409


def test_start_refuses_an_unknown_format(client, db_session, test_project):
    job_id = _upload(client, test_project, HOST_PORT_TEXT, "results.txt", "text/plain", stage=True).json()["job_id"]
    r = client.post(
        f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/start",
        json={"format_override": "not_a_format"},
    )
    assert r.status_code == 422
    assert _job(db_session, job_id).status == "staged"


def test_failed_job_with_its_file_can_be_started_again(client, db_session, test_project):
    job_id = _upload(client, test_project, HOST_PORT_TEXT, "results.txt", "text/plain", stage=True).json()["job_id"]
    job = _job(db_session, job_id)
    job.status = "failed"
    job.error_message = "Unsupported file type or format"
    db_session.commit()
    r = client.post(
        f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/start",
        json={"format_override": "naabu_output"},
    )
    assert r.status_code == 200, r.text
    db_session.refresh(job)
    assert job.status == "queued" and job.error_message is None and job.format_override == "naabu_output"


def test_detection_does_not_construct_a_service_per_request(client, db_session, test_project, monkeypatch):
    """v2.354.1 — a fresh IngestionService per detection ran the storage
    writability probe, and concurrent detections from a multi-file upload
    raced on its process-id-named test file: some requests 500'd with
    "storage is not writable".  Detection must use the module singleton."""
    from app.services import ingestion_service as module

    def _boom(self, *a, **kw):
        raise AssertionError("detect_for_job must not construct a new IngestionService")

    monkeypatch.setattr(module.IngestionService, "__init__", _boom)
    job_id = _upload(client, test_project, NMAP_XML, "scan.xml", stage=True).json()["job_id"]
    r = client.get(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/detection")
    assert r.status_code == 200, r.text
    assert r.json()["primary"] == "nmap_xml"


def test_staged_jobs_expire_and_their_files_go(client, db_session, test_project):
    fresh = _upload(client, test_project, NMAP_XML, "fresh.xml", stage=True).json()["job_id"]
    old = _upload(client, test_project, HOST_PORT_TEXT, "old.txt", "text/plain", stage=True).json()["job_id"]
    old_job = _job(db_session, old)
    old_job.created_at = datetime.now(timezone.utc) - timedelta(hours=30)
    db_session.commit()
    old_path = Path(old_job.storage_path)
    assert old_path.exists()

    assert expire_staged_jobs(db_session) == 1
    db_session.refresh(old_job)
    assert old_job.status == "failed" and "expired" in old_job.error_message
    assert not old_path.exists()
    assert _job(db_session, fresh).status == "staged"
