"""Regression: sort_by=ip_address must order IPs numerically (by octet), not
lexicographically. String order puts 10.0.0.10 before 10.0.0.2 and 10.x before
9.x; Postgres' inet cast fixes it. Gated to Postgres (SQLite has no inet and
falls back to the string column).
"""
from __future__ import annotations

import pytest

from app.db import models


def test_ip_address_sort_is_numeric_not_lexicographic(client, db_session, test_project):
    if db_session.bind.dialect.name != "postgresql":
        pytest.skip("inet ordering is Postgres-only; SQLite falls back to string sort")

    for ip in ["10.0.0.2", "10.0.0.10", "9.0.0.1", "192.168.1.10", "192.168.1.2"]:
        db_session.add(models.Host(project_id=test_project.id, ip_address=ip, state="up"))
    db_session.flush()

    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/",
        params={"sort_by": "ip_address", "sort_order": "asc", "limit": 100},
    )
    assert r.status_code == 200, r.text
    ips = [h["ip_address"] for h in r.json()["items"]]
    assert ips == ["9.0.0.1", "10.0.0.2", "10.0.0.10", "192.168.1.2", "192.168.1.10"], ips


def test_sorting_by_address_does_not_count_vulnerabilities_and_ports_per_host(db_session, test_project, test_user):
    """Review 2026-10-07: an address is unique in a project, so the two count
    tiebreakers could never change the order — they were still evaluated for
    every matching host.  Other sorts keep them."""
    from app.services.host_query import apply_host_sorting, build_filtered_host_query

    def order_by(sort_by):
        query = apply_host_sorting(
            build_filtered_host_query(db_session, test_user, project_id=test_project.id), sort_by, "asc")
        sql = str(query.statement.compile(dialect=db_session.get_bind().dialect))
        return sql[sql.index("ORDER BY"):]

    by_address = order_by("ip_address")
    assert "vulnerabilities" not in by_address and "ports_v2" not in by_address, by_address
    assert by_address.rstrip().endswith("hosts_v2.id ASC"), by_address
    assert "vulnerabilities" in order_by("hostname")


def test_the_host_stream_pages_by_key_and_gives_every_host_once(client, db_session, test_project):
    """More than two pages of the agents' NDJSON stream: every host once, in
    order, and no page asked for with OFFSET."""
    import json

    from sqlalchemy import event

    total = 1100
    db_session.add_all([
        models.Host(project_id=test_project.id, ip_address=f"10.{n // 250}.{n % 250}.7", state="up")
        for n in range(total)
    ])
    db_session.commit()
    key = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={}).json()["api_key"]

    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db_session.get_bind().engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        stream = client.get("/api/v1/agent/assist/hosts.ndjson", headers={"X-API-Key": key})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert stream.status_code == 200, stream.text
    ips = [json.loads(line)["ip_address"] for line in stream.text.splitlines() if line]
    assert len(ips) == total and len(set(ips)) == total
    host_pages = [s for s in statements if s.startswith("SELECT hosts_v2.") and "LIMIT" in s]
    assert len(host_pages) == 3, len(host_pages)
    assert not [s for s in host_pages if "OFFSET" in s], host_pages


def test_exploitable_vulns_sort_orders_by_exploitable_count(client, db_session, test_project):
    """sort_by=exploitable_vulns ranks hosts by their count of known-exploitable
    vulns (mirrors the has_exploit_available filter + exploitable_count). The
    'exploitable first' triage sort the Attention column is built around (B3-2)."""
    from app.db.models_vulnerability import (
        Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
    )

    scan = models.Scan(project_id=test_project.id, filename="s.xml",
                       tool_name="Nessus", scan_type="nessus")
    db_session.add(scan)
    db_session.flush()

    hosts = {}
    for ip in ["10.1.0.1", "10.1.0.2", "10.1.0.3"]:
        h = models.Host(project_id=test_project.id, ip_address=ip, state="up")
        db_session.add(h)
        hosts[ip] = h
    db_session.flush()

    def add_vulns(host, *, exploitable, n):
        for i in range(n):
            db_session.add(Vulnerability(
                title=f"v{i}", severity=VulnerabilitySeverity.HIGH,
                source=VulnerabilitySource.NESSUS, host_id=host.id,
                scan_id=scan.id, exploitable=exploitable,
            ))

    add_vulns(hosts["10.1.0.1"], exploitable=True, n=2)   # 2 exploitable
    add_vulns(hosts["10.1.0.2"], exploitable=False, n=3)  # 0 exploitable
    add_vulns(hosts["10.1.0.3"], exploitable=True, n=1)   # 1 exploitable
    db_session.flush()

    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/",
        params={"sort_by": "exploitable_vulns", "sort_order": "desc", "limit": 100},
    )
    assert r.status_code == 200, r.text
    ips = [h["ip_address"] for h in r.json()["items"]]
    assert ips[:3] == ["10.1.0.1", "10.1.0.3", "10.1.0.2"], ips
