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
