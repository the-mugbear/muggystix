"""Regression tests for the vulnerability dedup fix (v2.72.0).

Bug: VulnerabilityService._create_vulnerability_from_nessus inserted
without flushing, so under the session's autoflush=False a repeated
(plugin_id, port) within ONE scan was written twice (the existence
lookup couldn't see the pending row).  The test session also runs
autoflush=False, so these tests reproduce the original bug and prove
the flush fix.
"""
from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySource
from app.parsers.nessus_parser import NessusHost, NessusVulnerability
from app.services.vulnerability_service import VulnerabilityService


def _vuln(plugin_id: str, port: int, *, name: str = "Test Plugin", severity: int = 3) -> NessusVulnerability:
    return NessusVulnerability(
        plugin_id=plugin_id,
        plugin_name=name,
        severity=severity,
        risk_factor="High",
        cvss_base_score=7.5,
        cvss_vector=None,
        cvss3_base_score=None,
        cvss3_vector=None,
        cve_list=[],
        description="desc",
        solution="patch it",
        synopsis="syn",
        plugin_output=None,
        port=port,
        protocol="tcp",
        service_name=None,
        exploitable=False,
        patch_publication_date=None,
        vuln_publication_date=None,
    )


def _nessus_host(ip: str, vulns) -> NessusHost:
    return NessusHost(
        ip_address=ip,
        hostname=None,
        operating_system=None,
        mac_address=None,
        netbios_name=None,
        fqdn=None,
        vulnerabilities=list(vulns),
        host_properties={},
    )


def _mk_host_and_scan(db, project_id, ip):
    host = models.Host(ip_address=ip, state="up", project_id=project_id)
    db.add(host)
    scan = models.Scan(project_id=project_id, filename="n.nessus", tool_name="Nessus", scan_type="nessus")
    db.add(scan)
    db.flush()
    return host, scan


def test_repeated_plugin_same_port_dedups_within_scan(db_session, test_project):
    """Same (plugin_id, port) twice in one scan → one row, not two."""
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.9.0.1")
    nessus_host = _nessus_host("10.9.0.1", [_vuln("19506", 443), _vuln("19506", 443)])

    VulnerabilityService(db_session).process_nessus_vulnerabilities(host, nessus_host, scan)
    db_session.flush()

    rows = (
        db_session.query(Vulnerability)
        .filter(
            Vulnerability.host_id == host.id,
            Vulnerability.plugin_id == "19506",
            Vulnerability.source == VulnerabilitySource.NESSUS,
        )
        .all()
    )
    assert len(rows) == 1


def test_same_plugin_different_ports_kept_separate(db_session, test_project):
    """Same plugin on two different ports → two rows (legitimate)."""
    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.9.0.2")
    nessus_host = _nessus_host("10.9.0.2", [_vuln("57582", 443), _vuln("57582", 8443)])

    VulnerabilityService(db_session).process_nessus_vulnerabilities(host, nessus_host, scan)
    db_session.flush()

    rows = (
        db_session.query(Vulnerability)
        .filter(Vulnerability.host_id == host.id, Vulnerability.plugin_id == "57582")
        .all()
    )
    assert len(rows) == 2
    assert {r.port.port_number for r in rows} == {443, 8443}


def test_one_bad_finding_does_not_lose_the_host(
    db_session, test_project, monkeypatch,
):
    """v2.91.4 (third code review #2) — a single finding whose flush
    raises must NOT roll back the host or the other valid findings.

    Pre-fix the per-finding except returned None without rolling back
    the SQLAlchemy session, leaving it in ``pending_rollback``.  The
    next finding's flush then threw ``PendingRollbackError``, the
    host-level except in ``_process_nessus_host`` rolled back the
    entire host savepoint, and every "previously persisted" finding
    on this host disappeared.  v2.91.3's ``write_failures`` counter
    was therefore lying — it counted rows that the outer rollback
    discarded.

    Reproduces the failure mode with a monkeypatched ``self.db.flush``
    that raises on a specific plugin_id and asserts: (a) host
    persists, (b) the OK findings persist, (c) the bad finding does
    not persist, (d) write_failures == 1.
    """
    from app.services.vulnerability_service import VulnerabilityService
    from sqlalchemy.exc import IntegrityError

    host, scan = _mk_host_and_scan(db_session, test_project.id, "10.9.0.4")
    nessus_host = _nessus_host(
        "10.9.0.4",
        [
            _vuln("11111", 80),
            _vuln("22222", 443),   # this one will fail to flush
            _vuln("33333", 8080),
        ],
    )

    svc = VulnerabilityService(db_session)
    real_flush = svc.db.flush

    bad_plugin_seen = {"count": 0}

    def flaky_flush(*a, **kw):
        # Spot the pending Vulnerability with plugin_id == "22222" and raise
        # on its flush — EVERY time it's pending.  The service now processes
        # findings in a batch SAVEPOINT and, on failure, retries the batch
        # finding-by-finding; a row that fails persistently must be isolated
        # (write_failures=1), not silently recovered.  A one-shot failure
        # would just be retried away, which is correct behaviour but a
        # different scenario than this test pins.
        pending = [
            obj for obj in svc.db.new
            if obj.__class__.__name__ == "Vulnerability"
            and getattr(obj, "plugin_id", None) == "22222"
        ]
        if pending:
            bad_plugin_seen["count"] += 1
            raise IntegrityError("forced", params={}, orig=Exception("flush failure"))
        return real_flush(*a, **kw)

    monkeypatch.setattr(svc.db, "flush", flaky_flush)
    stats = svc.process_nessus_vulnerabilities(host, nessus_host, scan)
    monkeypatch.setattr(svc.db, "flush", real_flush)  # restore for assertion phase
    db_session.flush()

    assert stats["write_failures"] == 1
    assert stats["total"] == 2

    surviving = (
        db_session.query(Vulnerability)
        .filter(Vulnerability.host_id == host.id)
        .all()
    )
    plugin_ids = {v.plugin_id for v in surviving}
    assert plugin_ids == {"11111", "33333"}, (
        "the host and its OK findings must survive a single bad-finding flush"
    )

    # And the host row itself is still present.
    assert db_session.query(models.Host).filter_by(id=host.id).first() is not None


def test_repeated_plugin_across_scans_dedups(db_session, test_project):
    """The same finding in two separate scans collapses onto one row,
    with scan_id advanced to the latest scan."""
    host, scan_a = _mk_host_and_scan(db_session, test_project.id, "10.9.0.3")
    svc = VulnerabilityService(db_session)
    svc.process_nessus_vulnerabilities(host, _nessus_host("10.9.0.3", [_vuln("33850", 22)]), scan_a)
    db_session.flush()

    scan_b = models.Scan(project_id=test_project.id, filename="n2.nessus", tool_name="Nessus", scan_type="nessus")
    db_session.add(scan_b)
    db_session.flush()
    svc.process_nessus_vulnerabilities(host, _nessus_host("10.9.0.3", [_vuln("33850", 22)]), scan_b)
    db_session.flush()

    rows = (
        db_session.query(Vulnerability)
        .filter(Vulnerability.host_id == host.id, Vulnerability.plugin_id == "33850")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].scan_id == scan_b.id
