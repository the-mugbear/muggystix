"""Characterization tests pinning the CURRENT behavior of the netexec
confidence write path (``NetexecParser._track_field_confidence``) and the
vulnerability-service host-attribute upsert (``_create_host_attribute``),
captured BEFORE P1.3 adds UNIQUE constraints to ``host_confidence`` /
``port_confidence`` / ``host_attributes``.

These lock the exact dedup keys those constraints must match:
  * confidence is keyed on ``(host_id | port_id, field_name)`` — one winning
    row per subject+field, updated in place when a higher-confidence
    observation arrives (and a ConflictHistory row is logged on the change).
  * a host attribute is keyed on ``(host_id, attribute_type, value, source)``
    — the same value from the same source upserts; a different value is a
    genuinely distinct attribute.

If a later change alters these keys, these tests fail loudly so the
constraint and the write path can't silently disagree.
"""
from datetime import datetime, timezone

from app.db import models
from app.db.models_confidence import HostConfidence, ConflictHistory
from app.db.models_vulnerability import HostAttribute
from app.parsers.netexec_parser import NetexecParser
from app.services.confidence_service import ConfidenceScore, ScanType, DataSource
from app.services.vulnerability_service import VulnerabilityService


def _mk_host_and_scan(db, project_id, ip):
    host = models.Host(ip_address=ip, state="up", project_id=project_id)
    db.add(host)
    scan = models.Scan(project_id=project_id, filename="x", tool_name="NetExec", scan_type="netexec")
    db.add(scan)
    db.flush()
    return host, scan


def _score(score, method="netexec smb"):
    return ConfidenceScore(
        score=score, source=DataSource.SMB_ENUM, scan_type=ScanType.NETEXEC,
        method=method, timestamp=datetime.now(timezone.utc), additional_info={},
    )


def _host_conf(db, host_id, field="os_name"):
    return (
        db.query(HostConfidence)
        .filter(HostConfidence.host_id == host_id, HostConfidence.field_name == field)
        .all()
    )


# --- confidence write path -------------------------------------------------

def test_confidence_create_one_row(db_session, test_project):
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.8.0.1")
    NetexecParser(db_session)._track_field_confidence(
        "host", host.id, "os_name", "Windows", _score(85), scan.id
    )
    db_session.flush()
    rows = _host_conf(db_session, host.id)
    assert len(rows) == 1
    assert rows[0].confidence_score == 85


def test_confidence_higher_score_updates_in_place_and_logs_conflict(db_session, test_project):
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.8.0.2")
    p = NetexecParser(db_session)
    p._track_field_confidence("host", host.id, "os_name", "Windows", _score(85), scan.id)
    db_session.flush()
    p._track_field_confidence("host", host.id, "os_name", "Windows Server", _score(95), scan.id)
    db_session.flush()

    rows = _host_conf(db_session, host.id)
    assert len(rows) == 1                      # updated in place, not a second row
    assert rows[0].confidence_score == 95
    conflicts = (
        db_session.query(ConflictHistory)
        .filter(ConflictHistory.host_id == host.id, ConflictHistory.field_name == "os_name")
        .all()
    )
    assert len(conflicts) == 1
    assert conflicts[0].new_confidence == 95


def test_confidence_lower_or_equal_score_ignored(db_session, test_project):
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.8.0.3")
    p = NetexecParser(db_session)
    p._track_field_confidence("host", host.id, "os_name", "Windows", _score(95), scan.id)
    db_session.flush()
    p._track_field_confidence("host", host.id, "os_name", "Linux", _score(70), scan.id)
    db_session.flush()

    rows = _host_conf(db_session, host.id)
    assert len(rows) == 1
    assert rows[0].confidence_score == 95      # held the higher-confidence value
    # No conflict is logged when the lower-confidence report is discarded.
    assert (
        db_session.query(ConflictHistory).filter(ConflictHistory.host_id == host.id).count() == 0
    )


def test_confidence_keyed_per_field(db_session, test_project):
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.8.0.4")
    p = NetexecParser(db_session)
    p._track_field_confidence("host", host.id, "os_name", "Windows", _score(85), scan.id)
    p._track_field_confidence("host", host.id, "hostname", "DC01", _score(85), scan.id)
    db_session.flush()
    # Distinct fields → distinct rows; the key is (host_id, field_name).
    assert (
        db_session.query(HostConfidence).filter(HostConfidence.host_id == host.id).count() == 2
    )


def test_confidence_same_field_twice_in_scan_upserts(db_session, test_project):
    """Two observations of the same (host, field) with NO intervening flush must
    not create a duplicate — relies on the flush inside _track_field_confidence
    so the second call finds the first. Guards the UNIQUE(host_id, field_name)."""
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.8.0.7")
    p = NetexecParser(db_session)
    p._track_field_confidence("host", host.id, "os_name", "Windows", _score(85), scan.id)
    p._track_field_confidence("host", host.id, "os_name", "Windows Server", _score(95), scan.id)
    db_session.flush()
    rows = _host_conf(db_session, host.id)
    assert len(rows) == 1
    assert rows[0].confidence_score == 95


# --- host-attribute upsert -------------------------------------------------

def test_host_attribute_same_value_upserts(db_session, test_project):
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.8.0.5")
    svc = VulnerabilityService(db_session)
    svc._create_host_attribute(host, "os_name", "Windows", "nessus", scan)
    svc._create_host_attribute(host, "os_name", "Windows", "nessus", scan)  # same key → upsert
    db_session.flush()
    same = (
        db_session.query(HostAttribute)
        .filter(
            HostAttribute.host_id == host.id,
            HostAttribute.attribute_type == "os_name",
            HostAttribute.value == "Windows",
            HostAttribute.source == "nessus",
        )
        .all()
    )
    assert len(same) == 1


def test_host_attribute_distinct_value_is_separate(db_session, test_project):
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.8.0.6")
    svc = VulnerabilityService(db_session)
    svc._create_host_attribute(host, "os_name", "Windows", "nessus", scan)
    svc._create_host_attribute(host, "os_name", "Linux", "nessus", scan)  # different value
    db_session.flush()
    assert (
        db_session.query(HostAttribute)
        .filter(HostAttribute.host_id == host.id, HostAttribute.attribute_type == "os_name")
        .count()
        == 2
    )
