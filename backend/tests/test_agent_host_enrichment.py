"""Review 2026-10-01 B4 — the agent host lists count scanner rows with the
Hosts page's own service.

``agent_common.batch_host_enrichment`` had its own GROUP BY over
``vulnerabilities`` (the "second rollup" CLAUDE.md forbids) beside three
results no caller read.  It now calls
``VulnerabilityService.get_bulk_host_vulnerability_summaries``; these tests pin
that the agent's rows and the page's rows state the same per-host counts, and
that the service's counts are what a plain GROUP BY returns (the rollup the
helper used to run).
"""
from __future__ import annotations

from sqlalchemy import event, func

from app.api.v1.endpoints.agent_common import batch_host_enrichment
from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity

SEVERITIES = ("critical", "high", "medium", "low")

# host ip -> rows per severity.  Uneven on purpose: a host with every
# severity, one with a single severity, one with only informational rows
# (present in the table, absent from the four counts) and one with none.
_FIXTURE = {
    "10.77.0.1": {"critical": 3, "high": 2, "medium": 4, "low": 1, "info": 5},
    "10.77.0.2": {"high": 7},
    "10.77.0.3": {"info": 2},
    "10.77.0.4": {},
}
_OPEN_PORTS = {"10.77.0.1": 3, "10.77.0.2": 1, "10.77.0.3": 0, "10.77.0.4": 2}


def _seed(db_session, project):
    scan = models.Scan(project_id=project.id, filename="enrich.nessus", scan_type="nessus")
    db_session.add(scan)
    db_session.flush()
    hosts = {}
    for ip, counts in _FIXTURE.items():
        host = models.Host(project_id=project.id, ip_address=ip, state="up")
        db_session.add(host)
        db_session.flush()
        hosts[ip] = host
        for n in range(_OPEN_PORTS[ip]):
            db_session.add(models.Port(
                host_id=host.id, port_number=8000 + n, protocol="tcp", state="open",
            ))
        # A closed port never counts as open.
        db_session.add(models.Port(
            host_id=host.id, port_number=9, protocol="tcp", state="closed",
        ))
        for severity, count in counts.items():
            for n in range(count):
                db_session.add(Vulnerability(
                    host_id=host.id, scan_id=scan.id, title=f"{severity}-{n}",
                    severity=VulnerabilitySeverity(severity), source="nessus",
                    plugin_id=f"{ip}-{severity}-{n}",
                ))
    db_session.commit()
    return hosts


def _assist(client, project_id):
    body = client.post(f"/api/v1/projects/{project_id}/assist/start", json={}).json()
    return {"X-API-Key": body["api_key"]}


def _expected(ip):
    return {s: _FIXTURE[ip].get(s, 0) for s in SEVERITIES}


def test_rollup_equals_a_plain_group_by(db_session, test_project):
    """The service's per-severity counts are the counts the removed GROUP BY
    produced — the swap changed which code counts, not the numbers."""
    hosts = _seed(db_session, test_project)
    host_ids = [h.id for h in hosts.values()]

    grouped = {}
    for hid, sev, cnt in (
        db_session.query(Vulnerability.host_id, Vulnerability.severity, func.count(Vulnerability.id))
        .filter(Vulnerability.host_id.in_(host_ids))
        .group_by(Vulnerability.host_id, Vulnerability.severity)
        .all()
    ):
        grouped.setdefault(hid, {})[sev.value] = cnt

    port_counts, vuln_map = batch_host_enrichment(db_session, host_ids)
    for ip, host in hosts.items():
        got = vuln_map.get(host.id, {})
        was = grouped.get(host.id, {})
        for severity in SEVERITIES + ("info",):
            assert got.get(severity, 0) == was.get(severity, 0), (ip, severity)
        assert port_counts.get(host.id, 0) == _OPEN_PORTS[ip], ip


def test_empty_page_runs_no_query(db_session):
    assert batch_host_enrichment(db_session, []) == ({}, {})


def test_enrichment_is_two_statements(db_session, test_project):
    """Open ports + the severity rollup — the DISTINCT services query nobody
    read is gone."""
    from tests.conftest import engine

    hosts = _seed(db_session, test_project)
    host_ids = [h.id for h in hosts.values()]
    statements = []

    def _before(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    try:
        batch_host_enrichment(db_session, host_ids)
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    assert len(statements) == 2, statements


def test_agent_host_lists_and_the_hosts_page_state_the_same_counts(
    client, db_session, test_project
):
    hosts = _seed(db_session, test_project)
    headers = _assist(client, test_project.id)

    page = client.get(f"/api/v1/projects/{test_project.id}/hosts/?limit=100")
    assert page.status_code == 200, page.text
    page_rows = {row["ip_address"]: row for row in page.json()["items"]}

    assist = client.get("/api/v1/agent/assist/hosts?limit=100", headers=headers)
    assert assist.status_code == 200, assist.text
    assist_rows = {row["ip_address"]: row for row in assist.json()["items"]}

    browse = client.get("/api/v1/agent/hosts?limit=100", headers=headers)
    assert browse.status_code == 200, browse.text
    browse_rows = {row["ip_address"]: row for row in browse.json()}

    for ip, host in hosts.items():
        expected = _expected(ip)
        # The page sends no summary at all for a host without scanner rows.
        page_summary = page_rows[ip]["vulnerability_summary"] or {}
        if _FIXTURE[ip]:
            assert page_summary, ip
        for severity in SEVERITIES:
            assert page_summary.get(severity, 0) == expected[severity], (ip, severity)
        for label, rows in (("assist", assist_rows), ("browse", browse_rows)):
            _same(rows[ip]["vuln_summary"], ip, label)
            assert rows[ip]["open_port_count"] == _OPEN_PORTS[ip], (label, ip)

        # The single-host reads go through the same helper.
        detail = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=headers)
        assert detail.status_code == 200, detail.text
        _same(detail.json()["vuln_summary"], ip, "assist detail")
        detail = client.get(f"/api/v1/agent/hosts/{host.id}", headers=headers)
        assert detail.status_code == 200, detail.text
        _same(detail.json()["vuln_summary"], ip, "browse detail")


def _same(summary, ip, label):
    """An agent row's ``vuln_summary`` against the fixture.  Like the page,
    the agent reads send none for a host without scanner rows — and a host
    with only informational rows gets a summary of zeros, not none."""
    if not _FIXTURE[ip]:
        assert summary is None, (label, ip)
        return
    assert summary is not None, (label, ip)
    assert {s: summary[s] for s in SEVERITIES} == _expected(ip), (label, ip)
