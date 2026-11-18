"""Review 2026-11-17, batch 1 — the side paths that still loaded everything.

Each of these kept an older loading shape after the main path beside it was
fixed, and each could exhaust a worker's memory on input inside the stated
scale (a 70k-host project, a full-range sweep of a tarpit):

* A1 — the tool-ready port formats eager-loaded every port and script;
* A2 — masscan flushed its buffer on distinct ADDRESSES only;
* A3 — only nmap and gnmap released the dedup service's history rows;
* A4 — the comprehensive JSON download hydrated every finding's endpoints;
* A10 — the scan page's host rows carried every stored port;
* A11 — a host's web paths were folded in Python from every stored row.

The tests pin the answer (old == new where there was an old answer) and the
shape that was the hazard.
"""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

from sqlalchemy import event, func
from sqlalchemy.orm import selectinload

import app.parsers.masscan_parser as masscan_module
from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.parsers.masscan_parser import MasscanParser
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.report_generator import ReportGenerator


def _host(db, project, ip, **extra):
    host = models.Host(project_id=project.id, ip_address=ip, state="up", **extra)
    db.add(host)
    db.flush()
    return host


def _port(db, host, number, state="open", **extra):
    port = models.Port(host_id=host.id, port_number=number, protocol="tcp", state=state, **extra)
    db.add(port)
    db.flush()
    return port


def _scan(db, project, name="scan.xml"):
    scan = models.Scan(project_id=project.id, filename=name, tool_name="nmap", scan_type="nmap")
    db.add(scan)
    db.flush()
    return scan


def _statements(db):
    """The SQL the session's connection runs from here on."""
    seen: list = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", record)
    return seen, lambda: event.remove(db.get_bind(), "before_cursor_execute", record)


# --- A2: masscan flushes on buffered records, not only on addresses ---------

def _list_file(tmp_path, ip: str, ports: range):
    lines = ["#masscan"] + [f"open tcp {port} {ip} 1700000000" for port in ports] + ["# end"]
    path = tmp_path / "sweep.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_masscan_flushes_one_address_with_many_ports_and_counts_it_once(
    db_session, test_project, tmp_path, monkeypatch,
):
    """One address, many ports: the address threshold never fires, so the
    record threshold must — and the host is still one host in the answer."""
    monkeypatch.setattr(masscan_module, "_COLLECT_FLUSH_RECORDS", 3)
    parser = MasscanParser(db_session)
    flushed: list = []
    real_persist = parser._batch_persist

    def persist(scan_id, buf):
        flushed.append(sum(len(ports) for ports in buf.values()))
        return real_persist(scan_id, buf)

    parser._batch_persist = persist  # type: ignore[method-assign]
    scan = parser.parse_file(
        str(_list_file(tmp_path, "10.9.0.1", range(1, 11))), "sweep.txt", project_id=test_project.id,
    )

    # Drained as it was read — never the whole file in one buffer.
    assert len(flushed) >= 3 and max(flushed) <= 3, flushed
    host = db_session.query(models.Host).filter(models.Host.project_id == test_project.id).one()
    assert db_session.query(models.Port).filter(models.Port.host_id == host.id).count() == 10
    # One row of history for the one host, however many flushes upserted it.
    assert (
        db_session.query(models.HostScanHistory)
        .filter(models.HostScanHistory.scan_id == scan.id).count()
    ) == 1


def test_every_masscan_format_counts_the_ports_it_buffers(db_session, test_project, tmp_path, monkeypatch):
    """The limit is on PORT records in every format: a JSON entry or an XML
    host carrying several ports counts each (the JSON path counted one per
    entry, so a four-port limit let eight ports sit in the buffer)."""
    monkeypatch.setattr(masscan_module, "_COLLECT_FLUSH_RECORDS", 4)
    ports = [(n, 1000 + n) for n in range(8)]                    # 2 entries × 4 ports

    as_json = tmp_path / "multi.json"
    as_json.write_text(json.dumps([
        {"ip": "10.9.0.2", "timestamp": "1700000000",
         "ports": [{"port": port, "proto": "tcp", "status": "open"} for _, port in ports[half:half + 4]]}
        for half in (0, 4)
    ]))
    as_xml = tmp_path / "multi.xml"
    as_xml.write_text(
        '<?xml version="1.0"?>\n<nmaprun scanner="masscan" start="1700000000">\n'
        + "".join(
            '<host endtime="1700000000"><address addr="10.9.0.3" addrtype="ipv4"/><ports>'
            + "".join(
                f'<port protocol="tcp" portid="{port}"><state state="open" reason="syn-ack"/></port>'
                for _, port in ports[half:half + 4]
            )
            + "</ports></host>\n"
            for half in (0, 4)
        )
        + "</nmaprun>\n"
    )

    for path in (as_json, as_xml):
        parser = MasscanParser(db_session)
        most_buffered: list = []
        real_persist = parser._batch_persist

        def persist(scan_id, buf, real=real_persist, seen=most_buffered):
            seen.append(sum(len(rows) for rows in buf.values()))
            return real(scan_id, buf)

        parser._batch_persist = persist  # type: ignore[method-assign]
        parser.parse_file(str(path), path.name, project_id=test_project.id)
        assert most_buffered and max(most_buffered) <= 4, (path.name, most_buffered)

    stored = dict(
        db_session.query(models.Host.ip_address, models.Port.id)
        .join(models.Port, models.Port.host_id == models.Host.id)
        .filter(models.Host.project_id == test_project.id)
        .with_entities(models.Host.ip_address, func.count(models.Port.id))
        .group_by(models.Host.ip_address)
        .all()
    )
    assert stored == {"10.9.0.2": 8, "10.9.0.3": 8}


# --- A3: a commit releases the history rows; a savepoint does not -----------

def test_a_commit_releases_the_history_rows_the_dedup_service_held(db_session, test_project):
    scan = _scan(db_session, test_project)
    dedup = HostDeduplicationService(db_session)
    host = dedup.find_or_create_host("10.9.1.1", scan.id, {"state": "up"}, test_project.id)
    dedup.find_or_create_port(host.id, scan.id, {"port_number": 22, "protocol": "tcp", "state": "open"})
    assert dedup._pending_host_history and dedup._pending_port_history

    # A record's SAVEPOINT releasing is not a commit: the caches stay (or the
    # per-host working set would be rebuilt for every record).
    with db_session.begin_nested():
        dedup.find_or_create_port(host.id, scan.id, {"port_number": 80, "protocol": "tcp", "state": "open"})
    assert dedup._pending_port_history

    db_session.commit()
    assert dedup._pending_host_history == {} and dedup._pending_port_history == {}

    # And a repeat after the release finds the stored rows: no duplicate.
    dedup.find_or_create_host("10.9.1.1", scan.id, {"state": "up"}, test_project.id)
    dedup.find_or_create_port(host.id, scan.id, {"port_number": 22, "protocol": "tcp", "state": "open"})
    db_session.commit()
    assert db_session.query(models.HostScanHistory).filter_by(scan_id=scan.id).count() == 1
    assert db_session.query(models.PortScanHistory).filter_by(scan_id=scan.id).count() == 2


def test_no_parser_releases_the_history_by_hand(db_session):
    """The release is the commit's; a parser calling it again is the old
    "remember to" rule coming back."""
    import pathlib

    parsers = pathlib.Path(masscan_module.__file__).parent
    callers = [
        path.name for path in parsers.glob("*.py")
        if "release_committed_history" in path.read_text()
    ]
    assert callers == []


# --- A1: the tool-ready port formats read port columns ----------------------

def _export_fixture(db, project):
    a = _host(db, project, "10.9.2.1")
    b = _host(db, project, "10.9.2.2")
    _port(db, a, 443, service_name="https")
    _port(db, a, 22, state="closed", service_name="ssh")
    _port(db, a, 8080, state="filtered")
    _port(db, b, 25, state="closed")
    db.add(models.Script(port_id=db.query(models.Port).filter_by(host_id=a.id, port_number=443).one().id,
                         script_id="ssl-cert", output="x" * 50))
    db.flush()
    return a, b


def test_the_port_formats_print_what_they_did_and_load_no_script(client, db_session, test_project):
    _export_fixture(db_session, test_project)
    base = f"/api/v1/projects/{test_project.id}/hosts/tool-ready"
    seen, stop = _statements(db_session)
    try:
        host_port = client.get(f"{base}/host-port")
        nuclei = client.get(f"{base}/nuclei")
        web = client.get(f"{base}/web-targets")
        as_json = client.get(f"{base}/json?include_ports=true")
    finally:
        stop()
    for answer in (host_port, nuclei, web, as_json):
        assert answer.status_code == 200, answer.text

    # Open ports only; a host with none is its bare address.
    assert host_port.text.splitlines() == ["10.9.2.1:443", "10.9.2.2"]
    assert nuclei.text.splitlines() == ["https://10.9.2.1", "10.9.2.2"]
    assert web.text.splitlines() == ["https://10.9.2.1"]
    # ``json`` is the one format that prints a port in any state.
    by_ip = {row["ip_address"]: row for row in as_json.json()}
    assert sorted((p["port"], p["state"]) for p in by_ip["10.9.2.1"]["ports"]) == [
        (22, "closed"), (443, "open"), (8080, "filtered"),
    ]
    assert by_ip["10.9.2.2"]["ports"] == [{
        "port": 25, "protocol": "tcp", "state": "closed", "service": None, "product": None, "version": None,
    }]

    # The hazard: no format reads a script, and the open-only ones ask the
    # database for open ports rather than filtering every port in Python.
    assert not [s for s in seen if "scripts_v2" in s], "the export loaded scripts"


# --- A10: a scan page row carries its open ports ----------------------------

def test_a_scan_host_row_carries_its_open_ports_only(client, db_session, test_project):
    scan = _scan(db_session, test_project)
    host = _host(db_session, test_project, "10.9.3.1")
    _port(db_session, host, 80)
    _port(db_session, host, 81, state="closed")
    _port(db_session, host, 82, state="filtered")
    db_session.add(models.HostScanHistory(host_id=host.id, scan_id=scan.id, state_at_scan="up"))
    db_session.flush()

    answer = client.get(f"/api/v1/projects/{test_project.id}/hosts/scan/{scan.id}")
    assert answer.status_code == 200, answer.text
    (row,) = answer.json()
    assert [(p["port_number"], p["state"]) for p in row["ports"]] == [(80, "open")]


# --- A11: web paths are folded by the database ------------------------------

def test_web_paths_are_the_latest_row_per_url_with_its_scan_count(client, db_session, test_project):
    host = _host(db_session, test_project, "10.9.4.1")
    port = _port(db_session, host, 8443)
    first, second = _scan(db_session, test_project, "a.json"), _scan(db_session, test_project, "b.json")

    def path(scan, url, status, port_id=None):
        db_session.add(models.WebPath(
            project_id=test_project.id, host_id=host.id, scan_id=scan.id, source="ffuf",
            url=url, path=url.split("10.9.4.1")[-1], status_code=status, port_id=port_id,
        ))
        db_session.flush()

    path(first, "http://10.9.4.1/admin", 403)
    path(second, "http://10.9.4.1/admin", 200)          # the later scan's answer wins
    path(first, "https://10.9.4.1:8443/login", 200, port.id)
    path(first, "http://10.9.4.1/a", 301)

    answer = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}/web-paths")
    assert answer.status_code == 200, answer.text
    assert [(r["path"], r["status_code"], r["scans"], r["port"]) for r in answer.json()] == [
        ("/a", 301, 1, None),
        ("/admin", 200, 2, None),
        (":8443/login", 200, 1, 8443),
    ]


# --- A4: the JSON download's findings, old == new ---------------------------

def _findings_as_they_were(gen: ReportGenerator, host_ids):
    """``_findings_for_report_ids`` before the change: entities, one IN."""
    host_id_set = set(host_ids)
    findings = (
        gen.db.query(Finding)
        .options(selectinload(Finding.hosts).selectinload(FindingHost.host), selectinload(Finding.owner))
        .filter(Finding.project_id == gen.project_id, Finding.hosts.any(FindingHost.host_id.in_(host_ids)))
        .all()
    )
    comments = gen._finding_comments([f.id for f in findings])
    return {
        f.id: {
            "id": f.id, "title": f.title, "severity": f.severity, "status": f.status, "source": f.source,
            "owner": (f.owner.full_name or f.owner.username) if f.owner else None,
            "host_count": len(f.hosts),
            "affected_hosts": sorted(fh.host.ip_address for fh in f.hosts if fh.host),
            "affected": sorted(
                ({"ip": fh.host.ip_address, "host_id": fh.host_id}
                 for fh in f.hosts if fh.host and fh.host_id in host_id_set),
                key=lambda a: a["host_id"],
            ),
            "vuln_id": f.vuln_id, "evidence_annotation_id": f.evidence_annotation_id,
            "comments": comments.get(f.id, []),
        }
        for f in findings
    }


def test_the_downloads_findings_are_what_they_were(db_session, test_project, test_user):
    hosts = [_host(db_session, test_project, f"10.9.5.{n}") for n in range(1, 6)]

    def finding(title, severity, on, owner=None):
        row = Finding(project_id=test_project.id, title=title, severity=severity, status="confirmed",
                      source="manual", created_by_id=test_user.id, owner_id=owner)
        db_session.add(row)
        db_session.flush()
        db_session.add_all([FindingHost(finding_id=row.id, host_id=h.id, host_status="open") for h in on])
        db_session.flush()
        return row

    finding("in and out", "low", hosts[:3], owner=test_user.id)
    finding("all in", "critical", hosts[1:2])
    finding("all out", "high", hosts[3:])            # touches no downloaded host
    finding("no endpoint", "medium", [])
    db_session.commit()

    gen = ReportGenerator(db=db_session, current_user=MagicMock(id=test_user.id), project_id=test_project.id)
    downloaded = [hosts[0].id, hosts[1].id]
    new = gen._findings_for_report_ids(downloaded)
    old = _findings_as_they_were(gen, downloaded)

    assert [f["title"] for f in new] == ["all in", "in and out"]      # by severity
    assert len(new) == len(old)
    for row in new:
        row = dict(row, affected_hosts=sorted(row["affected_hosts"]),
                   affected=sorted(row["affected"], key=lambda a: a["host_id"]))
        assert row == old[row["id"]]


def test_the_download_is_valid_json_with_and_without_the_roll_ups(db_session, test_project, test_user):
    host = _host(db_session, test_project, "10.9.6.1")
    row = Finding(project_id=test_project.id, title="one", severity="high", status="confirmed",
                  source="manual", created_by_id=test_user.id)
    db_session.add(row)
    db_session.flush()
    db_session.add(FindingHost(finding_id=row.id, host_id=host.id, host_status="open"))
    db_session.commit()
    gen = ReportGenerator(db=db_session, current_user=MagicMock(id=test_user.id), project_id=test_project.id)

    whole, inventory, empty = io.BytesIO(), io.BytesIO(), io.BytesIO()
    assert gen.write_json_report({}, "comprehensive", whole) == 1
    data = json.loads(whole.getvalue())
    assert list(data) == ["generated_at", "report_type", "hosts", "summary", "findings", "hotspots", "systemic"]
    assert [f["title"] for f in data["findings"]] == ["one"] and data["summary"]["total_hosts"] == 1

    gen.write_json_report({}, "inventory", inventory)
    assert list(json.loads(inventory.getvalue())) == ["generated_at", "report_type", "hosts", "summary"]

    # No host matches: every list is empty and the file is still JSON.
    gen.write_json_report({"state": "down"}, "comprehensive", empty)
    nothing = json.loads(empty.getvalue())
    assert nothing["hosts"] == [] and nothing["findings"] == []
