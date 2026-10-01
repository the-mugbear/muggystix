"""Ports a failed NESSUS import added to hosts that already existed (review
2026-10-01 R2, the part left open).

``delete_partial_scan`` finds an attempt's ports through
``port_scan_history.port_created``.  Nessus writes no port history — and must
not start to: it would change the port counts on the Scans page, the dashboard
and the scan diff — so a cancelled, re-queued or killed Nessus import left the
ports it had added to PRE-EXISTING hosts.  The attempt now remembers the ids of
the ports it creates on its job row (``in_progress_created_port_ids``), in the
transaction that commits each batch, and the cleanup deletes them under the
same guards as history-found ports.  Cleanup only: no page or count reads it.

Every test runs the import UNDER AN ACTIVE JOB (``ingestion_job_harness``).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import worker_loop
from app.core.config import settings
from app.db import models
from app.services.ingestion_service import IngestionService, delete_recorded_ports
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

KNOWN = "10.41.0.1"   # exists before the Nessus import, with 22/tcp


def _item(port: int) -> str:
    return (
        f'<ReportItem port="{port}" svc_name="www" protocol="tcp" severity="2" pluginID="4{port}" '
        f'pluginName="Issue on {port}"><description>d</description>'
        '<risk_factor>Medium</risk_factor></ReportItem>'
    )


def nessus_file(tmp_path, hosts, name="ports.nessus"):
    """``hosts``: ``[(ip, [port, …]), …]``."""
    body = "".join(
        f'<ReportHost name="{ip}"><HostProperties><tag name="host-ip">{ip}</tag></HostProperties>'
        + "".join(_item(p) for p in ports) + '</ReportHost>\n'
        for ip, ports in hosts
    )
    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0" ?>\n<NessusClientData_v2>\n<Report name="ports">\n'
        + body + '</Report>\n</NessusClientData_v2>\n'
    )
    return path


# The known host first (it gains 443 and is seen again on 22), then two more
# so there is a heartbeat AFTER the known host's batch is committed.
HOSTS = [(KNOWN, [22, 443]), ("10.41.0.2", [443]), ("10.41.0.3", [443])]


@pytest.fixture
def one_host_batches(monkeypatch):
    """Nessus commits (and heartbeats) after every host."""
    monkeypatch.setattr(settings, "NESSUS_COMMIT_BATCH_SIZE", 1)


@pytest.fixture
def known_host(db_session, test_project):
    """A host the inventory already has, with one open port, from nothing in
    particular (no scan history) — what an older import left."""
    host = models.Host(project_id=test_project.id, ip_address=KNOWN, state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(models.Port(host_id=host.id, port_number=22, protocol="tcp", state="open"))
    db_session.commit()
    return host.id


def _ports(db, host_id):
    db.expire_all()
    return sorted(
        row[0] for row in db.query(models.Port.port_number).filter(models.Port.host_id == host_id)
    )


def _during_the_run(read):
    """Read the database from INSIDE a heartbeat hook.  The test session must
    not be used while the worker's session is mid-transaction (the harness
    rule), so this opens a session of its own and closes it before the worker
    goes on."""
    from app.db import session as session_module

    with session_module.SessionLocal() as db:
        return read(db)


def _port_id(db, host_id, number):
    return db.query(models.Port.id).filter_by(host_id=host_id, port_number=number).scalar()


def _recorded(db, job_id):
    return db.query(models.IngestionJob.in_progress_created_port_ids).filter_by(id=job_id).scalar()


def _age_heartbeat(db, job_id):
    db.query(models.IngestionJob).filter_by(id=job_id).update(
        {"last_heartbeat": datetime.now(timezone.utc) - timedelta(days=2)}
    )
    db.commit()


def test_a_cancelled_nessus_import_removes_the_port_it_added_to_a_known_host(
    db_session, test_project, tmp_path, one_host_batches, known_host,
):
    pid = test_project.id
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, HOSTS))
    seen = {}

    def cancel():
        # Heartbeat 2: the known host's batch is committed — the port is
        # really there, and the job row names it.
        seen.update(_during_the_run(lambda db: {
            "ports": [_port_id(db, known_host, n) is not None for n in (22, 443)],
            "added": _port_id(db, known_host, 443),
            "found": _port_id(db, known_host, 22),
            "recorded": _recorded(db, job_id),
        }))
        svc.cancel_job(job_id)

    with on_heartbeat(svc, 2, cancel):
        run_next(db_session, svc)

    assert seen["ports"] == [True, True]
    added =db_session.query(models.Port.id).filter_by(host_id=known_host, port_number=443).all()
    assert added == []                                   # gone
    # The row named the port the attempt CREATED, never the one it found.
    assert seen["added"] in seen["recorded"] and seen["found"] not in seen["recorded"]
    job = job_row(db_session, job_id)
    assert job.status == "failed" and job.error_message == "Cancelled by user"
    assert scans_of(db_session, pid) == []
    # The host and the port it had before stay; the port the attempt merely
    # FOUND (22) was never the attempt's to delete.
    assert host_ips(db_session, pid) == [KNOWN]
    assert _ports(db_session, known_host) == [22]
    assert job.in_progress_created_port_ids is None


def test_a_nessus_import_handed_back_at_shutdown_removes_the_port_then_imports_once(
    db_session, test_project, tmp_path, monkeypatch, one_host_batches, known_host,
):
    pid = test_project.id
    stopping = {"on": False}
    monkeypatch.setattr(worker_loop, "is_shutting_down", lambda: stopping["on"])
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, HOSTS))

    with on_heartbeat(svc, 2, lambda: stopping.update(on=True)):
        run_next(db_session, svc)

    job = job_row(db_session, job_id)
    assert job.status == "queued", (job.status, job.error_message)
    assert _ports(db_session, known_host) == [22]
    assert host_ips(db_session, pid) == [KNOWN]
    assert job.in_progress_created_port_ids is None

    stopping["on"] = False
    run_next(db_session, svc)
    job = job_row(db_session, job_id)
    assert job.status == "completed", job.error_message
    assert _ports(db_session, known_host) == [22, 443]


def test_a_killed_nessus_import_is_cleaned_up_by_the_attempt_that_re_claims_it(
    db_session, test_project, tmp_path, one_host_batches, known_host,
):
    """A hard kill runs no cleanup.  The port list was committed WITH the
    batch, so the next claim knows what the dead attempt added."""
    pid = test_project.id
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, HOSTS))

    with on_heartbeat(svc, 2, kill_worker):
        run_until_killed(db_session, svc)

    dead = job_row(db_session, job_id)
    assert dead.status == "processing"
    assert _ports(db_session, known_host) == [22, 443]          # committed, and…
    dead_port = db_session.query(models.Port.id).filter_by(host_id=known_host, port_number=443).scalar()
    assert dead_port in dead.in_progress_created_port_ids       # …named on the job row
    dead_scan = dead.in_progress_scan_id

    # The re-claim deletes the dead attempt's leftovers before parsing; stop
    # it at its first heartbeat to look at the inventory in between.
    _age_heartbeat(db_session, job_id)
    assert svc.reap_orphaned_jobs() == 1
    between = {}

    def look():
        between.update(_during_the_run(lambda db: {
            "scans": [row[0] for row in db.query(models.Scan.id).all()],
            "dead_port_exists": db.query(models.Port.id).filter_by(id=dead_port).first() is not None,
        }))

    with on_heartbeat(svc, 1, look):
        run_next(db_session, svc)

    assert dead_scan not in between["scans"]
    assert between["dead_port_exists"] is False
    job = job_row(db_session, job_id)
    assert job.status == "completed", job.error_message
    assert all_scan_ids(db_session) == [job.scan_id]
    assert _ports(db_session, known_host) == [22, 443]
    assert host_ips(db_session, pid) == [KNOWN, "10.41.0.2", "10.41.0.3"]
    assert job.in_progress_created_port_ids is None


def test_a_killed_job_reaped_to_failed_loses_the_ports_too(
    db_session, test_project, tmp_path, monkeypatch, one_host_batches, known_host,
):
    pid = test_project.id
    monkeypatch.setattr(settings, "INGESTION_MAX_RETRIES", 0)
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, HOSTS))
    with on_heartbeat(svc, 2, kill_worker):
        run_until_killed(db_session, svc)
    assert _ports(db_session, known_host) == [22, 443]

    _age_heartbeat(db_session, job_id)
    svc.reap_orphaned_jobs()

    job = job_row(db_session, job_id)
    assert job.status == "failed"
    assert all_scan_ids(db_session) == []
    assert _ports(db_session, known_host) == [22]
    assert host_ips(db_session, pid) == [KNOWN]
    assert job.in_progress_created_port_ids is None


def test_a_successful_nessus_import_keeps_its_ports_and_clears_the_record(
    db_session, test_project, tmp_path, one_host_batches, known_host,
):
    pid = test_project.id
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, HOSTS))
    recorded = []
    with on_heartbeat(svc, 3, lambda: recorded.append(_during_the_run(lambda db: _recorded(db, job_id)))):
        run_next(db_session, svc)

    job = job_row(db_session, job_id)
    assert job.status == "completed", job.error_message
    # While it ran the row named what it had created (443 on each of the three
    # hosts by the third heartbeat — never the 22 it found)…
    assert len(recorded[0]) == 3
    # …and nothing once it finished.
    assert job.in_progress_created_port_ids is None and job.in_progress_scan_id is None
    assert _ports(db_session, known_host) == [22, 443]
    # No visible count changed: Nessus still writes no port history.
    assert db_session.query(models.PortScanHistory).filter_by(scan_id=job.scan_id).count() == 0


def test_the_last_partial_batch_is_recorded_with_the_commit_that_writes_it(
    db_session, test_project, tmp_path, known_host, monkeypatch,
):
    """Default-sized batches: a three-host file never reaches a batch
    heartbeat, so its ports are committed by the final commit — which must
    carry the record too (a failure after it still has to find them)."""
    from app.services import ingestion_service as mod

    pid = test_project.id
    stamped = []
    real = mod.note_ports_created
    monkeypatch.setattr(mod, "note_ports_created", lambda db, ids: (stamped.append(list(ids)), real(db, ids))[1])
    job = run_file(db_session, pid, nessus_file(tmp_path, HOSTS))
    assert job.status == "completed", job.error_message
    assert len(stamped) == 1 and len(stamped[0]) == 3
    assert job.in_progress_created_port_ids is None


def test_a_record_whose_scan_is_already_gone_is_still_cleaned_up_at_the_next_claim(
    db_session, test_project, tmp_path, known_host,
):
    """Someone deleted the dead attempt's partial scan by hand: the FK nulled
    the job's scan pointer, the port record remained.  The next claim removes
    the recorded ports nothing refers to and drops the record."""
    pid = test_project.id
    leftover = models.Port(host_id=known_host, port_number=8443, protocol="tcp", state="open")
    db_session.add(leftover)
    db_session.flush()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, [("10.41.0.9", [443])]))
    db_session.query(models.IngestionJob).filter_by(id=job_id).update(
        {"in_progress_created_port_ids": [leftover.id]}
    )
    db_session.commit()

    run_next(db_session)

    job = job_row(db_session, job_id)
    assert job.status == "completed", job.error_message
    assert _ports(db_session, known_host) == [22]
    assert job.in_progress_created_port_ids is None


def test_a_recorded_port_another_scan_has_since_seen_or_that_is_referred_to_stays(
    db_session, test_project, known_host,
):
    """The guards are the history path's: a port another scan observed, or
    that a note / an observation from another scan refers to, is not the
    failed attempt's alone any more."""
    pid = test_project.id
    failed = models.Scan(project_id=pid, filename="failed.nessus", tool_name="Nessus")
    other = models.Scan(project_id=pid, filename="sweep.xml", tool_name="nmap")
    db_session.add_all([failed, other])
    db_session.flush()

    def port(number):
        p = models.Port(host_id=known_host, port_number=number, protocol="tcp", state="open")
        db_session.add(p)
        db_session.flush()
        return p.id

    alone, seen_again, pre_existing = port(8001), port(8002), db_session.query(models.Port.id).filter_by(
        host_id=known_host, port_number=22).scalar()
    db_session.add(models.PortScanHistory(port_id=seen_again, scan_id=other.id, state_at_scan="open"))
    db_session.commit()

    # 22 is not in the record (the attempt found it), so it cannot be touched;
    # a stale or nonsense id matches nothing.
    removed = delete_recorded_ports(db_session, [alone, seen_again, 987654321, "x", None], failed.id)
    db_session.commit()

    assert removed == 1
    assert _ports(db_session, known_host) == [22, 8002]
    assert db_session.query(models.Port.id).filter_by(id=pre_existing).first() is not None
