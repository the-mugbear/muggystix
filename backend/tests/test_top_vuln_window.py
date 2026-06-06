"""Regression test for v2.90.4 code-review #3 — top-5 vulnerability
enrichment rewritten to use a window function.

Pre-fix: ``_batch_host_enrichment`` in agent_common.py did
``.filter(severity in critical/high).order_by(severity).all()``,
materialising every critical/high vulnerability for up to 2000 hosts
before trimming to 5/host in Python.

Post-fix: a single window-function subquery
(``ROW_NUMBER() OVER (PARTITION BY host_id ORDER BY severity ASC,
cvss_score DESC, id ASC)``) returns at most 5 IDs per host directly
from the database; a second query hydrates them.

This test asserts:
  * On a host with > 5 critical/high vulnerabilities, exactly 5 are
    returned (truncation works).
  * The 5 returned are the highest-severity / highest-cvss ones
    (ordering preserved by the window function's ORDER BY).
  * Hosts with ≤ 5 are returned in full.
  * Medium / low / info severities are excluded (filter unchanged).
"""
from __future__ import annotations

from app.api.v1.endpoints.agent_common import _batch_host_enrichment
from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity


def _ensure_scan(db_session, project):
    scan = (
        db_session.query(models.Scan)
        .filter(models.Scan.project_id == project.id, models.Scan.filename == "vuln-fixt.xml")
        .first()
    )
    if scan is None:
        scan = models.Scan(
            project_id=project.id, filename="vuln-fixt.xml", scan_type="nessus",
        )
        db_session.add(scan)
        db_session.flush()
    return scan


def _make_vuln(db_session, host, severity, cvss_score, title, *, scan):
    v = Vulnerability(
        host_id=host.id,
        scan_id=scan.id,
        title=title,
        severity=severity,
        cvss_score=cvss_score,
        source="nessus",
        plugin_id=f"{title}-pid",
    )
    db_session.add(v)
    return v


def test_top_vulns_caps_at_5_per_host(db_session, test_project):
    """Host with 8 high-severity vulns → exactly 5 returned."""
    host = models.Host(
        project_id=test_project.id,
        ip_address="10.0.0.50",
        state="up",
    )
    db_session.add(host)
    db_session.flush()
    scan = _ensure_scan(db_session, test_project)

    for i in range(8):
        # Vary CVSS so we can verify ordering: 9.5, 9.4, 9.3, ...
        _make_vuln(
            db_session, host,
            severity=VulnerabilitySeverity.HIGH,
            cvss_score=9.5 - i * 0.1,
            title=f"high-{i}",
            scan=scan,
        )
    db_session.flush()

    port_counts, vuln_map, svc_map, port_details, top_vulns = _batch_host_enrichment(
        db_session, [host.id], include_ports=True,
    )
    assert host.id in top_vulns
    assert len(top_vulns[host.id]) == 5
    # Highest CVSS first (descending) — top 5 should be 9.5..9.1.
    returned_cvss = sorted([v.cvss_score for v in top_vulns[host.id]], reverse=True)
    assert returned_cvss == [9.5, 9.4, 9.3, 9.2, 9.1]


def test_top_vulns_prefers_critical_over_high(db_session, test_project):
    """When a host has both critical and high findings, critical fill
    the bucket first (severity ASC: 'critical' < 'high')."""
    host = models.Host(
        project_id=test_project.id,
        ip_address="10.0.0.51",
        state="up",
    )
    db_session.add(host)
    db_session.flush()
    scan = _ensure_scan(db_session, test_project)
    # 3 critical (CVSS 9.x) + 4 high (CVSS 8.x)
    for i in range(3):
        _make_vuln(
            db_session, host,
            severity=VulnerabilitySeverity.CRITICAL,
            cvss_score=9.0 + i * 0.1,
            title=f"crit-{i}",
            scan=scan,
        )
    for i in range(4):
        _make_vuln(
            db_session, host,
            severity=VulnerabilitySeverity.HIGH,
            cvss_score=8.0 + i * 0.1,
            title=f"high-{i}",
            scan=scan,
        )
    db_session.flush()
    _, _, _, _, top_vulns = _batch_host_enrichment(
        db_session, [host.id], include_ports=True,
    )
    # 5 total: 3 critical + 2 highest high.
    assert len(top_vulns[host.id]) == 5
    by_sev = {}
    for v in top_vulns[host.id]:
        by_sev[v.severity] = by_sev.get(v.severity, 0) + 1
    assert by_sev[VulnerabilitySeverity.CRITICAL] == 3
    assert by_sev[VulnerabilitySeverity.HIGH] == 2


def test_top_vulns_excludes_medium_and_below(db_session, test_project):
    """Medium / low / info vulns must not appear in top_vulns."""
    host = models.Host(
        project_id=test_project.id,
        ip_address="10.0.0.52",
        state="up",
    )
    db_session.add(host)
    db_session.flush()
    scan = _ensure_scan(db_session, test_project)
    _make_vuln(
        db_session, host,
        severity=VulnerabilitySeverity.MEDIUM, cvss_score=6.0, title="med", scan=scan,
    )
    _make_vuln(
        db_session, host,
        severity=VulnerabilitySeverity.LOW, cvss_score=3.0, title="low", scan=scan,
    )
    _make_vuln(
        db_session, host,
        severity=VulnerabilitySeverity.HIGH, cvss_score=8.0, title="high", scan=scan,
    )
    db_session.flush()
    _, _, _, _, top_vulns = _batch_host_enrichment(
        db_session, [host.id], include_ports=True,
    )
    severities = {v.severity for v in top_vulns[host.id]}
    assert severities == {VulnerabilitySeverity.HIGH}


def test_top_vulns_no_include_ports_returns_empty(db_session, test_project):
    """top_vulns is computed only when include_ports=True (the context
    endpoint's gated path).  Brief responses skip the enrichment."""
    host = models.Host(
        project_id=test_project.id,
        ip_address="10.0.0.53",
        state="up",
    )
    db_session.add(host)
    db_session.flush()
    scan = _ensure_scan(db_session, test_project)
    _make_vuln(
        db_session, host,
        severity=VulnerabilitySeverity.CRITICAL, cvss_score=10.0, title="c1", scan=scan,
    )
    db_session.flush()
    _, _, _, _, top_vulns = _batch_host_enrichment(
        db_session, [host.id], include_ports=False,
    )
    assert top_vulns == {}
