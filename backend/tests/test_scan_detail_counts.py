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

    # v2.298.0 — ports are counted from what the scan OBSERVED
    # (PortScanHistory.state_at_scan), so the fixture records the observation
    # alongside the port, exactly as HostDeduplicationService does at ingest.
    # Previously this test seeded current Port rows only, which passed by
    # asserting the very current-state semantics that made a scan's counts
    # change whenever a LATER scan touched the same host.
    def observed(host, number, state):
        port = models.Port(host_id=host.id, port_number=number, protocol="tcp", state=state)
        db_session.add(port)
        db_session.flush()
        db_session.add(models.PortScanHistory(
            port_id=port.id, scan_id=scan.id, state_at_scan=state,
        ))
        return port

    observed(h1, 80, "open")
    observed(h1, 443, "open")
    observed(h2, 22, "closed")
    db_session.commit()

    body = client.get(f"/api/v1/projects/{pid}/scans/{scan.id}").json()
    assert body["total_hosts"] == 3
    assert body["up_hosts"] == 2
    assert body["total_ports"] == 3
    assert body["open_ports"] == 2


def test_a_later_scan_does_not_rewrite_an_earlier_scans_port_counts(
    client, db_session, test_project
):
    """A scan is evidence of a moment. It must not change afterwards.

    Pre-v2.298.0 the per-scan port counts were aggregated from the *current*
    Port rows of every host the scan had ever seen, so a port first discovered
    on Tuesday was retroactively counted in Monday's scan, and closing a port
    today rewrote what last month's scan "found". Both endpoints are pinned
    here because they had separate copies of the same query.
    """
    pid = test_project.id
    monday = models.Scan(project_id=pid, filename="monday.xml", tool_name="nmap")
    tuesday = models.Scan(project_id=pid, filename="tuesday.xml", tool_name="nmap")
    db_session.add_all([monday, tuesday])
    db_session.flush()

    host = models.Host(project_id=pid, ip_address="10.8.0.1", state="up")
    db_session.add(host)
    db_session.flush()
    for scan in (monday, tuesday):
        db_session.add(models.HostScanHistory(
            host_id=host.id, scan_id=scan.id, state_at_scan="up",
            discovered_at=datetime.now(timezone.utc),
        ))

    # Monday saw one open port.
    p80 = models.Port(host_id=host.id, port_number=80, protocol="tcp", state="open")
    db_session.add(p80)
    db_session.flush()
    db_session.add(models.PortScanHistory(
        port_id=p80.id, scan_id=monday.id, state_at_scan="open",
    ))

    # Tuesday saw that port still open AND discovered a second one. The new
    # port row exists on the host now — the bug was letting it count backwards.
    p443 = models.Port(host_id=host.id, port_number=443, protocol="tcp", state="open")
    db_session.add(p443)
    db_session.flush()
    db_session.add(models.PortScanHistory(
        port_id=p80.id, scan_id=tuesday.id, state_at_scan="open",
    ))
    db_session.add(models.PortScanHistory(
        port_id=p443.id, scan_id=tuesday.id, state_at_scan="open",
    ))
    db_session.commit()

    detail = client.get(f"/api/v1/projects/{pid}/scans/{monday.id}").json()
    assert detail["total_ports"] == 1, "Tuesday's discovery leaked into Monday"
    assert detail["open_ports"] == 1

    rows = {r["id"]: r for r in client.get(f"/api/v1/projects/{pid}/scans/").json()}
    assert rows[monday.id]["open_ports"] == 1
    assert rows[tuesday.id]["open_ports"] == 2


def test_closing_a_port_today_does_not_rewrite_what_a_scan_found(
    client, db_session, test_project
):
    """The other direction: remediation must not erase the evidence that the
    port was open when it was scanned."""
    pid = test_project.id
    scan = models.Scan(project_id=pid, filename="audit.xml", tool_name="nmap")
    db_session.add(scan)
    db_session.flush()
    host = models.Host(project_id=pid, ip_address="10.8.1.1", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(models.HostScanHistory(
        host_id=host.id, scan_id=scan.id, state_at_scan="up",
        discovered_at=datetime.now(timezone.utc),
    ))
    # Observed open at scan time; since remediated, so the CURRENT row is closed.
    port = models.Port(host_id=host.id, port_number=23, protocol="tcp", state="closed")
    db_session.add(port)
    db_session.flush()
    db_session.add(models.PortScanHistory(
        port_id=port.id, scan_id=scan.id, state_at_scan="open",
    ))
    db_session.commit()

    body = client.get(f"/api/v1/projects/{pid}/scans/{scan.id}").json()
    assert body["open_ports"] == 1, "the scan found telnet open; that is a fact about the past"
