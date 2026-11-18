"""Review 2026-11-18 of the batch-1 parser / export work (commit 781fce08).

* A masscan JSON entry whose every port was rejected made a host marked up:
  the count of ports the entry buffered read a ``defaultdict`` by key, which
  ADDS the key, and an address in the map is a host to persist.
* The JSON download of a few hosts read every finding endpoint of the project
  and chose the findings in Python.
* The masscan host count had two owners (a tally of the flushes and the
  scan's history rows); the history is the one.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event

import app.parsers.masscan_parser as masscan_module
from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.parsers.masscan_parser import MasscanParser
from app.services.report_generator import ReportGenerator

REJECTED = [
    pytest.param([], id="no ports"),
    pytest.param([{"port": "bad", "status": "open"}], id="a port that is not a number"),
    pytest.param([{"port": 80, "status": "closed"}], id="a port that is not open"),
]


def _hosts(db, project):
    return {h.ip_address: h.state for h in db.query(models.Host).filter_by(project_id=project.id)}


@pytest.mark.parametrize("ports", REJECTED)
def test_a_masscan_entry_with_no_port_kept_makes_no_host(db_session, test_project, tmp_path, ports):
    path = tmp_path / "masscan.json"
    path.write_text(json.dumps([{"ip": "192.0.2.10", "ports": ports}]))

    # …and a file of nothing but such entries is not a masscan result at all.
    with pytest.raises(ValueError, match="found 0 hosts"):
        MasscanParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)

    db_session.rollback()
    assert _hosts(db_session, test_project) == {}
    assert db_session.query(models.HostScanHistory).count() == 0


@pytest.mark.parametrize("ports", REJECTED)
def test_a_rejected_masscan_entry_beside_a_real_one_leaves_only_the_real_host(
    db_session, test_project, tmp_path, ports,
):
    path = tmp_path / "masscan.json"
    path.write_text(json.dumps([
        {"ip": "192.0.2.10", "ports": ports},
        {"ip": "192.0.2.20", "ports": [{"port": 443, "proto": "tcp", "status": "open"}]},
        {"ip": "192.0.2.10", "ports": ports},
    ]))

    scan = MasscanParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)

    assert _hosts(db_session, test_project) == {"192.0.2.20": "up"}
    history = db_session.query(models.HostScanHistory).filter_by(scan_id=scan.id).all()
    assert [h.host.ip_address for h in history] == ["192.0.2.20"]


def test_the_masscan_host_count_is_the_scans_history_across_flushes(
    db_session, test_project, tmp_path, monkeypatch,
):
    """One owner of the count: an address whose entries straddle a flush is
    one host, and nothing tallies the flushes."""
    monkeypatch.setattr(masscan_module, "_COLLECT_FLUSH_RECORDS", 2)
    entries = [{"ip": "192.0.2.30", "ports": [{"port": port, "proto": "tcp", "status": "open"}]}
               for port in range(1000, 1007)]
    entries.append({"ip": "192.0.2.31", "ports": [{"port": 22, "proto": "tcp", "status": "open"}]})
    path = tmp_path / "masscan.json"
    path.write_text(json.dumps(entries))

    parser = MasscanParser(db_session)
    scan = parser.parse_file(str(path), path.name, project_id=test_project.id)

    assert db_session.query(models.HostScanHistory).filter_by(scan_id=scan.id).count() == 2
    assert parser.last_parse_stats["hosts"] == 2 if "hosts" in (parser.last_parse_stats or {}) else True
    stored = db_session.query(models.Port).join(models.Host).filter(models.Host.ip_address == "192.0.2.30").count()
    assert stored == 7
    source = open(masscan_module.__file__, encoding="utf-8").read()
    assert "counters[" not in source


def test_a_download_of_one_host_reads_only_its_findings_endpoints(db_session, test_project, test_user):
    def host(ip):
        row = models.Host(project_id=test_project.id, ip_address=ip, state="up")
        db_session.add(row)
        db_session.flush()
        return row

    def finding(title, on):
        row = Finding(project_id=test_project.id, title=title, severity="high", status="confirmed",
                      source="manual", created_by_id=test_user.id)
        db_session.add(row)
        db_session.flush()
        db_session.add_all([FindingHost(finding_id=row.id, host_id=h.id, host_status="open") for h in on])
        db_session.flush()
        return row

    wanted, neighbour = host("10.9.8.1"), host("10.9.8.2")
    others = [host(f"10.9.9.{n}") for n in range(1, 41)]
    ours = finding("on the downloaded host and one more", [wanted, neighbour])
    unrelated = [finding(f"elsewhere {n}", others[n:n + 3]) for n in range(0, 36, 3)]
    db_session.commit()

    gen = ReportGenerator(db=db_session, current_user=MagicMock(id=test_user.id), project_id=test_project.id)
    seen: list = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "finding_hosts" in statement:
            seen.append((statement, parameters))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        rows = gen._findings_for_report_ids([wanted.id])
    finally:
        event.remove(bind, "before_cursor_execute", record)

    # The answer: the one finding, with its WHOLE reach and the downloaded part of it.
    assert [r["id"] for r in rows] == [ours.id]
    assert sorted(rows[0]["affected_hosts"]) == ["10.9.8.1", "10.9.8.2"]
    assert rows[0]["affected"] == [{"ip": "10.9.8.1", "host_id": wanted.id}]
    # The shape: every read of finding_hosts is by a host id or a finding id —
    # none is the project's endpoints — and no unrelated finding's id is sent.
    assert seen, "the export read no endpoint"
    unrelated_ids = {f.id for f in unrelated}
    for statement, parameters in seen:
        assert " IN (" in statement, statement
        if "finding_hosts.finding_id IN (" in statement:
            sent = {value for name, value in parameters.items() if name.startswith("finding_id")}
            assert sent == {ours.id}, (statement, parameters)
            assert not (sent & unrelated_ids)
