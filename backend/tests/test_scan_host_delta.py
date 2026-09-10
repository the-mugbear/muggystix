"""The /scans inventory reports what each scan INTRODUCED, not a re-observation
count: new_hosts (hosts the scan first discovered) vs updated_hosts (already
known hosts it re-observed). The new/updated split is the dedup create/update
decision recorded on HostScanHistory.host_created at ingest.
"""
import pytest

from app.db import models
from app.services.host_deduplication_service import HostDeduplicationService
from tests.conftest import USING_POSTGRES


def _ingest(db, project_id, scan, ip, **host_data):
    svc = HostDeduplicationService(db)
    host = svc.find_or_create_host(ip, scan.id, {"state": "up", **host_data}, project_id=project_id)
    db.flush()
    return host


def test_new_vs_updated_host_counts(client, db_session, test_project):
    pid = test_project.id
    scan_a = models.Scan(project_id=pid, filename="a.xml", tool_name="nmap", scan_type="nmap_xml")
    scan_b = models.Scan(project_id=pid, filename="b.xml", tool_name="nmap", scan_type="nmap_xml")
    db_session.add_all([scan_a, scan_b])
    db_session.flush()

    # Scan A discovers two brand-new hosts.
    _ingest(db_session, pid, scan_a, "10.5.0.1")
    _ingest(db_session, pid, scan_a, "10.5.0.2")
    # Scan B re-observes one of A's hosts (update) and finds one new host.
    _ingest(db_session, pid, scan_b, "10.5.0.1")   # already known -> updated
    _ingest(db_session, pid, scan_b, "10.5.0.3")   # brand new
    db_session.commit()

    # Ground truth: host_created is set exactly on the creating observation.
    created_flags = {
        (h.scan_id, h.host.ip_address): h.host_created
        for h in db_session.query(models.HostScanHistory).all()
    }
    assert created_flags[(scan_a.id, "10.5.0.1")] is True
    assert created_flags[(scan_a.id, "10.5.0.2")] is True
    assert created_flags[(scan_b.id, "10.5.0.1")] is False   # re-observation
    assert created_flags[(scan_b.id, "10.5.0.3")] is True

    rows = {r["id"]: r for r in client.get(f"/api/v1/projects/{pid}/scans/").json()}
    a = rows[scan_a.id]
    assert (a["new_hosts"], a["updated_hosts"], a["total_hosts"]) == (2, 0, 2)
    b = rows[scan_b.id]
    assert (b["new_hosts"], b["updated_hosts"], b["total_hosts"]) == (1, 1, 2)


def test_sort_by_new_hosts(client, db_session, test_project):
    pid = test_project.id
    small = models.Scan(project_id=pid, filename="small.xml", tool_name="nmap", scan_type="nmap_xml")
    big = models.Scan(project_id=pid, filename="big.xml", tool_name="nmap", scan_type="nmap_xml")
    db_session.add_all([small, big])
    db_session.flush()
    _ingest(db_session, pid, small, "10.6.0.1")
    for i in range(3):
        _ingest(db_session, pid, big, f"10.6.1.{i}")
    db_session.commit()

    rows = client.get(f"/api/v1/projects/{pid}/scans/?sort_by=new_hosts&sort_order=desc").json()
    assert [r["id"] for r in rows[:2]] == [big.id, small.id]


@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="MasscanParser's bulk host path is PostgreSQL-specific SQL "
    "(ON CONFLICT ... RETURNING xmax); runs only against the Postgres test DB.",
)
def test_masscan_bulk_path_records_host_created(client, db_session, test_project, tmp_path):
    """v2.332.5 — the masscan parser bypasses the dedup service for
    throughput and wrote host_scan_history rows WITHOUT host_created, so
    every masscan import reported 0 new hosts.  Same contract as the dedup
    path: the scan that inserted the hosts_v2 row is its creator; a
    re-observation is not."""
    from app.parsers.masscan_parser import MasscanParser

    pid = test_project.id
    first = tmp_path / "first.txt"
    first.write_text("open tcp 80 10.7.0.1\nopen tcp 443 10.7.0.2\n")
    second = tmp_path / "second.txt"
    second.write_text("open tcp 22 10.7.0.1\nopen tcp 80 10.7.0.3\n")

    scan_a = MasscanParser(db_session).parse_file(str(first), "first.txt", project_id=pid)
    scan_b = MasscanParser(db_session).parse_file(str(second), "second.txt", project_id=pid)

    created_flags = {
        (h.scan_id, h.host.ip_address): h.host_created
        for h in db_session.query(models.HostScanHistory)
        .filter(models.HostScanHistory.scan_id.in_([scan_a.id, scan_b.id]))
    }
    assert created_flags[(scan_a.id, "10.7.0.1")] is True
    assert created_flags[(scan_a.id, "10.7.0.2")] is True
    assert created_flags[(scan_b.id, "10.7.0.1")] is False   # re-observation
    assert created_flags[(scan_b.id, "10.7.0.3")] is True

    rows = {r["id"]: r for r in client.get(f"/api/v1/projects/{pid}/scans/").json()}
    assert (rows[scan_a.id]["new_hosts"], rows[scan_a.id]["updated_hosts"]) == (2, 0)
    assert (rows[scan_b.id]["new_hosts"], rows[scan_b.id]["updated_hosts"]) == (1, 1)
