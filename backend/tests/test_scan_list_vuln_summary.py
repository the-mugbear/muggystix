"""Regression: the Findings column counts vulns from EVERY tool, not Nessus.

Reported from prod — OpenVAS scans rendered "No findings" on /scans despite
having findings. The list endpoint pre-filtered its vulnerability aggregate to
scans whose ``tool_name``/``scan_type`` contained "nessus", so the OpenVAS
parser's ``tool_name="openvas"`` (and Nikto's, and nmap's) never matched and
``vulnerability_summary`` came back None. The frontend renders None as the
literal string "No findings", which is how a data-shape bug read as an empty
scan.

The gate was never load-bearing: ``Vulnerability.scan_id.in_(scan_ids)`` is the
real restriction and a scan without vulns just has no row.
"""
from datetime import datetime, timezone

import pytest

from app.db import models
from app.db.models_vulnerability import (
    Vulnerability,
    VulnerabilitySeverity,
    VulnerabilitySource,
)


def _scan_with_finding(db, project_id, *, filename, tool_name, scan_type, source, ip):
    scan = models.Scan(
        project_id=project_id,
        filename=filename,
        tool_name=tool_name,
        scan_type=scan_type,
    )
    db.add(scan)
    db.flush()

    host = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    db.add(models.HostScanHistory(
        host_id=host.id,
        scan_id=scan.id,
        state_at_scan="up",
        discovered_at=datetime.now(timezone.utc),
    ))
    db.add(Vulnerability(
        host_id=host.id,
        scan_id=scan.id,
        title=f"{tool_name} finding",
        severity=VulnerabilitySeverity.HIGH,
        source=source,
    ))
    db.commit()
    return scan


@pytest.mark.parametrize(
    "tool_name,scan_type,source,ip",
    [
        ("openvas", "vulnerability_scan", VulnerabilitySource.OPENVAS, "10.9.0.1"),
        ("nikto", "web_vulnerability_scan", VulnerabilitySource.NIKTO, "10.9.0.2"),
        ("nmap", "nmap", VulnerabilitySource.NMAP, "10.9.0.3"),
        ("Nessus", "nessus", VulnerabilitySource.NESSUS, "10.9.0.4"),
    ],
)
def test_every_tool_reports_its_findings(
    client, db_session, test_project, tool_name, scan_type, source, ip,
):
    pid = test_project.id
    scan = _scan_with_finding(
        db_session, pid,
        filename=f"{tool_name}-report.xml",
        tool_name=tool_name, scan_type=scan_type, source=source, ip=ip,
    )

    rows = client.get(f"/api/v1/projects/{pid}/scans/").json()
    row = next(r for r in rows if r["id"] == scan.id)

    assert row["vulnerability_summary"] is not None, (
        f"{tool_name} scan reported no vulnerability_summary — the Scans page "
        'renders that as the literal "No findings"'
    )
    assert row["vulnerability_summary"]["total"] == 1
    assert row["vulnerability_summary"]["high"] == 1


def test_a_scan_with_no_findings_still_reports_none(client, db_session, test_project):
    """The other half of the contract: dropping the tool gate must not start
    inventing an empty summary for scans that genuinely found nothing —
    ``None`` is what the column keys its "No findings" fallback off."""
    pid = test_project.id
    scan = models.Scan(
        project_id=pid, filename="discovery.xml", tool_name="nmap", scan_type="nmap",
    )
    db_session.add(scan)
    db_session.flush()
    host = models.Host(project_id=pid, ip_address="10.9.1.1", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(models.HostScanHistory(
        host_id=host.id, scan_id=scan.id, state_at_scan="up",
        discovered_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    rows = client.get(f"/api/v1/projects/{pid}/scans/").json()
    row = next(r for r in rows if r["id"] == scan.id)
    assert row["vulnerability_summary"] is None
