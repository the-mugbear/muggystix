"""GET /scans/{id}/deletion-impact previews exactly what a delete removes.

Hosts are deduplicated per-IP-per-project, so deleting a scan removes only
hosts seen by NO other scan ("orphans"); hosts shared with other scans are
kept. This pins that rule (and the scan-scoped vuln/web counts) so the
delete-confirmation modal can tell the truth.
"""
from datetime import datetime, timezone

from app.db import models
from app.db.models_vulnerability import (
    Vulnerability,
    VulnerabilitySeverity,
    VulnerabilitySource,
)


def _seen(db, pid, scan, ip, state="up"):
    host = models.Host(project_id=pid, ip_address=ip, state=state)
    db.add(host)
    db.flush()
    db.add(models.HostScanHistory(
        host_id=host.id, scan_id=scan.id, state_at_scan=state,
        discovered_at=datetime.now(timezone.utc),
    ))
    return host


def test_deletion_impact_counts_only_what_delete_removes(client, db_session, test_project):
    pid = test_project.id
    target = models.Scan(project_id=pid, filename="target.xml", tool_name="nmap", scan_type="nmap_xml")
    other = models.Scan(project_id=pid, filename="other.xml", tool_name="nmap", scan_type="nmap_xml")
    db_session.add_all([target, other])
    db_session.flush()

    # h_orphan: seen ONLY by the target scan -> will be removed.
    h_orphan = _seen(db_session, pid, target, "10.9.0.1")
    # h_shared: seen by BOTH scans -> kept, only re-pointed.
    h_shared = _seen(db_session, pid, target, "10.9.0.2")
    db_session.add(models.HostScanHistory(
        host_id=h_shared.id, scan_id=other.id, state_at_scan="up",
        discovered_at=datetime.now(timezone.utc),
    ))

    # Ports: only the orphan host's ports are removed.
    db_session.add(models.Port(host_id=h_orphan.id, port_number=80, protocol="tcp", state="open"))
    db_session.add(models.Port(host_id=h_orphan.id, port_number=443, protocol="tcp", state="open"))
    db_session.add(models.Port(host_id=h_shared.id, port_number=22, protocol="tcp", state="open"))

    # Vulns/web are scan-scoped (scan_id CASCADE): a vuln recorded by the
    # target scan on the SHARED host is still removed.
    for host in (h_orphan, h_shared):
        db_session.add(Vulnerability(
            host_id=host.id, scan_id=target.id, title="x",
            severity=VulnerabilitySeverity.HIGH, source=VulnerabilitySource.NESSUS,
        ))
    # A vuln recorded by the OTHER scan must NOT be counted.
    db_session.add(Vulnerability(
        host_id=h_shared.id, scan_id=other.id, title="keep",
        severity=VulnerabilitySeverity.LOW, source=VulnerabilitySource.NESSUS,
    ))
    db_session.add(models.WebInterface(
        scan_id=target.id, host_id=h_orphan.id, source="httpx", url="http://10.9.0.1/",
    ))
    db_session.commit()

    body = client.get(f"/api/v1/projects/{pid}/scans/{target.id}/deletion-impact").json()
    assert body["hosts_removed"] == 1
    assert body["hosts_kept"] == 1
    assert body["sample_removed_ips"] == ["10.9.0.1"]
    assert body["ports_removed"] == 2          # only the orphan host's ports
    assert body["vulnerabilities_removed"] == 2  # both target-scan vulns, not the other-scan one
    assert body["web_interfaces_removed"] == 1


def test_deletion_impact_404_for_unknown_scan(client, test_project):
    resp = client.get(f"/api/v1/projects/{test_project.id}/scans/999999/deletion-impact")
    assert resp.status_code == 404
