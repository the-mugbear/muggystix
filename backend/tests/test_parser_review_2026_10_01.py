"""Parser fixes from the review of 2026-10-01: R4 (masscan refuses another
scanner's XML), R5 (DNS CSV row isolation, UTF-16 and NUL in text reports),
R7 (per-record cost in the nmap path) and N9 (rows dropped without a count).
"""
from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.models_vulnerability import Vulnerability
from app.parsers.dns_parser import DNSParser
from app.parsers.gnmap_parser import GnmapParser
from app.parsers.masscan_parser import MasscanParser
from app.parsers.nmap_parser import NmapXMLParser
from app.services.host_deduplication_service import HostDeduplicationService
from tests.ingestion_job_harness import host_ips, run_file, scans_of


def _write(tmp_path, name, content):
    path = tmp_path / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# R4 — masscan reads masscan's XML only
# ---------------------------------------------------------------------------

def test_masscan_refuses_an_nmap_file(db_session, test_project, tmp_path, sample_nmap_xml):
    path = _write(tmp_path, "scan.xml", sample_nmap_xml)
    with pytest.raises(ValueError, match="Not masscan XML"):
        MasscanParser(db_session).parse_file(str(path), "scan.xml", project_id=test_project.id)


def test_masscan_still_reads_its_own_xml(db_session, test_project, tmp_path, sample_masscan_xml):
    pid = test_project.id
    path = _write(tmp_path, "masscan.xml", sample_masscan_xml)
    scan = MasscanParser(db_session).parse_file(str(path), "masscan.xml", project_id=pid)
    assert scan.tool_name == "masscan"
    assert host_ips(db_session, pid) == ["192.168.1.100"]


def test_masscan_reads_a_root_that_names_no_scanner(db_session, test_project, tmp_path, sample_masscan_xml):
    """Only a root that NAMES another scanner is refused."""
    pid = test_project.id
    path = _write(tmp_path, "m.xml", sample_masscan_xml.replace(' scanner="masscan"', ""))
    MasscanParser(db_session).parse_file(str(path), "m.xml", project_id=pid)
    assert host_ips(db_session, pid) == ["192.168.1.100"]


def test_an_nmap_failure_is_not_rescued_as_a_masscan_import(
    db_session, test_project, tmp_path, monkeypatch, sample_nmap_xml,
):
    """The nmap parser fails on something other than a bad host.  The
    dispatcher then tried masscan, which read the same file and completed the
    job with open ports only — no services, scripts or OS."""
    def boom(self, file_path, filename, **kwargs):
        raise RuntimeError("nmap parser broke outside a host")

    monkeypatch.setattr(NmapXMLParser, "parse_file", boom)
    pid = test_project.id
    job = run_file(db_session, pid, _write(tmp_path, "scan.xml", sample_nmap_xml))

    assert job.status == "failed", (job.status, job.tool_name)
    assert scans_of(db_session, pid) == []
    assert host_ips(db_session, pid) == []


# ---------------------------------------------------------------------------
# R5 — DNS CSV: one bad row is one bad row; UTF-16; NUL
# ---------------------------------------------------------------------------

def _dns_values(db, pid):
    return sorted(
        row[0] for row in db.query(models.DNSRecord.value)
        .join(models.DNSName, models.DNSName.id == models.DNSRecord.name_id)
        .filter(models.DNSName.project_id == pid)
    )


def test_a_dns_row_the_database_refuses_costs_only_that_row(db_session, test_project, tmp_path):
    """The middle row's TTL does not fit the column, so its INSERT fails.
    Without a savepoint the session was unusable from there on: every later
    row was "rejected" too and the import failed."""
    pid = test_project.id
    path = _write(tmp_path, "dns.csv", (
        "record_type,name,address,ttl\n"
        "A,one.example.test,10.90.0.1,300\n"
        "A,two.example.test,10.90.0.2,99999999999999\n"
        "A,three.example.test,10.90.0.3,300\n"
        "PTR,four.example.test,10.90.0.4,300\n"
    ))
    parser = DNSParser(db_session)
    parser.parse_file(str(path), "dns.csv", project_id=pid)

    assert _dns_values(db_session, pid) == ["10.90.0.1", "10.90.0.3", "10.90.0.4"]
    assert parser.last_parse_stats["skipped"] == 1
    assert parser.last_parse_stats["partial"] is True
    # The PTR row after the failure still made its host.
    assert host_ips(db_session, pid) == ["10.90.0.4"]


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8-sig"])
def test_a_dns_csv_saved_as_unicode_text_imports(db_session, test_project, tmp_path, encoding):
    pid = test_project.id
    text = "record_type,name,address\nA,www.example.test,10.91.0.1\n"
    path = _write(tmp_path, "dns.csv", text.encode(encoding))
    DNSParser(db_session).parse_file(str(path), "dns.csv", project_id=pid)
    assert _dns_values(db_session, pid) == ["10.91.0.1"]


def test_a_nul_in_a_gnmap_line_does_not_lose_the_host(db_session, test_project, tmp_path):
    pid = test_project.id
    path = _write(tmp_path, "out.gnmap", (
        "# Nmap 7.94 scan initiated Mon Jul 15 10:30:01 2024 as: nmap -oG out.gnmap 10.92.0.1\n"
        "Host: 10.92.0.1 (gw\x00.example.test)\tStatus: Up\n"
        "Host: 10.92.0.1 (gw\x00.example.test)\tPorts: 22/open/tcp//ssh///\n"
    ))
    parser = GnmapParser(db_session)
    parser.parse_file(str(path), "out.gnmap", project_id=pid)
    db_session.commit()

    host = db_session.query(models.Host).filter(models.Host.ip_address == "10.92.0.1").one()
    assert host.hostname == "gw.example.test"
    assert parser.last_parse_stats["skipped"] == 0


def test_a_nul_in_a_masscan_banner_does_not_fail_the_import(db_session, test_project, tmp_path):
    pid = test_project.id
    path = _write(tmp_path, "masscan.txt", (
        "#masscan\n"
        "open tcp 80 10.93.0.1 1700000000\n"
        "banner tcp 80 10.93.0.1 1700000000 http.server ngi\x00nx\n"
        "# end\n"
    ))
    MasscanParser(db_session).parse_file(str(path), "masscan.txt", project_id=pid)
    db_session.commit()
    outputs = [row[0] for row in db_session.query(models.Script.output)]
    assert outputs == ["nginx"]


# ---------------------------------------------------------------------------
# R7 — per-record cost in the nmap path
# ---------------------------------------------------------------------------

def _nmap(hosts: str) -> str:
    return (
        '<?xml version="1.0"?>\n'
        '<nmaprun scanner="nmap" args="nmap -sC" start="1700000000" version="7.94">\n'
        + hosts + '</nmaprun>\n'
    )


def _statements(db_session):
    seen = []
    bind = db_session.get_bind()

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(bind, "before_cursor_execute", record)
    return seen, lambda: event.remove(bind, "before_cursor_execute", record)


def test_nmap_does_not_look_a_held_port_up_per_script_or_savepoint_per_port(
    db_session, test_project, tmp_path,
):
    """Five new ports, six scripts on one of them, none reporting a
    vulnerability.  It was a ``SELECT … FROM ports_v2`` per script (for a
    port the caller holds) and a SAVEPOINT per new port."""
    scripts = "".join(f'<script id="banner-{i}" output="hello {i}"/>' for i in range(6))
    ports = "".join(
        f'<port protocol="tcp" portid="{n}"><state state="open" reason="syn-ack"/>'
        f'<service name="svc{n}" method="probed" conf="10"/>{scripts if n == 80 else ""}</port>'
        for n in (21, 22, 80, 443, 8080)
    )
    path = _write(tmp_path, "scan.xml", _nmap(
        f'<host><status state="up"/><address addr="10.94.0.1" addrtype="ipv4"/><ports>{ports}</ports></host>\n'
    ))

    seen, stop = _statements(db_session)
    try:
        NmapXMLParser(db_session).parse_file(str(path), "scan.xml", project_id=test_project.id)
    finally:
        stop()

    port_lookups = [s for s in seen if "FROM ports_v2" in s and "port_number =" in s]
    savepoints = [s for s in seen if s.startswith("SAVEPOINT")]
    assert port_lookups == []
    # The host's own savepoint, the new host's inside it, and the two the
    # test session's commits open — four whatever the port count.  It was
    # those plus one per new port (nine here).
    assert len(savepoints) <= 4, savepoints
    assert db_session.query(models.Script).count() == 6


def test_an_nse_result_goes_on_the_port_the_script_ran_on(db_session, test_project, tmp_path):
    """The port was looked up again by NUMBER (tcp assumed), so a result on
    21/udp landed on 21/tcp when the host had both."""
    path = _write(tmp_path, "scan.xml", _nmap(
        '<host><status state="up"/><address addr="10.94.0.2" addrtype="ipv4"/><ports>'
        '<port protocol="tcp" portid="21"><state state="open"/><service name="ftp" conf="10"/></port>'
        '<port protocol="udp" portid="21"><state state="open"/><service name="ftp" conf="10"/>'
        '<script id="ftp-anon" output="Anonymous FTP login allowed (FTP code 230)"/></port>'
        '</ports></host>\n'
    ))
    NmapXMLParser(db_session).parse_file(str(path), "scan.xml", project_id=test_project.id)

    vuln = db_session.query(Vulnerability).filter(Vulnerability.check_id == "ftp_anonymous").one()
    port = db_session.get(models.Port, vuln.port_id)
    assert (port.port_number, port.protocol) == (21, "udp")


def test_nmap_processes_a_host_again_when_a_port_insert_lost_a_race(
    db_session, test_project, tmp_path, monkeypatch,
):
    """Without a savepoint per port, a concurrent insert surfaces as
    IntegrityError on the HOST's savepoint; the host is processed once more
    (and now finds the row) instead of being skipped."""
    path = _write(tmp_path, "scan.xml", _nmap(
        '<host><status state="up"/><address addr="10.94.0.3" addrtype="ipv4"/><ports>'
        '<port protocol="tcp" portid="22"><state state="open"/></port></ports></host>\n'
    ))
    real = NmapXMLParser._process_host_with_deduplication
    calls = {"n": 0}

    def lose_once(self, elem, scan_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT INTO ports_v2", {}, Exception("duplicate key"))
        return real(self, elem, scan_id)

    monkeypatch.setattr(NmapXMLParser, "_process_host_with_deduplication", lose_once)
    parser = NmapXMLParser(db_session)
    parser.parse_file(str(path), "scan.xml", project_id=test_project.id)

    assert calls["n"] == 2
    assert parser.last_parse_stats["skipped"] == 0
    assert host_ips(db_session, test_project.id) == ["10.94.0.3"]


def test_released_history_is_found_again_by_the_index(db_session, test_project):
    """``release_committed_history`` drops flushed rows from the pending
    dicts; a repeat of the host and port in the same scan must update the
    stored history row, not insert a second one (uq_host_scan / uq_port_scan)."""
    pid = test_project.id
    scan = models.Scan(filename="s.xml", tool_name="nmap", scan_type="nmap", project_id=pid)
    db_session.add(scan)
    db_session.flush()
    dedup = HostDeduplicationService(db_session)
    port = {"port_number": 22, "protocol": "tcp", "state": "open"}

    host = dedup.find_or_create_host("10.95.0.1", scan.id, {"state": "up"}, project_id=pid)
    dedup.find_or_create_port(host.id, scan.id, port, isolated=True)
    db_session.flush()
    assert dedup._pending_host_history and dedup._pending_port_history
    dedup.release_committed_history()
    assert not dedup._pending_host_history and not dedup._pending_port_history

    host = dedup.find_or_create_host("10.95.0.1", scan.id, {"state": "up"}, project_id=pid)
    dedup.find_or_create_port(host.id, scan.id, {**port, "service_name": "ssh"}, isolated=True)
    db_session.flush()

    assert db_session.query(models.HostScanHistory).filter_by(scan_id=scan.id).count() == 1
    history = db_session.query(models.PortScanHistory).filter_by(scan_id=scan.id).one()
    assert history.port_created is True and history.service_name == "ssh"


def test_unflushed_history_is_kept_by_a_release(db_session, test_project):
    """A row not yet in the database cannot be found by a query (autoflush is
    off), so it must stay in the pending dict."""
    pid = test_project.id
    scan = models.Scan(filename="s.xml", tool_name="nmap", scan_type="nmap", project_id=pid)
    db_session.add(scan)
    db_session.flush()
    dedup = HostDeduplicationService(db_session)
    dedup.find_or_create_host("10.95.0.2", scan.id, {"state": "up"}, project_id=pid)
    assert len(dedup._pending_host_history) == 1
    dedup.release_committed_history()   # the history row is still pending
    assert len(dedup._pending_host_history) == 1


# ---------------------------------------------------------------------------
# N9 — dropped rows are counted
# ---------------------------------------------------------------------------

def test_nessus_says_how_many_report_items_it_could_not_read(db_session, test_project, tmp_path, monkeypatch):
    from app.parsers.nessus_parser import NessusParser
    from app.services.nessus_integration_service import NessusIntegrationService
    from tests.test_nessus_skip_informational import NESSUS_XML

    real = NessusParser._parse_vulnerability

    def unreadable(self, report_item):
        if report_item.get("pluginID") == "90317":
            raise ValueError("malformed report item")
        return real(self, report_item)

    monkeypatch.setattr(NessusParser, "_parse_vulnerability", unreadable)
    path = _write(tmp_path, "batch.nessus", NESSUS_XML)
    result = NessusIntegrationService(db_session).process_nessus_file(str(path), project_id=test_project.id)

    assert result["success"] and result["partial"] is True
    assert result["report_items_unreadable"] == 1
    assert any("1 report item(s) could not be read" in w for w in result["warnings"])


def test_masscan_counts_list_lines_it_could_not_read(db_session, test_project, tmp_path):
    path = _write(tmp_path, "masscan.txt", (
        "#masscan\n"
        "open tcp 80 10.96.0.1 1700000000\n"
        "open tcp\n"
        "open tcp eighty 10.96.0.1 1700000000\n"
        "# end\n"
    ))
    parser = MasscanParser(db_session)
    parser.parse_file(str(path), "masscan.txt", project_id=test_project.id)

    assert parser.last_parse_stats["skipped"] == 2
    assert "2 lines" in parser.last_parse_stats["warnings"]
    assert parser.last_parse_stats["partial"] is True


def test_a_clean_masscan_list_reports_nothing_skipped(db_session, test_project, tmp_path):
    path = _write(tmp_path, "masscan.txt", "#masscan\nopen tcp 80 10.96.0.2 1700000000\n# end\n")
    parser = MasscanParser(db_session)
    parser.parse_file(str(path), "masscan.txt", project_id=test_project.id)
    assert parser.last_parse_stats is None
