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


def test_starting_a_job_uses_one_pooled_connection_not_two(client, db_session, test_project, monkeypatch):
    """v2.361.1 — found in the logs of a 30-file drop: every ``/start`` took
    exactly DB_POOL_TIMEOUT (30 s) and the whole API froze, ``/health``
    included.  ``enqueue_job`` opened a SECOND pooled connection for its
    pg_notify while the request still held its own; the review dialog starts
    every ready file in parallel, so with more starts than the pool has
    connections each held one and waited for another.  The notify now rides
    the request's session: opening another one here is the regression."""
    from app.db import session as session_module

    job_id = _upload(client, test_project, NMAP_XML, "scan.xml", stage=True).json()["job_id"]

    # COUNTED, not raised: enqueue_job swallows every exception (the notify is
    # a hint), so a raising stub passes on the broken code too.
    opened = []
    real = session_module.SessionLocal

    def _counting(*args, **kwargs):
        opened.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(session_module, "SessionLocal", _counting)
    r = client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/start", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    assert opened == [], "enqueue_job opened a second session while the request holds one"

    # The same holds for a plain (unstaged) upload and for re-process.
    direct = _upload(client, test_project, HOST_PORT_TEXT, "direct.txt", "text/plain")
    assert direct.status_code == 200, direct.text
    job = _job(db_session, job_id)
    job.status = "completed"
    db_session.commit()
    again = client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/reprocess", json={})
    assert again.status_code in (200, 201), again.text
    assert opened == [], "an upload or re-process opened a second session"


def test_discard_clears_a_staged_job_and_its_file(client, db_session, test_project):
    """v2.355.0 — a staged job the operator will not start can be cleared:
    the file goes, the row stays as a dismissed failure (out of the queue,
    still in Ingestion Results)."""
    job_id = _upload(client, test_project, NMAP_XML, "scan.xml", stage=True).json()["job_id"]
    job = _job(db_session, job_id)
    path = Path(job.storage_path)
    r = client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/discard")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"
    db_session.refresh(job)
    assert job.dismissed_at is not None and "Discarded" in job.error_message
    assert not path.exists()
    # Discard is for staged jobs only.
    assert client.post(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/discard").status_code == 409


def test_discard_staged_takes_exactly_the_confirmed_jobs(client, db_session, test_project):
    """The page lists only its most recent jobs, so "discard every staged
    job" removed more files than the count the operator confirmed (for an
    admin, other people's too).  The ids shown are the ids discarded."""
    a = _upload(client, test_project, NMAP_XML, "a.xml", stage=True).json()["job_id"]
    b = _upload(client, test_project, HOST_PORT_TEXT, "b.txt", "text/plain", stage=True).json()["job_id"]
    unseen = _upload(client, test_project, HOST_PORT_TEXT, "c.txt", "text/plain", stage=True, allow_duplicate=True).json()["job_id"]
    q = _upload(client, test_project, HOST_PORT_TEXT, "q.txt", "text/plain", stage=True, allow_duplicate=True).json()["job_id"]
    queued = _job(db_session, q)
    queued.status = "queued"
    db_session.commit()

    url = f"/api/v1/projects/{test_project.id}/upload/jobs/discard-staged"
    # No list is no longer "everything".
    assert client.post(url).status_code == 422
    assert client.post(url, json={"job_ids": []}).status_code == 422

    # q is named but no longer staged: skipped, and the response says so.
    r = client.post(url, json={"job_ids": [a, b, q]})
    assert r.status_code == 200, r.text
    assert r.json()["discarded"] == 2 and sorted(r.json()["job_ids"]) == sorted([a, b])
    assert _job(db_session, a).status == "failed" and _job(db_session, b).status == "failed"
    db_session.refresh(queued)
    assert queued.status == "queued"
    # The staged job the operator was never shown is untouched.
    assert _job(db_session, unseen).status == "staged"


UNRELATED_XML = b"""<?xml version="1.0"?>
<inventory><item sku="A-1"><name>widget</name></item></inventory>
"""


def test_an_unrecognised_xml_is_not_recognised_by_structure(client, db_session, test_project):
    """The XML branch appends fallback parsers whatever the content is, and
    they survive the neutral-filename pass — so an unrelated .xml read
    "Nmap XML · recognised by structure" and was marked ready."""
    job_id = _upload(client, test_project, UNRELATED_XML, "inventory.xml", stage=True).json()["job_id"]
    d = client.get(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/detection").json()
    assert d["candidates"], "the fallbacks are still listed as things the operator may choose"
    assert {c["basis"] for c in d["candidates"]} == {"fallback"}
    assert d["needs_choice"] is True
    assert "No distinctive signature" in d["reason"]


def test_a_recognised_xml_keeps_its_fallbacks_labelled(client, db_session, test_project):
    job_id = _upload(client, test_project, NMAP_XML, "whatever.xml", stage=True).json()["job_id"]
    d = client.get(f"/api/v1/projects/{test_project.id}/upload/jobs/{job_id}/detection").json()
    by_type = {c["file_type"]: c["basis"] for c in d["candidates"]}
    assert by_type["nmap_xml"] == "structure"
    assert by_type["masscan_xml"] == "fallback" and by_type["nessus_xml"] == "fallback"
    assert d["needs_choice"] is False


def test_fallback_attempts_are_still_plain_descriptors():
    """The marker must not change what the worker and the dispatch contract
    see: a 3-tuple that unpacks as (file_type, parser_class, description)."""
    from app.services.ingestion_service import FallbackAttempt, _fallback

    attempt = _fallback("nmap_xml", object, "Nmap XML file")
    file_type, parser_class, description = attempt
    assert (file_type, parser_class, description) == ("nmap_xml", object, "Nmap XML file")
    assert isinstance(attempt, tuple) and isinstance(attempt, FallbackAttempt) and attempt.fallback is True


def test_the_format_list_does_not_need_a_detection(client, test_project):
    """A failed inspection returns no detection — which is when the operator
    most needs to pick a format by hand."""
    r = client.get(f"/api/v1/projects/{test_project.id}/upload/formats")
    assert r.status_code == 200, r.text
    formats = {f["file_type"]: f for f in r.json()}
    assert "nmap_xml" in formats and "naabu_output" in formats
    assert set(formats["nmap_xml"]) == {"file_type", "label", "family"}


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
