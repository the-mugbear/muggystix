"""The /scans inventory reports what each scan INTRODUCED, not a re-observation
count: new_hosts (hosts the scan first discovered) vs updated_hosts (already
known hosts it re-observed). The new/updated split is the dedup create/update
decision recorded on HostScanHistory.host_created at ingest.
"""
from app.db import models
from app.services.host_deduplication_service import HostDeduplicationService


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
