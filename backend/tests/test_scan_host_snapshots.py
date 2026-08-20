"""The Scan Detail host table must report what the scan observed.

Counterpart to the count fix in v2.298.0. The page rendered the *current* Host
rows of everything the scan had ever seen — current state, current hostname,
and every port the host has today — under headings that read as a record of the
scan. So a host that was down on the day and is up now showed as up "in" that
scan, and ports discovered months later appeared inside it.

These pin the property that makes a scan usable as evidence: the response does
not change when the world moves on.
"""
from datetime import datetime, timezone

from app.db import models


def _scan(db, project_id, filename):
    scan = models.Scan(project_id=project_id, filename=filename, tool_name="nmap")
    db.add(scan)
    db.flush()
    return scan


def _observe(db, scan, host, *, state, hostname=None, created=False):
    db.add(models.HostScanHistory(
        host_id=host.id, scan_id=scan.id, state_at_scan=state,
        hostname_at_scan=hostname, host_created=created,
        discovered_at=datetime.now(timezone.utc),
    ))


def _port(db, host, number, *, current_state):
    port = models.Port(
        host_id=host.id, port_number=number, protocol="tcp", state=current_state,
        service_name="http",
    )
    db.add(port)
    db.flush()
    return port


def test_snapshot_reports_observed_state_not_current_state(
    client, db_session, test_project
):
    """The host was DOWN when scanned and is up today; the port was OPEN when
    scanned and is closed today. The scan record must say down/open."""
    pid = test_project.id
    scan = _scan(db_session, pid, "audit.xml")
    host = models.Host(
        project_id=pid, ip_address="10.40.0.1", state="up", hostname="renamed-since.example",
    )
    db_session.add(host)
    db_session.flush()
    _observe(db_session, scan, host, state="down", hostname="original.example")

    port = _port(db_session, host, 8080, current_state="closed")
    db_session.add(models.PortScanHistory(
        port_id=port.id, scan_id=scan.id, state_at_scan="open",
    ))
    db_session.commit()

    body = client.get(
        f"/api/v1/projects/{pid}/scans/{scan.id}/host-snapshots"
    ).json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["state_at_scan"] == "down", "current Host.state leaked into the scan record"
    assert row["hostname_at_scan"] == "original.example"
    assert row["open_port_count"] == 1, "the scan found it open; that is a fact about the past"
    assert row["ports"][0]["state_at_scan"] == "open"


def test_a_later_scans_discovery_does_not_appear_in_an_earlier_snapshot(
    client, db_session, test_project
):
    """The per-host version of the count regression: ports are listed by
    observation, so a port found on Tuesday is absent from Monday's record."""
    pid = test_project.id
    monday = _scan(db_session, pid, "monday.xml")
    tuesday = _scan(db_session, pid, "tuesday.xml")
    host = models.Host(project_id=pid, ip_address="10.40.1.1", state="up")
    db_session.add(host)
    db_session.flush()
    _observe(db_session, monday, host, state="up", created=True)
    _observe(db_session, tuesday, host, state="up")

    p80 = _port(db_session, host, 80, current_state="open")
    p443 = _port(db_session, host, 443, current_state="open")
    db_session.add(models.PortScanHistory(port_id=p80.id, scan_id=monday.id, state_at_scan="open"))
    db_session.add(models.PortScanHistory(port_id=p80.id, scan_id=tuesday.id, state_at_scan="open"))
    db_session.add(models.PortScanHistory(port_id=p443.id, scan_id=tuesday.id, state_at_scan="open"))
    db_session.commit()

    mon = client.get(f"/api/v1/projects/{pid}/scans/{monday.id}/host-snapshots").json()
    tue = client.get(f"/api/v1/projects/{pid}/scans/{tuesday.id}/host-snapshots").json()

    assert [p["port_number"] for p in mon["items"][0]["ports"]] == [80]
    assert [p["port_number"] for p in tue["items"][0]["ports"]] == [80, 443]
    # host_created marks the scan that introduced the host, so an analyst can
    # tell "first seen here" from "seen again".
    assert mon["items"][0]["host_created"] is True
    assert tue["items"][0]["host_created"] is False


def test_state_filter_applies_to_the_observed_state(client, db_session, test_project):
    """Filtering by state must mean "was down when scanned", not "is down now"
    — otherwise the filter contradicts the column beside it."""
    pid = test_project.id
    scan = _scan(db_session, pid, "mixed.xml")
    up_then_down = models.Host(project_id=pid, ip_address="10.40.2.1", state="down")
    down_then_up = models.Host(project_id=pid, ip_address="10.40.2.2", state="up")
    db_session.add_all([up_then_down, down_then_up])
    db_session.flush()
    _observe(db_session, scan, up_then_down, state="up")
    _observe(db_session, scan, down_then_up, state="down")
    db_session.commit()

    body = client.get(
        f"/api/v1/projects/{pid}/scans/{scan.id}/host-snapshots?state=up"
    ).json()
    assert [r["ip_address"] for r in body["items"]] == ["10.40.2.1"]


def test_snapshot_is_project_scoped(client, db_session, test_project):
    from app.db.models_project import Project

    other = Project(name="Other", slug="other-scan-snapshot")
    db_session.add(other)
    db_session.commit()
    foreign = _scan(db_session, other.id, "theirs.xml")
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/scans/{foreign.id}/host-snapshots"
    )
    assert resp.status_code == 404
