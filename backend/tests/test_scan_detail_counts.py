"""Regression test: GET /scans/{id} returns accurate aggregate counts.

The scan detail page derived its title-card counts from the fetched host
list (capped at the getHostsByScan limit), so a >1000-host scan showed
"1000/1000 up" while the /scans list badge showed the true total.  The
fix attaches HostScanHistory-based aggregates to the get_scan response;
this pins host/up/port/open counts independent of any fetch cap.
"""
from datetime import datetime, timezone

from app.db import models


def test_get_scan_reports_aggregate_counts(client, db_session, test_project):
    pid = test_project.id
    scan = models.Scan(project_id=pid, filename="big.xml", tool_name="nmap", scan_type="nmap_xml")
    db_session.add(scan)
    db_session.flush()

    def seen(ip, state):
        host = models.Host(project_id=pid, ip_address=ip, state=state)
        db_session.add(host)
        db_session.flush()
        db_session.add(models.HostScanHistory(
            host_id=host.id, scan_id=scan.id, state_at_scan=state,
            discovered_at=datetime.now(timezone.utc),
        ))
        return host

    h1 = seen("10.7.0.1", "up")
    h2 = seen("10.7.0.2", "up")
    seen("10.7.0.3", "down")  # down host, no ports

    for num, state in [(80, "open"), (443, "open")]:
        db_session.add(models.Port(host_id=h1.id, port_number=num, protocol="tcp", state=state))
    db_session.add(models.Port(host_id=h2.id, port_number=22, protocol="tcp", state="closed"))
    db_session.commit()

    body = client.get(f"/api/v1/projects/{pid}/scans/{scan.id}").json()
    assert body["total_hosts"] == 3
    assert body["up_hosts"] == 2
    assert body["total_ports"] == 3
    assert body["open_ports"] == 2
