"""Review 2026-10-08, ingestion stream: one regression test per fix."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models

SHA = "a" * 64


def _job(db, project_id, path, *, status="failed", **cols):
    job = models.IngestionJob(
        project_id=project_id, filename="scan.xml", original_filename="scan.xml",
        storage_path=str(path), status=status, retry_count=0,
        options=cols.pop("options", {"project_id": project_id}), **cols,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _fresh(db, job_id):
    db.expire_all()
    return db.query(models.IngestionJob).filter_by(id=job_id).one()


def _retry_url(pid, job_id):
    return f"/api/v1/projects/{pid}/upload/jobs/{job_id}/retry"


# --------------------------------------------------------------------------
# A — retrying a failed job runs the duplicate guard, like /start
# --------------------------------------------------------------------------

def test_retry_of_a_failed_job_whose_twin_was_imported_is_refused(client, db_session, test_project, tmp_path):
    f = tmp_path / "scan.xml"
    f.write_text("<nmaprun/>")
    job = _job(
        db_session, test_project.id, f, content_sha256=SHA, error_message="boom",
        completed_at=datetime.now(timezone.utc),
    )
    # The same file, uploaded again while this job sat failed, and imported.
    db_session.add(models.Scan(project_id=test_project.id, filename="scan.xml", content_sha256=SHA))
    db_session.commit()

    resp = client.post(_retry_url(test_project.id, job.id))

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "duplicate_scan"
    row = _fresh(db_session, job.id)
    assert row.status == "failed"
    assert row.retry_count == 0
    assert row.error_message == "boom"


def test_retry_of_a_job_uploaded_as_import_anyway_is_not_refused(client, db_session, test_project, tmp_path):
    f = tmp_path / "scan.xml"
    f.write_text("<nmaprun/>")
    job = _job(
        db_session, test_project.id, f, content_sha256=SHA,
        options={"project_id": test_project.id, "allow_duplicate": True},
    )
    db_session.add(models.Scan(project_id=test_project.id, filename="scan.xml", content_sha256=SHA))
    db_session.commit()

    resp = client.post(_retry_url(test_project.id, job.id))

    assert resp.status_code == 200
    assert resp.json() == {"job_id": job.id, "status": "queued", "message": "Job re-queued"}
    assert _fresh(db_session, job.id).status == "queued"


def test_retry_and_start_leave_a_failed_job_in_the_same_state(client, db_session, test_project, tmp_path):
    """One failed → queued path: the attempt count and the cleared error
    fields do not depend on which button was pressed.  A retry keeps the
    format the job carried; a start records the one it was given."""
    rows = []
    for name in ("retry", "start"):
        f = tmp_path / f"{name}.xml"
        f.write_text("<nmaprun/>")
        job = _job(
            db_session, test_project.id, f, error_message="boom", last_error="trace",
            format_override="nmap_xml", completed_at=datetime.now(timezone.utc),
        )
        url = f"/api/v1/projects/{test_project.id}/upload/jobs/{job.id}/{name}"
        resp = client.post(url, json={}) if name == "start" else client.post(url)
        assert resp.status_code == 200, resp.text
        rows.append(_fresh(db_session, job.id))
    retried, started = rows
    for row in rows:
        assert (row.status, row.retry_count) == ("queued", 1)
        assert (row.error_message, row.last_error, row.parse_error_id, row.completed_at) == (None,) * 4
    assert retried.format_override == "nmap_xml"
    assert started.format_override is None


# --------------------------------------------------------------------------
# B — a Nessus re-import follows the plugin's new severity
# --------------------------------------------------------------------------

def _nessus_file(tmp_path, name, *, severity, plugin_name, solution=""):
    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0" ?><NessusClientData_v2><Report name="r">'
        '<ReportHost name="10.9.0.5"><HostProperties><tag name="host-ip">10.9.0.5</tag></HostProperties>'
        f'<ReportItem port="22" svc_name="ssh" protocol="tcp" severity="{severity}" pluginID="90317" '
        f'pluginName="{plugin_name}"><description>Weak.</description>'
        f"{solution}</ReportItem></ReportHost></Report></NessusClientData_v2>"
    )
    return path


def test_a_nessus_reimport_follows_the_plugins_new_severity(db_session, test_project, tmp_path):
    from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
    from app.services.nessus_integration_service import NessusIntegrationService

    pid = test_project.id  # the import expunges the session
    first = NessusIntegrationService(db_session).process_nessus_file(
        str(_nessus_file(tmp_path, "a.nessus", severity=1, plugin_name="SSH Weak Algorithms",
                         solution="<solution>Disable them.</solution>")),
        project_id=pid,
    )
    assert first["success"], first
    second = NessusIntegrationService(db_session).process_nessus_file(
        str(_nessus_file(tmp_path, "b.nessus", severity=4, plugin_name="SSH Weak Algorithms Supported")),
        project_id=pid,
    )
    assert second["success"], second

    db_session.expire_all()
    rows = db_session.query(Vulnerability).filter(Vulnerability.plugin_id == "90317").all()
    assert len(rows) == 1
    row = rows[0]
    assert row.severity == VulnerabilitySeverity.CRITICAL
    assert row.title == "SSH Weak Algorithms Supported"
    # A field the re-import does not carry keeps its stored value.
    assert row.solution == "Disable them."
    # "First recorded by" never moves; the re-observation is the later scan.
    assert (row.scan_id, row.last_seen_scan_id) == (first["scan_id"], second["scan_id"])


# --------------------------------------------------------------------------
# C — gnmap: a heartbeat that fails is not "one bad host"
# --------------------------------------------------------------------------

def test_a_failed_heartbeat_fails_the_gnmap_import_instead_of_losing_hosts_silently(
    db_session, test_project, tmp_path,
):
    from tests.ingestion_job_harness import host_ips, on_heartbeat, queue_file, run_next, job_row
    from app.services.ingestion_service import IngestionService

    path = tmp_path / "sweep.gnmap"
    path.write_text(
        "# Nmap 7.94 scan initiated Mon Oct  5 10:00:00 2026 as: nmap -oG sweep.gnmap 10.8.0.0/24\n"
        + "".join(f"Host: 10.8.0.{i} ()\tPorts: 22/open/tcp//ssh///\n" for i in range(1, 121))
        + "# Nmap done at Mon Oct  5 10:01:00 2026 -- 256 IP addresses (120 hosts up) scanned\n"
    )

    def _commit_fails():
        raise RuntimeError("could not commit the batch")

    svc = IngestionService()
    job_id = queue_file(db_session, test_project.id, path)
    with on_heartbeat(svc, 1, _commit_fails) as beats:
        run_next(db_session, svc)

    assert beats, "the parser never heartbeated"
    job = job_row(db_session, job_id)
    assert job.status == "failed"
    assert job.scan_id is None
    assert host_ips(db_session, test_project.id) == []


# --------------------------------------------------------------------------
# D — the cancel is the row; the service keeps no set of cancelled jobs
# --------------------------------------------------------------------------

def test_the_service_keeps_no_in_memory_record_of_cancels():
    from app.services.ingestion_service import IngestionService

    assert not hasattr(IngestionService(), "_cancelled")


# --------------------------------------------------------------------------
# G — one record of "this attempt's scan": the job row's pointer
# --------------------------------------------------------------------------

NMAP_XML = (
    '<?xml version="1.0"?>\n<nmaprun scanner="nmap"><host><status state="up"/>'
    '<address addr="10.7.0.1" addrtype="ipv4"/></host></nmaprun>\n'
)


def test_a_failure_after_the_parser_returned_does_not_orphan_its_scan(
    db_session, test_project, tmp_path, monkeypatch,
):
    """The parser committed its scan and returned; the dispatcher then failed
    before its own commit.  The next parser in the chain stamps a new scan on
    the job, so the first must be removed before it is tried."""
    from app.parsers.nmap_parser import NmapXMLParser
    from app.services.ingestion_service import IngestionService
    from tests.ingestion_job_harness import all_scan_ids, job_row, queue_file, run_next

    path = tmp_path / "scan.xml"
    path.write_text(NMAP_XML)
    svc = IngestionService()
    attempt = ("nmap_xml", NmapXMLParser, "Nmap XML file")
    monkeypatch.setattr(svc, "_build_parsing_attempts", lambda job, sample: [attempt, attempt])

    class _FailsInTheDispatcher:
        command_line = None

        @property
        def project_id(self):
            return None

        @project_id.setter
        def project_id(self, value):
            raise RuntimeError("failed after the parser returned")

    real, created = NmapXMLParser.parse_file, []

    def parse_file(self, *args, **kwargs):
        scan = real(self, *args, **kwargs)
        created.append(scan.id)
        return _FailsInTheDispatcher() if len(created) == 1 else scan

    monkeypatch.setattr(NmapXMLParser, "parse_file", parse_file)
    job_id = queue_file(db_session, test_project.id, path)
    run_next(db_session, svc)

    job = job_row(db_session, job_id)
    assert len(created) == 2
    assert (job.status, job.scan_id, job.in_progress_scan_id) == ("completed", created[1], None)
    assert all_scan_ids(db_session) == [created[1]]


def test_a_stale_attempt_never_deletes_the_scan_the_new_owner_is_writing(db_session, test_project):
    """The pointer is read under this attempt's claim token: an attempt whose
    job was re-claimed finds the NEW attempt's scan named on the row and must
    leave it alone."""
    from app.services import ingestion_service as mod

    old_claim = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(microsecond=0)
    new_claim = datetime.now(timezone.utc).replace(microsecond=0)
    scan = models.Scan(project_id=test_project.id, filename="live.xml")
    db_session.add(scan)
    db_session.flush()
    scan_id = scan.id
    job_id = _job(db_session, test_project.id, "/nonexistent/scan.xml", status="processing",
                  started_at=new_claim, last_heartbeat=new_claim, in_progress_scan_id=scan_id).id
    svc = mod.IngestionService()

    def scan_exists():
        db_session.expire_all()
        return db_session.query(models.Scan.id).filter_by(id=scan_id).count() == 1

    mod._active_job.job_id, mod._active_job.claimed_at = job_id, old_claim
    try:
        assert svc._discard_attempt_scan(db_session, job_id) is None
        assert scan_exists()
        # The owning attempt's own failure does remove it.
        mod._active_job.claimed_at = new_claim
        assert svc._discard_attempt_scan(db_session, job_id) is True
    finally:
        mod._active_job.job_id = mod._active_job.claimed_at = None
    assert not scan_exists()


# --------------------------------------------------------------------------
# H — the heartbeat is one statement; the timeout comes from the claim token
# --------------------------------------------------------------------------

def test_a_heartbeat_is_one_statement_and_still_times_out(db_session, test_project, monkeypatch):
    import pytest
    from sqlalchemy import event

    from app.core.config import settings
    from app.services.ingestion_service import IngestionService, ParseFailure

    claim = (datetime.now(timezone.utc) - timedelta(seconds=30)).replace(microsecond=0)
    job = _job(db_session, test_project.id, "/nonexistent/scan.xml", status="processing",
               started_at=claim, last_heartbeat=claim)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "ingestion_jobs" in statement:
            statements.append(statement.split()[0].upper())

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        IngestionService().update_heartbeat(db_session, job.id, "10 hosts", claimed_at=claim)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert statements == ["UPDATE"]

    monkeypatch.setattr(settings, "INGESTION_JOB_TIMEOUT", 10)
    with pytest.raises(ParseFailure, match="timed out"):
        IngestionService().update_heartbeat(db_session, job.id, "20 hosts", claimed_at=claim)


# --------------------------------------------------------------------------
# E — the reaper's budget counts the reaper's own re-queues only
# --------------------------------------------------------------------------

def _stale(db, project_id, path, **cols):
    from app.core.config import settings

    cutoff_s = settings.INGESTION_JOB_TIMEOUT * settings.INGESTION_ORPHAN_CUTOFF_MULTIPLIER
    old = datetime.now(timezone.utc) - timedelta(seconds=cutoff_s + 600)
    return _job(db, project_id, path, status="processing", started_at=old, last_heartbeat=old, **cols)


def test_a_failure_and_an_operator_retry_do_not_spend_the_reapers_budget(
    client, db_session, test_project, tmp_path,
):
    """Fail once, the operator retries, the worker is hard-killed: the reaper
    has auto-retried nothing yet and must re-queue, not fail the job for
    "exceeded N auto-retries"."""
    from app.core.config import settings
    from app.services.ingestion_service import IngestionService, _transitions

    f = tmp_path / "scan.xml"
    f.write_text("<nmaprun/>")
    claim = datetime.now(timezone.utc).replace(microsecond=0)
    job = _job(db_session, test_project.id, f, status="processing", started_at=claim, last_heartbeat=claim)
    # An ordinary failure, then the operator's retry.
    assert _transitions.fail(db_session, job.id, claim, increment_retry=True, error_message="boom") == 1
    db_session.commit()
    assert client.post(_retry_url(test_project.id, job.id)).status_code == 200
    row = _fresh(db_session, job.id)
    assert row.retry_count == settings.INGESTION_MAX_RETRIES == 2
    # Claimed again, and the worker dies.
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=settings.INGESTION_JOB_TIMEOUT * settings.INGESTION_ORPHAN_CUTOFF_MULTIPLIER + 600
    )
    row.status, row.started_at, row.last_heartbeat = "processing", stale, stale
    db_session.commit()

    assert IngestionService().reap_orphaned_jobs() == 1

    row = _fresh(db_session, job.id)
    assert row.status == "queued"
    assert (row.reap_count, row.retry_count) == (1, 2)


def test_the_reaper_still_gives_up_after_its_own_budget(db_session, test_project, tmp_path):
    from app.core.config import settings
    from app.services.ingestion_service import IngestionService

    f = tmp_path / "scan.xml"
    f.write_text("<nmaprun/>")
    job = _stale(db_session, test_project.id, f, reap_count=settings.INGESTION_MAX_RETRIES)

    IngestionService().reap_orphaned_jobs()

    row = _fresh(db_session, job.id)
    assert row.status == "failed"
    assert "auto-retries" in (row.error_message or "")
    assert row.retry_count == 0  # the page's "retried N×" is not the reaper's counter


# --------------------------------------------------------------------------
# J1 — a dead worker's job is known at once: nobody holds its attempt's lock
# --------------------------------------------------------------------------

def _nmap_hosts(tmp_path, count):
    path = tmp_path / "many.xml"
    path.write_text(
        '<?xml version="1.0"?>\n<nmaprun scanner="nmap">'
        + "".join(
            f'<host><status state="up"/><address addr="10.6.{i // 250}.{i % 250 + 1}" addrtype="ipv4"/></host>'
            for i in range(count)
        )
        + "</nmaprun>\n"
    )
    return path


def test_a_hard_killed_workers_job_is_requeued_without_waiting_for_the_lease(
    db_session, test_project, tmp_path,
):
    from app.services.ingestion_service import IngestionService
    from tests.ingestion_job_harness import (
        all_scan_ids, job_row, kill_worker, on_heartbeat, queue_file, run_next, run_until_killed,
    )

    worker = IngestionService()
    job_id = queue_file(db_session, test_project.id, _nmap_hosts(tmp_path, 250))
    with on_heartbeat(worker, 2, kill_worker):
        run_until_killed(db_session, worker)
    dead = job_row(db_session, job_id)
    assert dead.status == "processing" and isinstance(dead.in_progress_scan_id, int)
    # The heartbeat is seconds old — far inside the time-based window.
    assert datetime.now(timezone.utc) - dead.last_heartbeat < timedelta(minutes=1)
    db_session.commit()

    # Another worker (or this one, restarted) reaps before its first claim.
    restarted = IngestionService()
    assert restarted.reap_orphaned_jobs() == 1
    row = job_row(db_session, job_id)
    assert (row.status, row.reap_count) == ("queued", 1)
    assert "worker is gone" in (row.last_error or "")

    # The next claim removes the dead attempt's scan and imports the file once.
    run_next(db_session, restarted)
    row = job_row(db_session, job_id)
    assert row.status == "completed"
    assert all_scan_ids(db_session) == [row.scan_id]


def test_a_live_peers_job_is_never_reaped_and_holds_its_lock_from_the_claim(
    db_session, test_project, tmp_path,
):
    """Two workers: while A imports, B's reaper finds A's attempt alive (its
    lock is held on A's own connection) and leaves it."""
    from sqlalchemy.orm import Session

    from app.services import ingestion_service as mod
    from tests.conftest import engine
    from tests.ingestion_job_harness import job_row, on_heartbeat, queue_file, run_next

    worker_a = mod.IngestionService()
    job_id = queue_file(db_session, test_project.id, _nmap_hosts(tmp_path, 250))
    seen = {}

    def peer_looks():
        # Worker B: its own connection for the lock question.  (Its reaper's
        # statements run on the test's one sandboxed connection — through
        # the session already open on it — with no commit of their own.)
        claim = mod._active_job.claimed_at
        with Session(bind=engine) as peer:
            seen["over_while_running"] = mod.attempt_is_over(peer, job_id, claim)
            reaped = mod._transitions.requeue_or_fail_stale(
                mod._active_job.db, cutoff=worker_a._orphan_cutoff(), max_retries=2,
                attempt_is_over=lambda j, started: mod.attempt_is_over(peer, j, started),
            )
            peer.rollback()
        seen["claim"] = claim
        seen["reaped_by_b"] = reaped.total

    with on_heartbeat(worker_a, 1, peer_looks):
        run_next(db_session, worker_a)

    assert seen["over_while_running"] is False and seen["reaped_by_b"] == 0
    row = job_row(db_session, job_id)
    assert (row.status, row.reap_count) == ("completed", 0)
    # Finished: the lock went with the attempt.
    assert mod.attempt_is_over(db_session, job_id, seen["claim"]) is True


def test_the_liveness_lock_is_per_attempt_and_in_its_own_key_space():
    from app.services import ingestion_service as mod

    t0 = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
    first = mod._liveness_key(7, t0)
    # The next claim of the same job is another attempt: it never waits on,
    # or passes for, a reaped attempt that is still holding its lock.
    assert mod._liveness_key(7, t0 + timedelta(microseconds=1)) != first
    assert mod._liveness_key(7, t0.replace(tzinfo=None)) == first
    assert first["cls"] == mod._JOB_LIVENESS_LOCK_CLASS != mod._PROJECT_IMPORT_LOCK_CLASS
    assert -(2 ** 31) <= first["attempt"] < 2 ** 31


def test_a_claim_that_cannot_take_its_liveness_lock_leaves_the_job_queued(
    db_session, test_project, tmp_path, monkeypatch,
):
    from app.services import ingestion_service as mod
    from tests.ingestion_job_harness import job_row, queue_file, run_next

    f = tmp_path / "scan.xml"
    f.write_text(NMAP_XML)
    job_id = queue_file(db_session, test_project.id, f)
    monkeypatch.setattr(mod, "hold_attempt_liveness", lambda db, job_id, claimed_at: None)

    assert run_next(db_session, mod.IngestionService()) is False
    assert job_row(db_session, job_id).status == "queued"


# --------------------------------------------------------------------------
# F — a job's file is removed under its row lock, and the row records it
# --------------------------------------------------------------------------

def _stored(tmp_path, name):
    job_dir = tmp_path / name
    job_dir.mkdir()
    f = job_dir / "scan.xml"
    f.write_text("<nmaprun/>")
    return f


def test_a_discarded_staged_jobs_file_goes_before_the_commit_and_the_row_says_so(
    db_session, test_project, tmp_path, monkeypatch,
):
    from app.services import staged_import_service as sis

    f = _stored(tmp_path, "discard")
    job = _job(db_session, test_project.id, f, status="staged")
    seen = {}
    real_commit = db_session.commit

    def commit():
        seen.setdefault("file_present_at_commit", f.exists())
        return real_commit()

    monkeypatch.setattr(db_session, "commit", commit)
    sis.discard_staged_job(db_session, job)

    # Once the commit makes the job ``failed`` (startable again) the file is
    # already gone: no start can pass its file check in between.
    assert seen == {"file_present_at_commit": False}
    row = _fresh(db_session, job.id)
    assert row.status == "failed" and row.file_removed_at is not None
    assert sis.file_retained(row) is False


def test_an_expired_staged_jobs_file_goes_before_the_commit(db_session, test_project, tmp_path, monkeypatch):
    from app.services import staged_import_service as sis

    f = _stored(tmp_path, "expire")
    job = _job(db_session, test_project.id, f, status="staged")
    job.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.commit()
    seen = {}
    real_commit = db_session.commit

    def commit():
        seen.setdefault("file_present_at_commit", f.exists())
        return real_commit()

    monkeypatch.setattr(db_session, "commit", commit)
    assert sis.expire_staged_jobs(db_session) == 1

    assert seen == {"file_present_at_commit": False}
    assert _fresh(db_session, job.id).file_removed_at is not None


def test_the_retention_sweep_stamps_the_row_and_never_selects_it_again(db_session, test_project, tmp_path):
    from sqlalchemy import event

    from app.services import staged_import_service as sis

    long_ago = datetime.now(timezone.utc) - timedelta(days=30)
    kept = _job(db_session, test_project.id, _stored(tmp_path, "kept"), status="completed",
                completed_at=datetime.now(timezone.utc))
    swept = _job(db_session, test_project.id, _stored(tmp_path, "swept"), status="completed",
                 completed_at=long_ago)
    # A row from before the column: its file went long ago, nothing recorded it.
    old = _job(db_session, test_project.id, tmp_path / "gone" / "scan.xml", status="failed",
               completed_at=long_ago)

    assert sis.expire_retained_files(db_session) == 1
    rows = {j.id: _fresh(db_session, j.id) for j in (kept, swept, old)}
    assert rows[kept.id].file_removed_at is None and sis.file_retained(rows[kept.id])
    assert rows[swept.id].file_removed_at is not None
    assert rows[old.id].file_removed_at is not None
    assert not hasattr(sis, "_files_known_gone")

    # The next sweep — in any worker process — finds nothing to look at.
    locks = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            locks.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert sis.expire_retained_files(db_session) == 0
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert locks == []


def test_the_results_page_reads_the_row_not_the_disk(client, db_session, test_project, tmp_path, monkeypatch):
    from pathlib import Path

    f = _stored(tmp_path, "page")
    kept = _job(db_session, test_project.id, f, status="completed", completed_at=datetime.now(timezone.utc))
    gone = _job(db_session, test_project.id, f, status="completed", completed_at=datetime.now(timezone.utc),
                file_removed_at=datetime.now(timezone.utc))

    def no_disk(self):  # pragma: no cover — the assertion is that it is not called
        raise AssertionError(f"the page asked the disk about {self}")

    monkeypatch.setattr(Path, "exists", no_disk)
    resp = client.get(f"/api/v1/projects/{test_project.id}/parse-errors/ingestion-results")
    monkeypatch.undo()

    assert resp.status_code == 200, resp.text
    rows = {r["id"]: r for r in resp.json()["items"]}
    assert rows[kept.id]["file_retained"] is True and rows[kept.id]["retained_until"] is not None
    assert rows[gone.id]["file_retained"] is False and rows[gone.id]["retained_until"] is None
