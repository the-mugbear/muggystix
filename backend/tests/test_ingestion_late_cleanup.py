"""A partial scan cleaned up LATE, and the cleanup's other edges (branch
review 2026-10-01: ingestion S1, S2, S3, M1, M2, M4, M5).

``test_ingestion_partial_scan.py`` covers the cleanup that runs at once (a
cancel, a failure).  These are the cases where time passes first:

* S1 — a worker is killed mid-import; ANOTHER import of the same hosts
  completes; only then is the dead job reaped or re-claimed.  The cleanup
  used to delete by ``scan_id`` ("first recorded by", which never moves), so
  the completed import lost the vulnerabilities, ports and scripts it had
  re-observed.
* S2 — with more than one worker the cleanup could delete a host a
  concurrent import was re-observing (two real connections).
* S3 — a heartbeat that finds the job is no longer its attempt's committed
  the pending batch before it raised (two real connections: the cancel comes
  from another connection, as it does from the API process).
* M1 — a claim that could not remove the dead attempt's scan went on to parse
  into a second one.
* M5 — a ``processing`` job cancelled while its worker was dead kept its scan.
* M2 — the web parsers' host history froze at the first heartbeat.
* M4 — the EyeWitness screenshots of a deleted partial scan stayed on disk.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.core.config import settings
from app.db import models
from app.db import session as session_module
from app.db.models_vulnerability import HostAttribute, Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.parsers.parser_utils import ScanHostObservations, announce_scan, record_hosts_in_scan
from app.services import ingestion_service as ingestion_module
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.ingestion_service import (
    IngestionService,
    ParseFailure,
    delete_partial_scan,
    project_import_lock,
    remove_scan_files,
)
from tests.conftest import engine
from tests.ingestion_job_harness import (
    all_scan_ids,
    host_ips,
    job_row,
    kill_worker,
    new_host_count,
    on_heartbeat,
    queue_file,
    run_file,
    run_next,
    run_until_killed,
    scans_of,
)
from tests.test_ingestion_partial_scan import THREE, nessus_file, nmap_file
from tests.two_connections import two_sessions  # noqa: F401  (fixture)


@pytest.fixture
def one_host_batches(monkeypatch):
    """Nessus commits (and heartbeats) after every host."""
    monkeypatch.setattr(settings, "NESSUS_COMMIT_BATCH_SIZE", 1)


def _age_heartbeat(db, job_id):
    db.query(models.IngestionJob).filter_by(id=job_id).update(
        {"last_heartbeat": datetime.now(timezone.utc) - timedelta(days=2)}
    )
    db.commit()


def _vulns(db, pid):
    db.expire_all()
    return (
        db.query(Vulnerability).join(models.Host, models.Host.id == Vulnerability.host_id)
        .filter(models.Host.project_id == pid).order_by(Vulnerability.id).all()
    )


def _ports(db, pid):
    db.expire_all()
    return (
        db.query(models.Port).join(models.Host, models.Host.id == models.Port.host_id)
        .filter(models.Host.project_id == pid).order_by(models.Port.id).all()
    )


# ---------------------------------------------------------------------------
# S1 — Nessus: killed, a second import completes, then the dead job ends
# ---------------------------------------------------------------------------

def _nessus_killed_then_a_second_import_completes(db, pid, tmp_path):
    """Job A commits its first host (10.40.0.1: port 443, plugin 42873) and is
    hard-killed.  Job B imports the same three hosts and completes."""
    svc = IngestionService()
    dead_job = queue_file(db, pid, nessus_file(tmp_path, THREE, name="first.nessus"))
    with on_heartbeat(svc, 2, kill_worker):
        run_until_killed(db, svc)
    dead = job_row(db, dead_job)
    assert dead.status == "processing" and isinstance(dead.in_progress_scan_id, int)
    dead_scan = dead.in_progress_scan_id
    assert [v.scan_id for v in _vulns(db, pid)] == [dead_scan]  # the committed batch is there

    second = run_file(db, pid, nessus_file(tmp_path, THREE, name="second.nessus"))
    assert second.status == "completed", second.error_message
    assert len(_vulns(db, pid)) == 3 and len(_ports(db, pid)) == 3
    return svc, dead_job, dead_scan, second.scan_id


def test_reaping_a_dead_nessus_job_keeps_what_a_later_import_re_observed(
    db_session, test_project, tmp_path, monkeypatch, one_host_batches,
):
    pid = test_project.id
    monkeypatch.setattr(settings, "INGESTION_MAX_RETRIES", 0)
    svc, dead_job, dead_scan, live_scan = _nessus_killed_then_a_second_import_completes(
        db_session, pid, tmp_path)

    _age_heartbeat(db_session, dead_job)
    assert svc.reap_orphaned_jobs() == 1

    job = job_row(db_session, dead_job)
    assert job.status == "failed" and job.in_progress_scan_id is None
    assert [s.id for s in scans_of(db_session, pid)] == [live_scan]
    assert host_ips(db_session, pid) == ["10.40.0.1", "10.40.0.2", "10.40.0.3"]
    # Everything the completed import reported is still there, and belongs to
    # a scan that exists: the row the dead attempt first recorded moved to
    # the scan that re-observed it.
    vulns = _vulns(db_session, pid)
    assert len(vulns) == 3
    assert {(v.scan_id, v.last_seen_scan_id) for v in vulns} == {(live_scan, live_scan)}
    ports = _ports(db_session, pid)
    assert len(ports) == 3 and {p.last_updated_scan_id for p in ports} == {live_scan}
    assert all(v.port_id is not None for v in vulns)
    # The host the dead attempt created is now introduced by the scan left.
    assert new_host_count(db_session, live_scan) == 3
    # Attribute rows (CASCADE on scan_id) the dead attempt first wrote and the
    # live scan re-observed are the live scan's now, not deleted with the scan.
    per_host = {}
    for attr in db_session.query(HostAttribute).all():
        per_host.setdefault(attr.host_id, set()).add(attr.scan_id)
    assert len(set(map(frozenset, per_host.values()))) <= 1
    assert all(scans == {live_scan} for scans in per_host.values())


def test_re_claiming_a_dead_nessus_job_keeps_the_later_import_and_adds_no_duplicates(
    db_session, test_project, tmp_path, one_host_batches,
):
    pid = test_project.id
    svc, dead_job, dead_scan, live_scan = _nessus_killed_then_a_second_import_completes(
        db_session, pid, tmp_path)

    _age_heartbeat(db_session, dead_job)
    assert svc.reap_orphaned_jobs() == 1
    assert job_row(db_session, dead_job).status == "queued"
    run_next(db_session, svc)

    job = job_row(db_session, dead_job)
    assert job.status == "completed", job.error_message
    assert job.scan_id not in (dead_scan, live_scan)
    assert [s.id for s in scans_of(db_session, pid)] == [live_scan, job.scan_id]
    vulns = _vulns(db_session, pid)
    assert len(vulns) == 3 and len({(v.host_id, v.plugin_id) for v in vulns}) == 3
    # First recorded by the import that completed; last seen by the retry.
    assert {(v.scan_id, v.last_seen_scan_id) for v in vulns} == {(live_scan, job.scan_id)}
    ports = _ports(db_session, pid)
    assert len(ports) == 3 and len({(p.host_id, p.port_number) for p in ports}) == 3
    assert host_ips(db_session, pid) == ["10.40.0.1", "10.40.0.2", "10.40.0.3"]


def test_a_dead_attempts_own_rows_still_go_when_no_other_scan_saw_them(
    db_session, test_project, tmp_path, monkeypatch, one_host_batches,
):
    """The other half of S1: with no second import, nothing is kept."""
    pid = test_project.id
    monkeypatch.setattr(settings, "INGESTION_MAX_RETRIES", 0)
    svc = IngestionService()
    dead_job = queue_file(db_session, pid, nessus_file(tmp_path, THREE))
    with on_heartbeat(svc, 2, kill_worker):
        run_until_killed(db_session, svc)
    assert len(_vulns(db_session, pid)) == 1

    _age_heartbeat(db_session, dead_job)
    assert svc.reap_orphaned_jobs() == 1

    assert all_scan_ids(db_session) == []
    assert host_ips(db_session, pid) == []
    assert _vulns(db_session, pid) == [] and _ports(db_session, pid) == []
    assert db_session.query(HostAttribute).count() == 0


# ---------------------------------------------------------------------------
# S1 — nmap: scripts and web rows
# ---------------------------------------------------------------------------

def _nmap_with_scripts(tmp_path, count, name):
    """``count`` hosts, each with ssh (a script) and https (a script and a
    certificate, which the parser also records as a web interface)."""
    hosts = "".join(
        f'<host><status state="up" reason="syn-ack"/>'
        f'<address addr="10.60.{i // 250}.{i % 250 + 1}" addrtype="ipv4"/><ports>'
        f'<port protocol="tcp" portid="22"><state state="open" reason="syn-ack"/>'
        f'<service name="ssh" method="probed" conf="10"/>'
        f'<script id="ssh-hostkey" output="2048 aa:bb (RSA)"/></port>'
        f'<port protocol="tcp" portid="443"><state state="open" reason="syn-ack"/>'
        f'<service name="https" method="probed" conf="10"/>'
        f'<script id="http-title" output="Welcome"/>'
        f'<script id="ssl-cert" output="Subject: commonName=h{i}">'
        f'<table key="subject"><elem key="commonName">h{i}</elem></table>'
        f'<table key="issuer"><elem key="commonName">ca</elem></table>'
        f'<table key="validity"><elem key="notAfter">2031-01-01T00:00:00</elem></table>'
        f'</script></port></ports></host>\n'
        for i in range(count)
    )
    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0"?>\n'
        '<nmaprun scanner="nmap" args="nmap -sV -sC" start="1700000000" version="7.94">\n'
        + hosts + '<runstats><finished time="1700000100"/></runstats></nmaprun>\n'
    )
    return path


def test_reaping_a_dead_nmap_job_keeps_the_scripts_and_web_rows_of_a_later_import(
    db_session, test_project, tmp_path, monkeypatch,
):
    """Scripts carry a CASCADING ``scan_id`` that a re-observation never
    moves: the first 100 hosts' scripts were first recorded by the dead scan
    and used to be deleted with it, although the completed import reported
    every one of them."""
    pid = test_project.id
    monkeypatch.setattr(settings, "INGESTION_MAX_RETRIES", 0)
    svc = IngestionService()
    dead_job = queue_file(db_session, pid, _nmap_with_scripts(tmp_path, 250, "first.xml"))
    with on_heartbeat(svc, 2, kill_worker):
        run_until_killed(db_session, svc)
    dead_scan = job_row(db_session, dead_job).in_progress_scan_id
    assert db_session.query(models.Script).filter_by(scan_id=dead_scan).count() >= 300

    second = run_file(db_session, pid, _nmap_with_scripts(tmp_path, 250, "second.xml"))
    assert second.status == "completed", second.error_message
    live_scan = second.scan_id
    assert db_session.query(models.Script).count() == 750
    assert db_session.query(models.WebInterface).filter_by(scan_id=live_scan).count() == 250

    _age_heartbeat(db_session, dead_job)
    assert svc.reap_orphaned_jobs() == 1
    db_session.expire_all()

    assert job_row(db_session, dead_job).status == "failed"
    assert all_scan_ids(db_session) == [live_scan]
    assert len(host_ips(db_session, pid)) == 250
    assert db_session.query(models.Port).count() == 500
    scripts = db_session.query(models.Script.scan_id).all()
    assert len(scripts) == 750 and {row[0] for row in scripts} == {live_scan}
    web = db_session.query(models.WebInterface.scan_id).all()
    assert len(web) == 250 and {row[0] for row in web} == {live_scan}
    assert new_host_count(db_session, live_scan) == 250


# ---------------------------------------------------------------------------
# S1 — the guards, one at a time
# ---------------------------------------------------------------------------

def _scan(db, pid, name):
    scan = models.Scan(filename=name, tool_name="nmap", scan_type="nmap", project_id=pid)
    db.add(scan)
    db.flush()
    return scan


def test_a_host_something_else_attached_to_without_history_stays(db_session, test_project):
    """History is not the only witness.  Each of these hosts was created by
    the partial scan and no other scan has a history row for it."""
    pid = test_project.id
    partial, other = _scan(db_session, pid, "partial.xml"), _scan(db_session, pid, "other.nessus")
    dedup = HostDeduplicationService(db_session)

    def host(ip):
        return dedup.find_or_create_host(ip, partial.id, {"state": "up"}, project_id=pid)

    alone = host("10.61.0.1")
    named = host("10.61.0.2")
    named.hostname, named.hostname_source = "typed-by-a-person.example.test", "operator"
    backfilled = host("10.61.0.3")
    db_session.add(Vulnerability(
        host_id=backfilled.id, title="SMB signing not required", plugin_id="smb_signing",
        severity=VulnerabilitySeverity.MEDIUM, source=VulnerabilitySource.NETEXEC, scan_id=None,
    ))
    re_observed = host("10.61.0.4")
    db_session.add(Vulnerability(
        host_id=re_observed.id, title="Weak ciphers", plugin_id="42873",
        severity=VulnerabilitySeverity.MEDIUM, source=VulnerabilitySource.NESSUS,
        scan_id=partial.id, last_seen_scan_id=other.id,
    ))
    port_updated = host("10.61.0.5")
    port = dedup.find_or_create_port(
        port_updated.id, partial.id, {"port_number": 443, "protocol": "tcp", "state": "open"})
    port.last_updated_scan_id = other.id
    web = host("10.61.0.6")
    db_session.add(models.WebInterface(
        scan_id=other.id, host_id=web.id, project_id=pid, source="httpx",
        url="http://10.61.0.6/", ip_address="10.61.0.6",
    ))
    db_session.commit()
    partial_id, other_id, alone_ip = partial.id, other.id, alone.ip_address
    port_id, re_observed_id = port.id, re_observed.id

    removed = delete_partial_scan(db_session, partial_id)
    db_session.commit()
    db_session.expire_all()

    assert removed["hosts"] == 1
    assert alone_ip not in host_ips(db_session, pid)
    assert host_ips(db_session, pid) == ["10.61.0.2", "10.61.0.3", "10.61.0.4", "10.61.0.5", "10.61.0.6"]
    # The port another scan last updated stays although no port history says so.
    assert db_session.get(models.Port, port_id) is not None
    # The observation another scan re-observed stays and is that scan's now.
    kept = db_session.query(Vulnerability).filter_by(host_id=re_observed_id).one()
    assert (kept.scan_id, kept.last_seen_scan_id) == (other_id, other_id)
    assert [s.id for s in scans_of(db_session, pid)] == [other_id]


# ---------------------------------------------------------------------------
# S3 — a heartbeat that no longer owns the job commits nothing
# ---------------------------------------------------------------------------

def _real_connections(monkeypatch, two_sessions):  # noqa: F811
    """The worker's and the API's sessions on their own connections, really
    committing — what ``db_session`` (one connection, savepoints) cannot show."""
    monkeypatch.setattr(session_module, "SessionLocal", two_sessions._factory)
    return two_sessions._factory


def test_a_cancel_from_another_connection_stops_the_batch_before_it_is_committed(
    two_sessions, monkeypatch, tmp_path,  # noqa: F811
):
    """The cancel arrives as a row another connection changed (the API
    process), not through the in-memory set.  The heartbeat that sees it must
    roll the pending batch back — it used to commit first, then raise."""
    factory = _real_connections(monkeypatch, two_sessions)
    project, _user = two_sessions.project()
    pid = project.id
    with factory() as db:
        job_id = queue_file(db, pid, nmap_file(tmp_path, 250, net="10.62"))

    worker = IngestionService()
    beats, committed = [], {}
    real = worker.update_heartbeat

    def count_hosts():
        with factory() as other:
            return other.query(models.Host).filter_by(project_id=pid).count()

    def hooked(db, job_id_, progress=None, **kwargs):
        beats.append(progress)
        if len(beats) == 2:
            # Another process: its own service object, its own connection.
            assert IngestionService().cancel_job(job_id_) is True
        try:
            return real(db, job_id_, progress, **kwargs)
        except ParseFailure:
            committed.setdefault("when_the_heartbeat_raised", count_hosts())
            raise

    worker.update_heartbeat = hooked  # type: ignore[method-assign]
    assert worker.poll_and_run_one() is True

    assert job_id not in worker._cancelled  # the cancel was never in this process's memory
    # Heartbeat 1 committed the first 100 hosts.  Heartbeat 2 found the job
    # cancelled: the second hundred, pending in its transaction, must not be
    # visible to anyone.
    assert committed == {"when_the_heartbeat_raised": 100}
    with factory() as db:
        job = db.get(models.IngestionJob, job_id)
        assert job.status == "failed" and job.error_message == "Cancelled by user"
        assert job.in_progress_scan_id is None
        assert db.query(models.Scan).filter_by(project_id=pid).count() == 0
        assert db.query(models.Host).filter_by(project_id=pid).count() == 0


# ---------------------------------------------------------------------------
# S2 — the cleanup and a concurrent import of the same project
# ---------------------------------------------------------------------------

def test_the_cleanup_waits_for_an_import_that_is_re_observing_the_host(two_sessions):  # noqa: F811
    """Two workers.  Import B has re-observed, in a transaction still open, a
    host the dead scan A created.  The cleanup of A must not decide "no other
    scan saw it" from a snapshot that cannot see B's row: it waits for the
    batch, then keeps the host."""
    project, _user = two_sessions.project()
    pid = project.id

    def build(db):
        dead, live = _scan(db, pid, "dead.xml"), _scan(db, pid, "live.xml")
        host = HostDeduplicationService(db).find_or_create_host(
            "10.63.0.1", dead.id, {"state": "up"}, project_id=pid)
        return dead.id, live.id, host.id

    dead_scan, live_scan, host_id = two_sessions.commit(build)

    importing, cleaning = two_sessions.a, two_sessions.b
    result = {}

    def clean_up():
        try:
            result["removed"] = delete_partial_scan(cleaning, dead_scan)
            cleaning.commit()
        except BaseException as exc:  # noqa: BLE001 — handed back to the test
            cleaning.rollback()
            result["error"] = exc

    with project_import_lock(importing, pid):
        importing.add(models.HostScanHistory(host_id=host_id, scan_id=live_scan, state_at_scan="up"))
        importing.flush()

        thread = threading.Thread(target=clean_up, daemon=True)
        thread.start()
        thread.join(1.5)
        assert thread.is_alive(), "the cleanup did not wait for the import's open batch"
        assert "removed" not in result

        importing.commit()
        thread.join(30)
    assert not thread.is_alive()
    assert "error" not in result, result.get("error")

    assert result["removed"]["hosts"] == 0
    with two_sessions.fresh() as db:
        assert db.get(models.Host, host_id) is not None
        seen_by = [row.scan_id for row in db.query(models.HostScanHistory).filter_by(host_id=host_id)]
        assert seen_by == [live_scan]
        assert db.get(models.Scan, dead_scan) is None


def test_imports_of_one_project_do_not_wait_for_each_other(two_sessions):  # noqa: F811
    """The import side of the lock is shared: two imports of a project hold
    it together."""
    project, _user = two_sessions.project()
    with project_import_lock(two_sessions.a, project.id), project_import_lock(two_sessions.b, project.id):
        two_sessions.a.execute(models.Scan.__table__.select().limit(1))
        done = two_sessions.race(
            lambda db: db.execute(models.Scan.__table__.select().limit(1)).fetchall() is not None,
            lambda db: db.execute(models.Scan.__table__.select().limit(1)).fetchall() is not None,
            timeout=10,
        )
    assert done == [True, True]


# ---------------------------------------------------------------------------
# M1 — a leftover that cannot be removed stops the import
# ---------------------------------------------------------------------------

def test_a_claim_that_cannot_remove_the_dead_attempts_scan_does_not_parse(
    db_session, test_project, tmp_path, monkeypatch,
):
    pid = test_project.id
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nmap_file(tmp_path, 250))
    with on_heartbeat(svc, 2, kill_worker):
        run_until_killed(db_session, svc)
    dead_scan = job_row(db_session, job_id).in_progress_scan_id
    _age_heartbeat(db_session, job_id)
    assert svc.reap_orphaned_jobs() == 1

    real = ingestion_module.delete_partial_scan
    monkeypatch.setattr(
        ingestion_module, "delete_partial_scan",
        lambda db, scan_id, **kw: (_ for _ in ()).throw(RuntimeError("simulated cleanup failure")),
    )
    run_next(db_session, svc)

    job = job_row(db_session, job_id)
    assert job.status == "failed"
    assert "earlier, interrupted attempt" in job.error_message
    # Still named on the row — it used to be overwritten by a second scan's id.
    assert job.in_progress_scan_id == dead_scan
    assert all_scan_ids(db_session) == [dead_scan]

    # A retry, once the cleanup works again, removes it and imports once.
    monkeypatch.setattr(ingestion_module, "delete_partial_scan", real)
    assert svc.requeue_job(job_id) == "requeued"
    run_next(db_session, svc)
    job = job_row(db_session, job_id)
    assert job.status == "completed", job.error_message
    assert all_scan_ids(db_session) == [job.scan_id] and job.scan_id != dead_scan
    assert new_host_count(db_session, job.scan_id) == 250


def test_a_parser_whose_partial_scan_cannot_be_removed_ends_the_fallback_chain(
    db_session, test_project, tmp_path, monkeypatch,
):
    """An ordinary parser error lets the dispatcher try the next format.  Not
    when the failed parser's committed scan is still there: the next parser's
    scan would replace the job's pointer to it."""
    from app.parsers.nmap_parser import NmapXMLParser

    pid = test_project.id
    stuck = {}

    def commit_a_scan_then_fail(self, file_path, filename, **kwargs):
        scan = models.Scan(filename=filename, tool_name="nmap", scan_type="nmap", project_id=pid)
        self.db.add(scan)
        self.db.flush()
        stuck["scan"] = scan.id
        announce_scan(self.db, scan)
        raise ValueError("not the XML this parser reads")

    monkeypatch.setattr(NmapXMLParser, "parse_file", commit_a_scan_then_fail)
    monkeypatch.setattr(
        ingestion_module, "delete_partial_scan",
        lambda db, scan_id, **kw: (_ for _ in ()).throw(RuntimeError("simulated cleanup failure")),
    )
    svc = IngestionService()
    attempts = []
    real_execute = svc._execute_parser
    monkeypatch.setattr(
        svc, "_execute_parser",
        lambda db, job, parser_class, description: (
            attempts.append(parser_class.__name__), real_execute(db, job, parser_class, description))[1],
    )
    job = run_file(db_session, pid, nmap_file(tmp_path, 3), svc=svc)

    assert job.status == "failed"
    assert attempts == ["NmapXMLParser"]
    assert "no other format was tried" in job.error_message
    assert job.in_progress_scan_id == stuck["scan"]
    assert all_scan_ids(db_session) == [stuck["scan"]]


# ---------------------------------------------------------------------------
# M5 — cancelling a processing job whose worker is dead
# ---------------------------------------------------------------------------

def test_cancelling_a_job_whose_worker_is_dead_removes_its_partial_scan(
    db_session, test_project, tmp_path,
):
    pid = test_project.id
    worker = IngestionService()
    job_id = queue_file(db_session, pid, nmap_file(tmp_path, 250))
    with on_heartbeat(worker, 2, kill_worker):
        run_until_killed(db_session, worker)
    assert len(all_scan_ids(db_session)) == 1 and len(host_ips(db_session, pid)) >= 100
    _age_heartbeat(db_session, job_id)

    db_session.commit()
    assert IngestionService().cancel_job(job_id) is True  # the API process

    job = job_row(db_session, job_id)
    assert job.status == "failed" and job.error_message == "Cancelled by user"
    assert job.in_progress_scan_id is None
    assert all_scan_ids(db_session) == []
    assert host_ips(db_session, pid) == []


def test_the_reaper_cleans_a_cancelled_job_whose_worker_died_after_the_cancel(
    db_session, test_project, tmp_path,
):
    """Cancelled while the lease still looked alive: the cancel leaves the
    cleanup to the attempt's next heartbeat.  It never comes; once the lease
    is past the reaper's window the reaper removes the scan."""
    pid = test_project.id
    worker = IngestionService()
    job_id = queue_file(db_session, pid, nmap_file(tmp_path, 250))
    with on_heartbeat(worker, 2, kill_worker):
        run_until_killed(db_session, worker)
    db_session.commit()
    assert IngestionService().cancel_job(job_id) is True
    assert job_row(db_session, job_id).status == "failed"
    assert len(all_scan_ids(db_session)) == 1  # a live attempt would clean this itself

    assert worker.reap_orphaned_jobs() == 0     # lease still inside the window: not touched
    assert len(all_scan_ids(db_session)) == 1

    _age_heartbeat(db_session, job_id)
    assert worker.reap_orphaned_jobs() == 0     # nothing to reap: the job is already failed…
    assert all_scan_ids(db_session) == []       # …but its leftover is gone
    assert host_ips(db_session, pid) == []
    assert job_row(db_session, job_id).in_progress_scan_id is None


# ---------------------------------------------------------------------------
# M2 — host history written ahead of every heartbeat
# ---------------------------------------------------------------------------

def _statements(fn) -> int:
    counter = {"n": 0}

    def _before(conn, cursor, statement, params, context, executemany):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _before)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return counter["n"]


def test_host_history_follows_the_file_and_only_re_reads_what_changed(db_session, test_project):
    pid = test_project.id
    scan = _scan(db_session, pid, "httpx.jsonl")
    hosts = [models.Host(ip_address=f"10.64.0.{i + 1}", state="up", project_id=pid) for i in range(3)]
    db_session.add_all(hosts)
    db_session.flush()
    first, second, third = hosts
    observed = ScanHostObservations()

    # Before the first heartbeat: the file has only named the address.
    observed.note(first, created=True, state=None)
    observed.note(second, created=False, state="up", hostname="b.example.test")
    record_hosts_in_scan(db_session, scan.id, observed)
    db_session.flush()

    # Later records say more about the first host, and name a third.
    observed.note(first, created=False, state="up", hostname="a.example.test")
    observed.note(second, created=False, state="up", hostname="b.example.test")  # nothing new
    observed.note(third, created=True, state="up")
    record_hosts_in_scan(db_session, scan.id, observed)
    db_session.flush()

    rows = {
        row.host_id: (row.host_created, row.state_at_scan, row.hostname_at_scan)
        for row in db_session.query(models.HostScanHistory).filter_by(scan_id=scan.id)
    }
    assert rows == {
        first.id: (True, "up", "a.example.test"),   # was frozen at (True, None, None)
        second.id: (False, "up", "b.example.test"),
        third.id: (True, "up", None),
    }

    # A beat with nothing noted since the last one reads nothing at all, and
    # one changed host costs one read however many hosts the file has named.
    assert _statements(lambda: record_hosts_in_scan(db_session, scan.id, observed)) == 0
    observed.note(third, created=False, state="up", hostname="c.example.test")
    assert observed.take_unwritten() == [
        type(next(iter(observed)))(host_id=third.id, created=True, state="up", hostname="c.example.test")
    ]


def test_a_rolled_back_record_is_not_left_marked_as_unwritten(db_session, test_project):
    pid = test_project.id
    host = models.Host(ip_address="10.64.1.1", state="up", project_id=pid)
    db_session.add(host)
    db_session.flush()
    observed = ScanHostObservations()
    saved = observed.checkpoint()
    observed.note(host, created=True, state="up")
    observed.restore(saved)
    assert observed.take_unwritten() == [] and len(observed) == 0


# ---------------------------------------------------------------------------
# M4 — the files of a deleted partial scan
# ---------------------------------------------------------------------------

def test_deleting_a_partial_scan_removes_its_screenshots_and_nothing_else(
    db_session, test_project, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    pid = test_project.id
    scan, neighbour = _scan(db_session, pid, "bundle.zip"), _scan(db_session, pid, "other.zip")
    db_session.commit()
    scan_id, neighbour_id = scan.id, neighbour.id

    root = tmp_path / "web_screenshots"
    (root / str(scan_id)).mkdir(parents=True)
    (root / str(scan_id) / "a.png").write_bytes(b"png")
    (root / f"report-{scan_id}.csv").write_text("Protocol,Port\n")
    (root / str(neighbour_id)).mkdir()
    (root / str(neighbour_id) / "b.png").write_bytes(b"png")
    (root / f"report-{neighbour_id}.json").write_text("{}")

    assert IngestionService()._discard_partial_scan(db_session, scan_id, why="test") is True

    assert all_scan_ids(db_session) == [neighbour_id]
    assert sorted(p.name for p in root.iterdir()) == [str(neighbour_id), f"report-{neighbour_id}.json"]
    assert (root / str(neighbour_id) / "b.png").exists()


def test_removing_a_scans_files_never_leaves_the_screenshots_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    root = tmp_path / "web_screenshots"
    root.mkdir()
    outside = tmp_path / "ingestion_queue"
    outside.mkdir()
    (outside / "upload.nessus").write_text("keep me")
    (root / "41").symlink_to(outside, target_is_directory=True)

    assert remove_scan_files(41) == 0
    assert (outside / "upload.nessus").read_text() == "keep me"
    assert remove_scan_files(True) == 0 and remove_scan_files("../ingestion_queue") == 0  # type: ignore[arg-type]
    assert (outside / "upload.nessus").exists()
