"""The ports a failed import created are found by a stamp on the port row
(``ports_v2.created_scan_id``), not by a list on the job row.

Nessus writes no ``PortScanHistory`` (and must not start to), so the stamp is
its only record of which ports an attempt added.  It is set where the row is
inserted, by every import path, and never moved; NULL — a row older than the
column, or one whose creating scan is gone — is never an attempt's own.

The Nessus runs are driven UNDER AN ACTIVE JOB (``ingestion_job_harness``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.core.config import settings
from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.db.models_host_tests import HostTest
from app.services import ingestion_service as ingestion_module
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.ingestion_service import IngestionService, delete_partial_scan
from tests.ingestion_job_harness import (
    all_scan_ids,
    host_ips,
    job_row,
    kill_worker,
    on_heartbeat,
    queue_file,
    run_file,
    run_next,
    run_until_killed,
    scans_of,
)
from tests.test_ingestion_nessus_created_ports import nessus_file

KNOWN = "10.43.0.1"   # exists before the import, with 22/tcp
HOSTS = [(KNOWN, [22, 443, 8443]), ("10.43.0.2", [443]), ("10.43.0.3", [443])]


@pytest.fixture
def one_host_batches(monkeypatch):
    """Nessus commits (and heartbeats) after every host."""
    monkeypatch.setattr(settings, "NESSUS_COMMIT_BATCH_SIZE", 1)


@pytest.fixture
def known_host(db_session, test_project):
    """A host the inventory already has, with one port that carries no stamp
    — what every row written before the column looks like."""
    host = models.Host(project_id=test_project.id, ip_address=KNOWN, state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(models.Port(host_id=host.id, port_number=22, protocol="tcp", state="open"))
    db_session.commit()
    return host.id


def _ports(db, host_id):
    db.expire_all()
    return {
        number: (port_id, stamp)
        for number, port_id, stamp in db.query(
            models.Port.port_number, models.Port.id, models.Port.created_scan_id,
        ).filter(models.Port.host_id == host_id)
    }


def _age_heartbeat(db, job_id):
    db.query(models.IngestionJob).filter_by(id=job_id).update(
        {"last_heartbeat": datetime.now(timezone.utc) - timedelta(days=2)}
    )
    db.commit()


def _scan(db, pid, name, tool="Nessus"):
    scan = models.Scan(project_id=pid, filename=name, tool_name=tool)
    db.add(scan)
    db.flush()
    return scan


def _stamped_port(db, host_id, number, scan_id):
    port = models.Port(
        host_id=host_id, port_number=number, protocol="tcp", state="open",
        created_scan_id=scan_id, last_updated_scan_id=scan_id,
    )
    db.add(port)
    db.flush()
    return port.id


# ---------------------------------------------------------------------------
# Every insert site stamps
# ---------------------------------------------------------------------------

def test_every_import_path_stamps_the_ports_it_inserts_and_only_those(
    db_session, test_project, tmp_path, known_host,
):
    pid = test_project.id

    # Nessus (vulnerability_service._get_or_create_port).
    job = run_file(db_session, pid, nessus_file(tmp_path, [(KNOWN, [22, 443])]))
    assert job.status == "completed", job.error_message
    ports = _ports(db_session, known_host)
    assert ports[443][1] == job.scan_id
    assert ports[22][1] is None                      # found, not created

    # The dedup service (every history-writing parser).
    nmap = _scan(db_session, pid, "sweep.xml", tool="nmap")
    dedup = HostDeduplicationService(db_session)
    created = dedup.find_or_create_port(known_host, nmap.id, {"port_number": 8080, "protocol": "tcp", "state": "open"})
    found = dedup.find_or_create_port(known_host, nmap.id, {"port_number": 443, "protocol": "tcp", "state": "open"})
    db_session.commit()
    assert created.created_scan_id == nmap.id
    assert found.created_scan_id == job.scan_id      # a re-observation never moves it

    # Masscan's set-based upsert: the INSERT stamps, the DO UPDATE does not.
    from app.parsers.masscan_parser import MasscanParser

    masscan = _scan(db_session, pid, "masscan.json", tool="masscan")
    MasscanParser(db_session)._upsert_ports_batch(masscan.id, [
        (known_host, {"port_number": 9090, "protocol": "tcp", "state": "open"}),
        (known_host, {"port_number": 8080, "protocol": "tcp", "state": "open"}),
        (known_host, {"port_number": 22, "protocol": "tcp", "state": "open"}),
    ])
    db_session.commit()
    ports = _ports(db_session, known_host)
    assert ports[9090][1] == masscan.id
    assert ports[8080][1] == nmap.id
    assert ports[22][1] is None


# ---------------------------------------------------------------------------
# A failed Nessus import
# ---------------------------------------------------------------------------

def test_a_failed_nessus_import_removes_exactly_the_ports_it_created(
    db_session, test_project, tmp_path, one_host_batches, known_host,
):
    pid = test_project.id
    before = _ports(db_session, known_host)
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, HOSTS))

    # Heartbeat 2: the known host's batch (22 found; 443 and 8443 created) is
    # committed.
    with on_heartbeat(svc, 2, lambda: svc.cancel_job(job_id)):
        run_next(db_session, svc)

    job = job_row(db_session, job_id)
    assert job.status == "failed" and job.error_message == "Cancelled by user"
    assert scans_of(db_session, pid) == []
    assert host_ips(db_session, pid) == [KNOWN]
    # The port that was there before is the same row, untouched.
    assert _ports(db_session, known_host) == before == {22: (before[22][0], None)}


def test_a_port_a_failed_attempt_created_and_a_later_scan_re_observed_is_kept(
    db_session, test_project, tmp_path, monkeypatch, one_host_batches, known_host,
):
    pid = test_project.id
    monkeypatch.setattr(settings, "INGESTION_MAX_RETRIES", 0)
    svc = IngestionService()
    dead_job = queue_file(db_session, pid, nessus_file(tmp_path, HOSTS, name="first.nessus"))
    with on_heartbeat(svc, 2, kill_worker):
        run_until_killed(db_session, svc)
    dead_scan = job_row(db_session, dead_job).in_progress_scan_id
    created = _ports(db_session, known_host)
    assert created[443][1] == created[8443][1] == dead_scan

    # A second import completes and reports 443 again — not 8443.
    second = run_file(db_session, pid, nessus_file(tmp_path, [(KNOWN, [443])], name="second.nessus"))
    assert second.status == "completed", second.error_message

    _age_heartbeat(db_session, dead_job)
    assert svc.reap_orphaned_jobs() == 1

    assert job_row(db_session, dead_job).status == "failed"
    assert all_scan_ids(db_session) == [second.scan_id]
    ports = _ports(db_session, known_host)
    # 443 is the row the dead attempt inserted, kept because another scan saw
    # it; the scan that inserted it is gone, so nothing names it as creator.
    assert sorted(ports) == [22, 443]
    assert ports[443] == (created[443][0], None)
    assert db_session.get(models.Port, created[443][0]).last_updated_scan_id == second.scan_id


def test_the_re_claim_of_a_dead_job_removes_the_previous_attempts_ports_by_their_stamp(
    db_session, test_project, tmp_path, one_host_batches, known_host,
):
    """A hard kill runs no cleanup; the stamp was committed with each port, so
    the attempt that re-claims the job finds them."""
    pid = test_project.id
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, HOSTS))
    with on_heartbeat(svc, 2, kill_worker):
        run_until_killed(db_session, svc)
    dead = _ports(db_session, known_host)
    dead_scan = job_row(db_session, job_id).in_progress_scan_id
    assert sorted(dead) == [22, 443, 8443]

    _age_heartbeat(db_session, job_id)
    assert svc.reap_orphaned_jobs() == 1
    assert job_row(db_session, job_id).status == "queued"
    between = {}

    def look():
        from app.db import session as session_module

        with session_module.SessionLocal() as db:
            between["ports"] = {row[0] for row in db.query(models.Port.id).filter_by(host_id=known_host)}
            between["scans"] = [row[0] for row in db.query(models.Scan.id)]

    with on_heartbeat(svc, 1, look):
        run_next(db_session, svc)

    # At the new attempt's first heartbeat the dead attempt's port ROWS and
    # scan were gone; the port from before it was not.
    assert dead[22][0] in between["ports"]
    assert not {dead[443][0], dead[8443][0]} & between["ports"]
    assert dead_scan not in between["scans"]
    job = job_row(db_session, job_id)
    assert job.status == "completed", job.error_message
    ports = _ports(db_session, known_host)
    assert ports[22] == dead[22]
    assert ports[443][1] == ports[8443][1] == job.scan_id
    assert all_scan_ids(db_session) == [job.scan_id]


def test_a_nessus_import_writes_no_list_of_ports_on_its_job_row(
    db_session, test_project, tmp_path, one_host_batches, known_host,
):
    """The job row carried a JSON list of every port created so far, rewritten
    with each batch.  The column and its writer are gone; the import still
    writes no port history."""
    assert "in_progress_created_port_ids" not in models.IngestionJob.__table__.c
    assert not hasattr(ingestion_module, "note_ports_created")

    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    engine = db_session.get_bind().engine
    hosts = [(f"10.43.1.{i}", [443, 8443]) for i in range(1, 13)]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        job = run_file(db_session, test_project.id, nessus_file(tmp_path, hosts))
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert job.status == "completed", job.error_message
    job_updates = [(s, p) for s, p in statements if s.lstrip().upper().startswith("UPDATE INGESTION_JOBS")]
    assert job_updates
    assert not [s for s, _p in job_updates if "port" in s.lower()]
    # No write to the job row grows with the import: none carries a list.
    assert not [
        p for _s, p in job_updates
        if any(isinstance(v, (list, tuple)) for v in (p.values() if isinstance(p, dict) else p))
    ]
    assert db_session.query(models.Port).filter_by(created_scan_id=job.scan_id).count() == 24
    assert db_session.query(models.PortScanHistory).filter_by(scan_id=job.scan_id).count() == 0


# ---------------------------------------------------------------------------
# The guards, on the stamp path
# ---------------------------------------------------------------------------

def test_a_port_with_no_stamp_is_never_deleted_by_the_cleanup(db_session, test_project, known_host):
    """NULL means "not this attempt's" — even when the failed scan is the only
    one that ever touched the port."""
    pid = test_project.id
    failed = _scan(db_session, pid, "failed.nessus")
    unstamped = models.Port(
        host_id=known_host, port_number=8001, protocol="tcp", state="open",
        last_updated_scan_id=failed.id,
    )
    db_session.add(unstamped)
    _stamped_port(db_session, known_host, 8002, failed.id)
    db_session.commit()
    before = _ports(db_session, known_host)

    removed = delete_partial_scan(db_session, failed.id)
    db_session.commit()

    assert removed["ports"] == 1
    after = _ports(db_session, known_host)
    assert sorted(after) == [22, 8001]
    assert after[22] == before[22] and after[8001][0] == before[8001][0]


def test_a_stamped_port_a_person_or_another_scan_holds_is_kept(db_session, test_project, test_user, known_host):
    pid = test_project.id
    failed, other = _scan(db_session, pid, "failed.nessus"), _scan(db_session, pid, "other.nessus")

    alone = _stamped_port(db_session, known_host, 9001, failed.id)
    noted = _stamped_port(db_session, known_host, 9002, failed.id)
    db_session.add(models.Annotation(port_id=noted, project_id=pid, user_id=test_user.id, body="look at this"))
    endpoint = _stamped_port(db_session, known_host, 9003, failed.id)
    finding = Finding(project_id=pid, title="Exposed admin console", severity="high", status="open", source="manual")
    db_session.add(finding)
    db_session.flush()
    db_session.add(FindingHost(finding_id=finding.id, host_id=known_host, port_id=endpoint))
    updated = _stamped_port(db_session, known_host, 9004, failed.id)
    db_session.get(models.Port, updated).last_updated_scan_id = other.id
    in_history = _stamped_port(db_session, known_host, 9005, failed.id)
    db_session.add(models.PortScanHistory(port_id=in_history, scan_id=other.id, state_at_scan="open"))
    # Another scan's port is not this attempt's, whoever updated it last.
    others = _stamped_port(db_session, known_host, 9006, other.id)
    db_session.get(models.Port, others).last_updated_scan_id = failed.id
    db_session.commit()

    removed = delete_partial_scan(db_session, failed.id)
    db_session.commit()

    assert removed["ports"] == 1
    kept = _ports(db_session, known_host)
    assert alone not in {port_id for port_id, _stamp in kept.values()}
    assert sorted(kept) == [22, 9002, 9003, 9004, 9005, 9006]
    # The scan that created them is gone, so nothing names them as its own.
    assert {kept[n][1] for n in (9002, 9003, 9004, 9005)} == {None}
    assert kept[9006][1] == other.id


def test_a_host_a_failed_nessus_attempt_created_stays_once_someone_has_a_test_on_it(
    db_session, test_project, tmp_path, monkeypatch, one_host_batches, known_host,
):
    pid = test_project.id
    monkeypatch.setattr(settings, "INGESTION_MAX_RETRIES", 0)
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, HOSTS))
    # Heartbeat 3: 10.43.0.2 (created by this attempt) is committed too.
    with on_heartbeat(svc, 3, kill_worker):
        run_until_killed(db_session, svc)
    assert host_ips(db_session, pid) == [KNOWN, "10.43.0.2"]
    worked_on = db_session.query(models.Host.id).filter_by(project_id=pid, ip_address="10.43.0.2").scalar()
    key = str(uuid.uuid4())
    db_session.add(HostTest(
        project_id=pid, host_id=worked_on, tool="curl", description="d", rationale="r",
        priority="medium", status="proposed", source="person", request_key=key, request_hash=key,
    ))
    db_session.commit()

    _age_heartbeat(db_session, job_id)
    assert svc.reap_orphaned_jobs() == 1

    assert job_row(db_session, job_id).status == "failed"
    assert all_scan_ids(db_session) == []
    # The host someone is working on stays; the known host loses only what
    # the attempt added to it.
    assert host_ips(db_session, pid) == [KNOWN, "10.43.0.2"]
    assert sorted(_ports(db_session, known_host)) == [22]
