"""What an import that does not finish leaves behind (review 2026-10-01:
C1, R1, R2, R3, R6).

Every test here runs the import UNDER AN ACTIVE JOB (``ingestion_job_harness``)
— the heartbeat really commits and really raises on cancel / shutdown.  Before
this file nothing in the suite did, so none of these were visible:

* C1 — Nessus turned cancel, timeout and shutdown into ``success: False``; the
  job failed instead of being handed back, and the committed batches stayed in
  a scan no job pointed at.
* R1 — a hard-killed worker's partial scan was known only to the dead process;
  the re-queued attempt imported into a second scan.
* R2 — deleting a partial scan left the hosts it had created, so the retry
  reported "0 new hosts" for hosts this very file introduced.
* R3 — Nessus kept detached rows in the dedup caches across its batch commit.
* R6 — twelve parsers never heartbeated, so a cancel never reached them.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import worker_loop
from app.core.config import settings
from app.db import models
from app.db.models_vulnerability import Vulnerability
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.ingestion_service import (
    IngestionService,
    ParseFailure,
    delete_partial_scan,
)
from tests.ingestion_job_harness import (
    active_job,
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


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

_NESSUS_HOST = (
    '<ReportHost name="{ip}"><HostProperties>'
    '<tag name="host-ip">{ip}</tag><tag name="host-fqdn">{fqdn}</tag>'
    '</HostProperties>'
    '<ReportItem port="443" svc_name="www" protocol="tcp" severity="2" pluginID="42873" '
    'pluginName="SSL Medium Strength Cipher Suites Supported">'
    '<description>Weak ciphers.</description><risk_factor>Medium</risk_factor>'
    '</ReportItem></ReportHost>\n'
)


def nessus_file(tmp_path, hosts, name="batch.nessus", truncate_after=None):
    """``hosts`` is a list of ``(ip, fqdn)``.  ``truncate_after=N`` cuts the
    file in the middle of host N+1."""
    body = "".join(_NESSUS_HOST.format(ip=ip, fqdn=fqdn) for ip, fqdn in hosts)
    text = (
        '<?xml version="1.0" ?>\n<NessusClientData_v2>\n<Report name="batch">\n'
        + body + '</Report>\n</NessusClientData_v2>\n'
    )
    if truncate_after is not None:
        cut = 0
        for _ in range(truncate_after):
            cut = text.index("</ReportHost>", cut) + len("</ReportHost>")
        text = text[:cut] + '\n<ReportHost name="10.40.9.9"><HostProperties><tag name="host-ip">10.40.9.9</tag>'
    path = tmp_path / name
    path.write_text(text)
    return path


def nmap_file(tmp_path, count, name="sweep.xml", net="10.20"):
    hosts = "".join(
        f'<host><status state="up" reason="syn-ack"/>'
        f'<address addr="{net}.{i // 250}.{i % 250 + 1}" addrtype="ipv4"/>'
        f'<ports><port protocol="tcp" portid="22"><state state="open" reason="syn-ack"/>'
        f'<service name="ssh" method="probed" conf="10"/></port></ports></host>\n'
        for i in range(count)
    )
    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0"?>\n'
        '<nmaprun scanner="nmap" args="nmap -sV" start="1700000000" version="7.94">\n'
        + hosts + '<runstats><finished time="1700000100"/></runstats></nmaprun>\n'
    )
    return path


THREE = [("10.40.0.1", "a.example.test"), ("10.40.0.2", "b.example.test"), ("10.40.0.3", "c.example.test")]


@pytest.fixture
def one_host_batches(monkeypatch):
    """Nessus commits (and heartbeats) after every host."""
    monkeypatch.setattr(settings, "NESSUS_COMMIT_BATCH_SIZE", 1)


def _age_heartbeat(db, job_id):
    db.query(models.IngestionJob).filter_by(id=job_id).update(
        {"last_heartbeat": datetime.now(timezone.utc) - timedelta(days=2)}
    )
    db.commit()


# ---------------------------------------------------------------------------
# C1 — Nessus: cancel, shutdown, truncation
# ---------------------------------------------------------------------------

def test_nessus_shutdown_hands_the_job_back_and_leaves_nothing(
    db_session, test_project, tmp_path, monkeypatch, one_host_batches,
):
    """A worker restart mid-import re-queues the job (it used to FAIL it), and
    the batch already committed is gone — scan, host and observation — so the
    next worker's import is the only one."""
    pid = test_project.id
    stopping = {"on": False}
    monkeypatch.setattr(worker_loop, "is_shutting_down", lambda: stopping["on"])
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, THREE))

    # Heartbeat 2 comes after host 2: host 1 is committed by then.
    with on_heartbeat(svc, 2, lambda: stopping.update(on=True)) as beats:
        run_next(db_session, svc)

    assert len(beats) == 2
    job = job_row(db_session, job_id)
    assert job.status == "queued", (job.status, job.error_message)
    assert job.started_at is None and job.retry_count == 0
    assert scans_of(db_session, pid) == []
    assert host_ips(db_session, pid) == []
    assert db_session.query(Vulnerability).count() == 0

    stopping["on"] = False
    run_next(db_session, svc)
    job = job_row(db_session, job_id)
    assert job.status == "completed", job.error_message
    assert [s.id for s in scans_of(db_session, pid)] == [job.scan_id]
    assert host_ips(db_session, pid) == ["10.40.0.1", "10.40.0.2", "10.40.0.3"]
    # The retry introduced all three — not "2 new" because a dead attempt's
    # host was still lying around (R2).
    assert new_host_count(db_session, job.scan_id) == 3


def test_nessus_cancel_fails_the_job_and_removes_the_partial_scan(
    db_session, test_project, tmp_path, one_host_batches,
):
    pid = test_project.id
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, THREE))

    with on_heartbeat(svc, 2, lambda: svc.cancel_job(job_id)):
        run_next(db_session, svc)

    job = job_row(db_session, job_id)
    assert job.status == "failed"
    assert scans_of(db_session, pid) == []
    assert host_ips(db_session, pid) == []
    assert db_session.query(Vulnerability).count() == 0
    assert job.scan_id is None
    # The cancel itself, not "Failed to process Nessus file: Job cancelled".
    assert job.error_message == "Cancelled by user"


def test_a_truncated_nessus_file_fails_and_keeps_nothing(
    db_session, test_project, tmp_path, one_host_batches,
):
    """Decision (C1): a truncated export is still a FAILED import, and — like
    every other failed import — it leaves no scan behind.  It used to keep the
    hosts read before the break in a scan the failed job did not name, and the
    re-upload the message asks for then made a second scan."""
    pid = test_project.id
    job = run_file(db_session, pid, nessus_file(tmp_path, THREE, truncate_after=2))

    assert job.status == "failed"
    assert scans_of(db_session, pid) == []
    assert host_ips(db_session, pid) == []
    assert "truncated" in (job.error_message or "").lower()
    assert "Nothing from this file was kept" in job.error_message


def test_a_cancelled_import_keeps_what_an_earlier_scan_recorded(
    db_session, test_project, tmp_path, one_host_batches,
):
    """R2 removes only what the failed attempt CREATED: a host an earlier scan
    introduced stays, with that scan's observation."""
    pid = test_project.id
    first = run_file(db_session, pid, nessus_file(tmp_path, THREE[:1], name="first.nessus"))
    assert first.status == "completed", first.error_message
    first_scan = first.scan_id

    svc = IngestionService()
    job_id = queue_file(db_session, pid, nessus_file(tmp_path, THREE, name="second.nessus"))
    with on_heartbeat(svc, 3, lambda: svc.cancel_job(job_id)):
        run_next(db_session, svc)

    assert job_row(db_session, job_id).status == "failed"
    assert [s.id for s in scans_of(db_session, pid)] == [first_scan]
    assert host_ips(db_session, pid) == ["10.40.0.1"]
    rows = db_session.query(Vulnerability).all()
    assert [(v.plugin_id, v.scan_id) for v in rows] == [("42873", first_scan)]


# ---------------------------------------------------------------------------
# R1 / R2 — a worker that dies
# ---------------------------------------------------------------------------

def test_a_killed_nmap_import_is_replaced_not_duplicated(db_session, test_project, tmp_path):
    """The worker dies after the first 100 hosts were committed.  The reaper
    re-queues the job; the new attempt deletes the dead one's scan and what it
    created, then imports the file once: one scan, 250 new hosts.  (Before:
    two scans, and "150 new hosts" on the one the job named.)"""
    pid = test_project.id
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nmap_file(tmp_path, 250))

    with on_heartbeat(svc, 2, kill_worker):
        run_until_killed(db_session, svc)

    dead = job_row(db_session, job_id)
    assert dead.status == "processing"
    assert len(host_ips(db_session, pid)) >= 100  # the committed part is really there
    dead_scans = all_scan_ids(db_session)
    assert len(dead_scans) == 1 and dead.in_progress_scan_id == dead_scans[0]

    _age_heartbeat(db_session, job_id)
    assert svc.reap_orphaned_jobs() == 1
    assert job_row(db_session, job_id).status == "queued"
    run_next(db_session, svc)

    job = job_row(db_session, job_id)
    assert job.status == "completed", job.error_message
    assert all_scan_ids(db_session) == [job.scan_id]
    assert job.scan_id != dead_scans[0]
    assert job.in_progress_scan_id is None
    assert len(host_ips(db_session, pid)) == 250
    assert new_host_count(db_session, job.scan_id) == 250


def test_a_job_reaped_to_failed_loses_its_partial_scan(db_session, test_project, tmp_path, monkeypatch):
    """Out of retries: nothing will claim the job again, so the reaper itself
    removes what the dead attempt committed."""
    pid = test_project.id
    monkeypatch.setattr(settings, "INGESTION_MAX_RETRIES", 0)
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nmap_file(tmp_path, 250))
    with on_heartbeat(svc, 2, kill_worker):
        run_until_killed(db_session, svc)
    assert len(all_scan_ids(db_session)) == 1

    _age_heartbeat(db_session, job_id)
    assert svc.reap_orphaned_jobs() == 1

    job = job_row(db_session, job_id)
    assert job.status == "failed"
    assert job.in_progress_scan_id is None
    assert all_scan_ids(db_session) == []
    assert host_ips(db_session, pid) == []


def _killed_right_after_the_scan_commit(monkeypatch):
    """The worker dies in the instant after the scan row's first commit."""
    from app.services import ingestion_service as mod

    real = mod.note_scan_created

    def note_then_die(db, scan_id):
        real(db, scan_id)
        kill_worker()

    monkeypatch.setattr(mod, "note_scan_created", note_then_die)
    return lambda: monkeypatch.setattr(mod, "note_scan_created", real)


_GNMAP = (
    "# Nmap 7.94 scan initiated Mon Jul 15 10:30:01 2024 as: nmap -oG out.gnmap 10.41.0.1\n"
    "Host: 10.41.0.1 (gw.example.test)\tStatus: Up\n"
    "Host: 10.41.0.1 (gw.example.test)\tPorts: 22/open/tcp//ssh///\n"
    "# Nmap done at Mon Jul 15 10:30:25 2024 -- 1 IP address (1 host up) scanned in 24.12 seconds\n"
)
_MASSCAN_LIST = "#masscan\nopen tcp 80 10.42.0.1 1700000000\nopen tcp 443 10.42.0.1 1700000001\n# end\n"
_NETEXEC = (
    "SMB         10.43.0.1       445    DC01             [*] Windows Server 2019 Build 17763 x64 "
    "(name:DC01) (domain:corp.local) (signing:True) (SMBv1:False)\n"
)
_DNS_CSV = "record_type,name,address\nA,www.example.test,10.44.0.1\n"
_HTTPX = json.dumps({"url": "http://10.45.0.1:80/", "host": "10.45.0.1", "port": "80",
                     "status_code": 200, "tech": ["Nginx"], "webserver": "nginx"}) + "\n"


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


@pytest.mark.parametrize("build", [
    pytest.param(lambda t: nmap_file(t, 2), id="nmap"),
    pytest.param(lambda t: _write(t, "out.gnmap", _GNMAP), id="gnmap"),
    pytest.param(lambda t: _write(t, "masscan-out.txt", _MASSCAN_LIST), id="masscan"),
    pytest.param(lambda t: _write(t, "nxc-smb.txt", _NETEXEC), id="netexec"),
    pytest.param(lambda t: nessus_file(t, THREE), id="nessus"),
    pytest.param(lambda t: _write(t, "dns-records.csv", _DNS_CSV), id="dns"),
    pytest.param(lambda t: _write(t, "httpx-results.jsonl", _HTTPX), id="httpx"),
])
def test_the_scan_is_on_the_job_row_from_its_first_commit(
    db_session, test_project, tmp_path, monkeypatch, build,
):
    """R1 — there is no moment at which a committed scan exists that its job
    does not name: killed right after the scan row's first commit, the job
    row already carries it, and the next claim removes it before importing."""
    pid = test_project.id
    svc = IngestionService()
    job_id = queue_file(db_session, pid, build(tmp_path))

    restore = _killed_right_after_the_scan_commit(monkeypatch)
    run_until_killed(db_session, svc)

    dead = job_row(db_session, job_id)
    dead_scans = all_scan_ids(db_session)
    assert dead.status == "processing"
    assert len(dead_scans) == 1, "the scan row was not committed with its job pointer"
    assert dead.in_progress_scan_id == dead_scans[0]

    restore()
    _age_heartbeat(db_session, job_id)
    assert svc.reap_orphaned_jobs() == 1
    run_next(db_session, svc)

    job = job_row(db_session, job_id)
    assert job.status == "completed", job.error_message
    assert all_scan_ids(db_session) == [job.scan_id]
    assert job.in_progress_scan_id is None


def test_a_cancelled_waiting_job_drops_the_dead_attempts_scan(db_session, test_project, tmp_path):
    """Re-queued by the reaper, then cancelled before any worker claimed it:
    nothing would ever parse it again, so the cancel removes the leftover."""
    pid = test_project.id
    svc = IngestionService()
    job_id = queue_file(db_session, pid, nmap_file(tmp_path, 250))
    with on_heartbeat(svc, 2, kill_worker):
        run_until_killed(db_session, svc)
    _age_heartbeat(db_session, job_id)
    assert svc.reap_orphaned_jobs() == 1
    assert len(all_scan_ids(db_session)) == 1

    db_session.commit()
    assert svc.cancel_job(job_id) is True

    assert job_row(db_session, job_id).status == "failed"
    assert all_scan_ids(db_session) == []
    assert host_ips(db_session, pid) == []


# ---------------------------------------------------------------------------
# R2 — delete_partial_scan: what goes, what stays
# ---------------------------------------------------------------------------

def _scan(db, pid, name):
    scan = models.Scan(filename=name, tool_name="nmap", scan_type="nmap", project_id=pid)
    db.add(scan)
    db.flush()
    return scan


def test_delete_partial_scan_removes_only_what_that_scan_created(db_session, test_project, test_user):
    pid = test_project.id
    first, partial = _scan(db_session, pid, "first.xml"), _scan(db_session, pid, "partial.xml")
    port = lambda n: {"port_number": n, "protocol": "tcp", "state": "open"}  # noqa: E731

    dedup = HostDeduplicationService(db_session)
    known = dedup.find_or_create_host("10.50.0.1", first.id, {"state": "up"}, project_id=pid)
    dedup.find_or_create_port(known.id, first.id, port(22))
    db_session.commit()

    dedup = HostDeduplicationService(db_session)
    known = dedup.find_or_create_host("10.50.0.1", partial.id, {"state": "up"}, project_id=pid)
    dedup.find_or_create_port(known.id, partial.id, port(22))    # re-observed
    dedup.find_or_create_port(known.id, partial.id, port(8443))  # introduced by the partial scan
    fresh = dedup.find_or_create_host("10.50.0.2", partial.id, {"state": "up"}, project_id=pid)
    dedup.find_or_create_port(fresh.id, partial.id, port(80))
    worked = dedup.find_or_create_host("10.50.0.3", partial.id, {"state": "up"}, project_id=pid)
    db_session.add(models.HostFollow(host_id=worked.id, user_id=test_user.id))
    db_session.commit()
    partial_id, first_id, known_id = partial.id, first.id, known.id

    removed = delete_partial_scan(db_session, partial_id)
    db_session.commit()
    db_session.expire_all()

    assert removed["hosts"] == 1 and removed["ports"] == 1
    assert [s.id for s in scans_of(db_session, pid)] == [first_id]
    # .2 was created by the partial scan and nobody touched it; .3 was
    # created by it too, but someone is following it: work is never deleted.
    assert host_ips(db_session, pid) == ["10.50.0.1", "10.50.0.3"]
    ports = db_session.query(models.Port.port_number).filter(models.Port.host_id == known_id).all()
    assert sorted(p[0] for p in ports) == [22]


def test_a_host_with_a_remediation_note_is_kept(db_session, test_project, test_user):
    """Review 2026-10-07: a remediation note needs only a host, and its
    foreign key cascades — a failed import took the admin's note with the
    host it had created."""
    from app.db.models_remediation import RemediationEvent

    pid = test_project.id
    partial = _scan(db_session, pid, "partial.xml")
    dedup = HostDeduplicationService(db_session)
    untouched = dedup.find_or_create_host("10.51.0.1", partial.id, {"state": "up"}, project_id=pid)
    noted = dedup.find_or_create_host("10.51.0.2", partial.id, {"state": "up"}, project_id=pid)
    db_session.add(RemediationEvent(
        project_id=pid, host_id=noted.id, kind="note", body="Owner is the plant team",
        author_id=test_user.id, occurred_at=datetime.now(timezone.utc),
    ))
    db_session.commit()
    assert untouched.id != noted.id

    removed = delete_partial_scan(db_session, partial.id)
    db_session.commit()
    db_session.expire_all()

    assert removed["hosts"] == 1
    assert host_ips(db_session, pid) == ["10.51.0.2"]
    assert db_session.query(RemediationEvent).filter_by(project_id=pid).count() == 1


# ---------------------------------------------------------------------------
# R3 — Nessus across a commit batch
# ---------------------------------------------------------------------------

def test_a_name_seen_in_two_nessus_batches_is_recorded_for_both_addresses(
    db_session, test_project, tmp_path, one_host_batches,
):
    """The same name on hosts in different commit batches.  The dedup
    service's name memo held the DNSName row the batch commit had detached,
    so the second observation raised DetachedInstanceError — caught, logged,
    and lost."""
    from app.services.nessus_integration_service import NessusIntegrationService

    pid = test_project.id
    path = nessus_file(tmp_path, [
        ("10.40.1.1", "lb.example.test"), ("10.40.1.2", "lb.example.test"), ("10.40.1.3", "lb.example.test"),
    ])
    result = NessusIntegrationService(db_session).process_nessus_file(str(path), project_id=pid)
    assert result["success"], result

    values = sorted(
        row[0] for row in db_session.query(models.DNSRecord.value)
        .join(models.DNSName, models.DNSName.id == models.DNSRecord.name_id)
        .filter(models.DNSName.project_id == pid, models.DNSName.fqdn == "lb.example.test")
    )
    assert values == ["10.40.1.1", "10.40.1.2", "10.40.1.3"]


# ---------------------------------------------------------------------------
# R6 — every parser heartbeats, outside its record savepoint
# ---------------------------------------------------------------------------

def _lines(records):
    return "".join(json.dumps(r) + "\n" for r in records)


def _openvas(n):
    results = "".join(
        f'<result id="r{i}"><name>Finding {i}</name><host>10.60.0.{i + 1}</host><port>22/tcp</port>'
        f'<severity>5.0</severity><threat>Medium</threat>'
        f'<nvt oid="1.3.6.1.4.1.25623.1.0.{1000 + i}"/></result>'
        for i in range(n)
    )
    return f'<?xml version="1.0"?>\n<report><results>{results}</results></report>\n'


def _heartbeat_cases():
    n = range(6)
    return [
        ("openvas_parser", "OpenVASParser", "openvas.xml", _openvas(6)),
        ("httpx_parser", "HttpxParser", "httpx.jsonl", _lines(
            {"url": f"http://10.61.0.{i + 1}:80/", "host": f"10.61.0.{i + 1}", "port": "80",
             "status_code": 200, "tech": ["Nginx"]} for i in n)),
        ("nuclei_parser", "NucleiParser", "nuclei.jsonl", _lines(
            {"template-id": f"t{i}", "matched-at": f"10.62.0.{i + 1}:22", "ip": f"10.62.0.{i + 1}",
             "info": {"name": f"T{i}", "severity": "low"}} for i in n)),
        ("whatweb_parser", "WhatwebParser", "whatweb.json", json.dumps([
            {"target": f"http://10.63.0.{i + 1}/", "http_status": 200,
             "plugins": {"IP": {"string": [f"10.63.0.{i + 1}"]}}} for i in n])),
        ("testssl_parser", "TestsslParser", "testssl.json", json.dumps([
            {"id": "TLS1", "ip": f"h{i}.example.test/10.64.0.{i + 1}", "port": "443",
             "severity": "LOW", "finding": "offered (deprecated)"} for i in n])),
        ("dnsx_parser", "DnsxParser", "dnsx.jsonl", _lines(
            {"host": f"h{i}.example.test", "a": [f"10.65.0.{i + 1}"], "status_code": "NOERROR"} for i in n)),
        ("nikto_parser", "NiktoParser", "nikto.json", json.dumps([
            {"ip": "10.66.0.1", "port": "80",
             "vulnerabilities": [{"id": str(700000 + i), "msg": f"finding {i}", "url": f"/p{i}"} for i in n]}])),
        ("dirbuster_parser", "DirBusterParser", "ffuf.json", json.dumps({"results": [
            {"url": f"http://10.67.0.{i + 1}/admin", "status": 200, "length": 10} for i in n]})),
        ("eyewitness_parser", "EyewitnessParser", "eyewitness.json", json.dumps([
            {"url": f"http://10.68.0.{i + 1}/", "ip": f"10.68.0.{i + 1}", "port": 80, "title": "t"} for i in n])),
        ("bloodhound_parser", "BloodHoundParser", "computers.json", json.dumps({"data": [
            {"Properties": {"name": f"H{i}.CORP.LOCAL", "ipv4": f"10.69.0.{i + 1}"}} for i in n]})),
        ("amass_parser", "AmassParser", "names.txt",
         "".join(f"h{i}.example.test 10.70.0.{i + 1}\n" for i in n)),
        ("naabu_parser", "NaabuParser", "naabu.jsonl", _lines(
            {"ip": f"10.71.0.{i + 1}", "port": 80} for i in n)),
        ("dns_parser", "DNSParser", "dns.csv",
         "record_type,name,address\n" + "".join(f"A,h{i}.example.test,10.72.0.{i + 1}\n" for i in n)),
    ]


@pytest.mark.parametrize("module,cls,filename,content", _heartbeat_cases(),
                         ids=[c[0].replace("_parser", "") for c in _heartbeat_cases()])
def test_a_cancel_reaches_every_parser(db_session, test_project, tmp_path, monkeypatch,
                                       module, cls, filename, content):
    """Each of these ran the whole file in one transaction and never looked at
    its job.  Now it heartbeats between records — outside the record's
    savepoint, so the cancel stops the import instead of being logged as one
    more bad record."""
    import importlib

    from app.parsers import parser_utils

    mod = importlib.import_module(f"app.parsers.{module}")
    # Heartbeat every 2 records, so six records are enough.
    monkeypatch.setattr(
        mod, "ProgressBeat",
        lambda label="records", every=500, before=None: parser_utils.ProgressBeat(label, 2, before),
    )
    pid = test_project.id
    path = _write(tmp_path, filename, content)

    with active_job(db_session, pid) as (job_id, svc):
        with on_heartbeat(svc, 1, lambda: svc._cancelled.add(job_id)) as beats:
            with pytest.raises(ParseFailure, match="cancelled"):
                getattr(mod, cls)(db_session).parse_file(str(path), filename, project_id=pid)
    assert beats, "the parser never heartbeated"
    db_session.rollback()


def test_a_cancelled_import_of_a_parser_that_now_commits_leaves_nothing(
    db_session, test_project, tmp_path, monkeypatch,
):
    """R6 made these parsers commit as they go, so they need what nmap had:
    the dispatcher deletes the part that was committed (their scan is named
    through ``ensure_scan`` → the job row)."""
    from app.parsers import naabu_parser, parser_utils

    monkeypatch.setattr(
        naabu_parser, "ProgressBeat",
        lambda label="records", every=500, before=None: parser_utils.ProgressBeat(label, 2, before),
    )
    pid = test_project.id
    path = _write(tmp_path, "naabu-out.jsonl", _lines({"ip": f"10.71.1.{i + 1}", "port": 80} for i in range(8)))
    svc = IngestionService()
    job_id = queue_file(db_session, pid, path)

    # Heartbeat 2: the first one's commit already holds two hosts.
    with on_heartbeat(svc, 2, lambda: svc.cancel_job(job_id)) as beats:
        run_next(db_session, svc)

    assert len(beats) == 2
    job = job_row(db_session, job_id)
    assert job.status == "failed" and "Cancelled" in (job.error_message or "")
    assert all_scan_ids(db_session) == []
    assert host_ips(db_session, pid) == []
