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
