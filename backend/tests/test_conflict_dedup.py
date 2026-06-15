"""Regression tests for the conflict_history dedup fix.

Bug: HostDeduplicationService._record_conflict inserted a ConflictHistory
row unconditionally, so the SAME disagreement (held value vs reported
value) accumulated a fresh row on every re-scan — and even multiple times
within one scan under the session's autoflush=False.  That inflated the
host conflict count and made the host-detail "Resolution history" list the
same line many times.  _record_conflict is now idempotent on
(object, field, previous_value, new_value).
"""
from app.db import models
from app.db.models_confidence import ConflictHistory
from app.services.host_deduplication_service import HostDeduplicationService


def _mk_host_and_scan(db, project_id, ip):
    host = models.Host(ip_address=ip, state="up", project_id=project_id)
    db.add(host)
    scan = models.Scan(project_id=project_id, filename="n.xml", tool_name="Nmap", scan_type="nmap")
    db.add(scan)
    db.flush()
    return host, scan


def _conflicts(db, host_id):
    return db.query(ConflictHistory).filter(ConflictHistory.host_id == host_id).all()


def test_identical_conflict_recorded_once(db_session, test_project):
    """The same held-vs-reported disagreement repeated → exactly one row."""
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.9.1.1")
    svc = HostDeduplicationService(db_session)

    for _ in range(5):
        svc._record_conflict("host", host.id, "hostname", "a.lab.local", "b.lab.local", scan.id, scan.id)

    rows = _conflicts(db_session, host.id)
    assert len(rows) == 1
    # The single row tracks the latest disagreeing scan.
    assert rows[0].new_scan_id == scan.id


def test_distinct_conflicts_kept_separate(db_session, test_project):
    """Different reported values are genuinely different conflicts → kept."""
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.9.1.2")
    svc = HostDeduplicationService(db_session)

    svc._record_conflict("host", host.id, "hostname", "a.lab.local", "b.lab.local", scan.id, scan.id)
    svc._record_conflict("host", host.id, "hostname", "a.lab.local", "c.lab.local", scan.id, scan.id)
    svc._record_conflict("host", host.id, "os_name", "Linux", "Windows", scan.id, scan.id)

    assert len(_conflicts(db_session, host.id)) == 3


# --- Scan-history intra-scan dedup (review A-3) ------------------------------
# The O(n^2) db.new scan that caught an unflushed same-(id,scan) history row
# was replaced by an O(1) dict.  These pin the property it must preserve:
# a repeat within one scan UPDATES the row (never inserts a duplicate that
# would violate uq_host_scan / uq_port_scan), while a different scan gets its
# own row.

def test_host_scan_history_deduped_within_one_scan(db_session, test_project):
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.9.2.1")
    svc = HostDeduplicationService(db_session)
    data = {"state": "up", "hostname": "h.lab.local", "os_name": "Linux"}

    for _ in range(4):  # same host seen 4x in one scan file
        svc._record_host_scan_history(host.id, scan.id, data)
    db_session.flush()  # a duplicate insert would raise IntegrityError here

    rows = db_session.query(models.HostScanHistory).filter(
        models.HostScanHistory.host_id == host.id,
        models.HostScanHistory.scan_id == scan.id,
    ).all()
    assert len(rows) == 1


def test_host_scan_history_distinct_row_per_scan(db_session, test_project):
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.9.2.2")
    scan2 = models.Scan(project_id=test_project.id, filename="m.xml", tool_name="Nmap", scan_type="nmap")
    db_session.add(scan2)
    db_session.flush()
    svc = HostDeduplicationService(db_session)
    data = {"state": "up", "hostname": "h.lab.local", "os_name": "Linux"}

    svc._record_host_scan_history(host.id, scan.id, data)
    svc._record_host_scan_history(host.id, scan2.id, data)  # re-scan
    db_session.flush()

    rows = db_session.query(models.HostScanHistory).filter(
        models.HostScanHistory.host_id == host.id,
    ).all()
    assert {r.scan_id for r in rows} == {scan.id, scan2.id}


def test_port_scan_history_deduped_within_one_scan(db_session, test_project):
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.9.2.3")
    port = models.Port(host_id=host.id, port_number=443, protocol="tcp", state="open")
    db_session.add(port)
    db_session.flush()
    svc = HostDeduplicationService(db_session)
    pdata = {"state": "open", "service_name": "https"}

    for _ in range(3):
        svc._record_port_scan_history(port.id, scan.id, pdata)
    db_session.flush()

    rows = db_session.query(models.PortScanHistory).filter(
        models.PortScanHistory.port_id == port.id,
        models.PortScanHistory.scan_id == scan.id,
    ).all()
    assert len(rows) == 1
