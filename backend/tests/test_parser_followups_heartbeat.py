"""R6 remainder (review 2026-10-01): rdap, rustscan and smbmap heartbeat, and
the collect-then-persist parsers heartbeat while they READ.

Every test runs under an active job (``ingestion_job_harness``): a heartbeat
is where a cancel reaches the parser, and a cancel during the read phase
leaves nothing — no scan, no host, no attribution row.
"""
from __future__ import annotations

import importlib
import json

import pytest

from app.db import models
from app.db.models_attribution import NetworkAttribution
from app.parsers import parser_utils
from app.services.ingestion_service import IngestionService, ParseFailure
from tests.ingestion_job_harness import (
    active_job, all_scan_ids, host_ips, job_row, on_heartbeat, queue_file, run_next,
)


def _lines(records):
    return "".join(json.dumps(r) + "\n" for r in records)


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return path


def _read_every_two(monkeypatch, mod):
    """The read-phase heartbeat every 2 records, so a handful is enough."""
    if hasattr(mod, "_READ_BEAT_EVERY"):
        monkeypatch.setattr(mod, "_READ_BEAT_EVERY", 2)
    if hasattr(mod, "beat_while_reading"):
        monkeypatch.setattr(
            mod, "beat_while_reading",
            lambda items, label="records read", every=20000: parser_utils.beat_while_reading(items, label, 2),
        )


N = range(6)
MASSCAN_XML = (
    '<?xml version="1.0"?>\n<nmaprun scanner="masscan" start="1790124757" version="1.0-BETA">\n'
    + "".join(
        f'<host endtime="1790124714"><address addr="10.80.0.{i + 1}" addrtype="ipv4"/><ports>'
        f'<port protocol="tcp" portid="80"><state state="open" reason="syn-ack" reason_ttl="63"/></port>'
        f"</ports></host>\n" for i in N
    )
    + "</nmaprun>\n"
)
RDAP_RECORD = {"startAddress": "10.86.0.0", "endAddress": "10.86.0.255", "handle": "NET-10-86-0-0-1", "name": "LAB"}


def _read_phase_cases():
    return [
        ("naabu_parser", "NaabuParser", "naabu.jsonl", _lines({"ip": f"10.81.0.{i + 1}", "port": 80} for i in N)),
        ("naabu_parser", "NaabuParser", "naabu.txt", "".join(f"10.81.1.{i + 1}:80\n" for i in N)),
        ("testssl_parser", "TestsslParser", "testssl.json", json.dumps([
            {"id": "TLS1", "ip": f"h{i}.example.test/10.82.0.{i + 1}", "port": "443",
             "severity": "LOW", "finding": "offered (deprecated)"} for i in N])),
        ("dirbuster_parser", "DirBusterParser", "ffuf.json", json.dumps({"results": [
            {"url": f"http://10.83.0.{i + 1}/admin", "status": 200, "length": 10} for i in N]})),
        ("dirbuster_parser", "DirBusterParser", "ffuf.csv",
         "url,status_code,content_length\n" + "".join(f"http://10.83.1.{i + 1}/admin,200,10\n" for i in N)),
        ("dirbuster_parser", "DirBusterParser", "gobuster.txt",
         "".join(f"http://10.83.2.{i + 1}/admin (Status: 200) [Size: 10]\n" for i in N)),
        ("masscan_parser", "MasscanParser", "masscan.xml", MASSCAN_XML),
        ("masscan_parser", "MasscanParser", "masscan.json", json.dumps([
            {"ip": f"10.80.1.{i + 1}", "timestamp": "1790124714",
             "ports": [{"port": 80, "proto": "tcp", "status": "open", "reason": "syn-ack", "ttl": 63}]} for i in N])),
        ("masscan_parser", "MasscanParser", "masscan.txt",
         "#masscan\n" + "".join(f"open tcp 80 10.80.2.{i + 1} 1790124714\n" for i in N) + "# end\n"),
        ("smbmap_parser", "SMBMapParser", "smbmap.txt",
         "".join(f"[+] IP: 10.84.0.{i + 1}:445\tName: h{i}\tStatus: Authenticated\n" for i in N)),
        ("rustscan_parser", "RustScanParser", "rustscan.txt", "".join(f"Open 10.85.0.{i + 1}:22\n" for i in N)),
        ("rdap_parser", "RdapParser", "rdap.ndjson", _lines(
            {"query": f"10.86.{i}.1", "rdap": {**RDAP_RECORD, "startAddress": f"10.86.{i}.0",
                                              "endAddress": f"10.86.{i}.255"}} for i in N)),
    ]


@pytest.mark.parametrize(
    "module,cls,filename,content", _read_phase_cases(),
    ids=[f"{c[0].replace('_parser', '')}-{c[2].rsplit('.', 1)[1]}" for c in _read_phase_cases()],
)
def test_a_cancel_during_the_read_phase_stops_the_parse_and_leaves_nothing(
    db_session, test_project, tmp_path, monkeypatch, module, cls, filename, content,
):
    mod = importlib.import_module(f"app.parsers.{module}")
    _read_every_two(monkeypatch, mod)
    pid = test_project.id
    path = _write(tmp_path, filename, content)

    with active_job(db_session, pid) as (job_id, svc):
        with on_heartbeat(svc, 1, lambda: svc._cancelled.add(job_id)) as beats:
            with pytest.raises(ParseFailure, match="cancelled"):
                getattr(mod, cls)(db_session).parse_file(str(path), filename, project_id=pid)
    assert beats and "read" in beats[0], f"the first heartbeat was not in the read phase: {beats}"
    db_session.rollback()
    # Nothing but (at most) the scan row the dispatcher deletes was written.
    assert host_ips(db_session, pid) == []
    assert db_session.query(NetworkAttribution).filter_by(project_id=pid).count() == 0
    assert db_session.query(models.DNSRecord).filter_by(project_id=pid).count() == 0


@pytest.mark.parametrize("module,filename,content", [
    ("rustscan_parser", "rustscan.txt", "".join(f"Open 10.87.0.{i + 1}:22\n" for i in N)),
    ("testssl_parser", "testssl.json", _read_phase_cases()[2][3]),
    ("masscan_parser", "masscan.txt", _read_phase_cases()[8][3]),
])
def test_a_job_cancelled_while_reading_fails_with_no_scan(
    db_session, test_project, tmp_path, monkeypatch, module, filename, content,
):
    """The same, through the worker's own path: the job fails as cancelled and
    its scan is gone."""
    _read_every_two(monkeypatch, importlib.import_module(f"app.parsers.{module}"))
    pid = test_project.id
    svc = IngestionService()
    job_id = queue_file(db_session, pid, _write(tmp_path, filename, content))

    with on_heartbeat(svc, 1, lambda: svc.cancel_job(job_id)) as beats:
        run_next(db_session, svc)

    assert beats and "read" in beats[0]
    job = job_row(db_session, job_id)
    assert job.status == "failed" and "Cancelled" in (job.error_message or "")
    assert all_scan_ids(db_session) == []
    assert host_ips(db_session, pid) == []


@pytest.mark.parametrize("module,filename,content", [
    ("rustscan_parser", "rustscan.txt", "".join(f"Open 10.88.0.{i + 1}:22\n" for i in N)),
    ("smbmap_parser", "smbmap.txt",
     "".join(f"[+] IP: 10.88.1.{i + 1}:445\tName: h{i}\tStatus: Authenticated\n" for i in N)),
])
def test_a_cancel_between_hosts_removes_what_was_committed(
    db_session, test_project, tmp_path, monkeypatch, module, filename, content,
):
    """rustscan and smbmap never heartbeated while writing.  Now they do, so
    they commit as they go — and the dispatcher deletes the committed part."""
    mod = importlib.import_module(f"app.parsers.{module}")
    monkeypatch.setattr(
        mod, "ProgressBeat",
        lambda label="records", every=500, before=None: parser_utils.ProgressBeat(label, 2, before),
    )
    pid = test_project.id
    svc = IngestionService()
    job_id = queue_file(db_session, pid, _write(tmp_path, filename, content))

    # Heartbeat 2: the first one's commit already holds two hosts.
    with on_heartbeat(svc, 2, lambda: svc.cancel_job(job_id)) as beats:
        run_next(db_session, svc)

    assert beats == ["2 hosts", "4 hosts"]
    job = job_row(db_session, job_id)
    assert job.status == "failed" and "Cancelled" in (job.error_message or "")
    assert all_scan_ids(db_session) == []
    assert host_ips(db_session, pid) == []


def test_rdap_checks_its_job_once_before_it_writes(db_session, test_project, tmp_path):
    """A whole-file JSON document has no line loop to heartbeat in: the one
    heartbeat before the write is where a cancel lands, and no attribution row
    (which no cleanup could find — it is not the scan's) is written."""
    pid = test_project.id
    path = _write(tmp_path, "rdap.json", json.dumps(RDAP_RECORD, indent=2))
    from app.parsers.rdap_parser import RdapParser

    with active_job(db_session, pid) as (job_id, svc):
        with on_heartbeat(svc, 1, lambda: svc._cancelled.add(job_id)) as beats:
            with pytest.raises(ParseFailure, match="cancelled"):
                RdapParser(db_session).parse_file(str(path), "rdap.json", project_id=pid)
    assert beats == ["1 records read"]
    db_session.rollback()
    assert db_session.query(NetworkAttribution).filter_by(project_id=pid).count() == 0


def test_rdap_writes_its_blocks_in_one_transaction(db_session, test_project, tmp_path):
    """No heartbeat (so no commit) between attribution rows."""
    pid = test_project.id
    content = _lines(
        {"query": f"10.89.{i}.1", "rdap": {**RDAP_RECORD, "startAddress": f"10.89.{i}.0",
                                          "endAddress": f"10.89.{i}.255"}} for i in N)
    path = _write(tmp_path, "rdap.ndjson", content)
    from app.parsers.rdap_parser import RdapParser

    with active_job(db_session, pid) as (_job_id, svc):
        with on_heartbeat(svc, 99, lambda: None) as beats:
            RdapParser(db_session).parse_file(str(path), "rdap.ndjson", project_id=pid)
    assert beats == ["6 records read"]
    assert db_session.query(NetworkAttribution).filter_by(project_id=pid).count() == 6
