"""Contract tests for the testssl.sh JSON parser (Phase 5b)."""
from __future__ import annotations

import json

from app.db import models
from app.parsers.testssl_parser import TestsslParser, looks_like_testssl
from app.services.host_condition_sets import weak_tls_host_ids


class TestLooksLikeTestssl:
    def test_filename_match(self):
        assert looks_like_testssl(b"", "testssl-run.json")

    def test_content_match(self):
        sample = json.dumps([
            {"id": "SSLv3", "ip": "web/1.2.3.4", "port": "443", "severity": "OK", "finding": "not offered"},
        ]).encode()
        assert looks_like_testssl(sample, "scan.json")

    def test_rejects_httpx(self):
        assert not looks_like_testssl(b'{"url":"https://x/","tech":["Nginx"],"status_code":200}', "x.json")

    def test_rejects_plain(self):
        assert not looks_like_testssl(b'{"foo":"bar"}', "x.json")


def _fixture(tmp_path, records):
    p = tmp_path / "testssl.json"
    p.write_text(json.dumps(records))
    return p


def test_parse_weak_protocol_and_cert(db_session, test_project, tmp_path):
    records = [
        {"id": "SSLv2", "ip": "web.example.com/10.7.0.1", "port": "443", "severity": "OK", "finding": "not offered"},
        {"id": "SSLv3", "ip": "web.example.com/10.7.0.1", "port": "443", "severity": "HIGH", "finding": "offered (NOT ok)"},
        {"id": "TLS1", "ip": "web.example.com/10.7.0.1", "port": "443", "severity": "LOW", "finding": "offered (deprecated)"},
        {"id": "TLS1_2", "ip": "web.example.com/10.7.0.1", "port": "443", "severity": "OK", "finding": "offered"},
        {"id": "cert_notAfter", "ip": "web.example.com/10.7.0.1", "port": "443", "severity": "INFO", "finding": "2025-01-01 00:00"},
        {"id": "cert_chain_of_trust", "ip": "web.example.com/10.7.0.1", "port": "443", "severity": "HIGH", "finding": "self signed"},
    ]
    path = _fixture(tmp_path, records)
    parser = TestsslParser(db_session)
    scan = parser.parse_file(str(path), path.name, project_id=test_project.id)

    assert scan.tool_name == "testssl"
    row = (
        db_session.query(models.WebInterface)
        .filter(models.WebInterface.scan_id == scan.id)
        .one()
    )
    assert row.source == "testssl"
    assert row.ip_address == "10.7.0.1"
    assert row.tls_weak_protocol is True          # SSLv3 / TLS1.0 offered
    assert row.cert_self_signed is True
    assert row.cert_not_after is not None

    # The host is now picked up by the weak_tls condition set.
    assert row.host_id in weak_tls_host_ids(db_session, test_project.id)


def test_colliding_target_does_not_abort_upload(db_session, test_project, tmp_path):
    """A second target that collides on (scan_id, url, source) must be isolated
    by its savepoint and dropped — not poison the session and abort the whole
    upload (regression: the caught flush error used to leave pending_rollback)."""
    # Same IP:port reported under two hostnames → two targets, one URL.
    records = [
        {"id": "TLS1", "ip": "a.example.com/10.7.0.9", "port": "443", "severity": "LOW", "finding": "offered"},
        {"id": "TLS1", "ip": "b.example.com/10.7.0.9", "port": "443", "severity": "LOW", "finding": "offered"},
    ]
    path = _fixture(tmp_path, records)
    parser = TestsslParser(db_session)
    scan = parser.parse_file(str(path), path.name, project_id=test_project.id)  # must not raise

    rows = db_session.query(models.WebInterface).filter(models.WebInterface.scan_id == scan.id).all()
    assert len(rows) == 1                    # collision dropped, first survives
    assert rows[0].url == "https://10.7.0.9:443"
    # The session is healthy afterwards — a follow-up query does not raise.
    assert db_session.query(models.WebInterface).count() >= 1

    # v2.332.2 — the dropped target is reported as skipped AND leaves no trace
    # on the scan history: its note() used to land before the rollback, so the
    # snapshot carried b.example.com from a target the job said it skipped.
    assert parser.last_parse_stats["skipped"] == 1
    assert parser.last_parse_stats["partial"] is True
    hist = (
        db_session.query(models.HostScanHistory)
        .join(models.Host, models.HostScanHistory.host_id == models.Host.id)
        .filter(models.HostScanHistory.scan_id == scan.id, models.Host.ip_address == "10.7.0.9")
        .one()
    )
    assert hist.hostname_at_scan == "a.example.com", "a rolled-back target must not reach the history"
    assert hist.state_at_scan == "up"
    assert hist.host_created is True


def test_rolled_back_host_creation_does_not_poison_later_targets(
    db_session, test_project, tmp_path, monkeypatch
):
    """A Host created inside a target's savepoint is gone once that savepoint
    rolls back, but it stayed in the per-file cache — so the next target for
    the same address reused a transient object and failed too (v2.332.2)."""
    from app.parsers import testssl_parser as mod

    records = [
        {"id": "TLS1", "ip": "x.example.com/10.7.0.10", "port": "443", "severity": "LOW", "finding": "offered"},
        {"id": "TLS1", "ip": "x.example.com/10.7.0.10", "port": "8443", "severity": "LOW", "finding": "offered"},
    ]
    path = _fixture(tmp_path, records)

    # Fail the FIRST target after its host was created, then let the rest run.
    real_bind = mod.bind_hostname
    calls = {"n": 0}

    def flaky_bind(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected after host creation")
        return real_bind(*args, **kwargs)

    monkeypatch.setattr(mod, "bind_hostname", flaky_bind)

    parser = TestsslParser(db_session)
    scan = parser.parse_file(str(path), path.name, project_id=test_project.id)

    rows = db_session.query(models.WebInterface).filter(models.WebInterface.scan_id == scan.id).all()
    assert [r.port for r in rows] == [8443], "the second target must import cleanly"
    assert parser.last_parse_stats["skipped"] == 1
    host = db_session.query(models.Host).filter(
        models.Host.project_id == test_project.id, models.Host.ip_address == "10.7.0.10"
    ).one()
    assert rows[0].host_id == host.id
    hist = db_session.query(models.HostScanHistory).filter(
        models.HostScanHistory.scan_id == scan.id, models.HostScanHistory.host_id == host.id
    ).one()
    assert hist.host_created is True, "the surviving target is the one that created the host"


def test_parse_strong_only_is_not_weak(db_session, test_project, tmp_path):
    records = [
        {"id": "SSLv3", "ip": "10.7.0.2", "port": "443", "severity": "OK", "finding": "not offered"},
        {"id": "TLS1", "ip": "10.7.0.2", "port": "443", "severity": "OK", "finding": "not offered"},
        {"id": "TLS1_2", "ip": "10.7.0.2", "port": "443", "severity": "OK", "finding": "offered"},
        {"id": "TLS1_3", "ip": "10.7.0.2", "port": "443", "severity": "OK", "finding": "offered"},
    ]
    path = _fixture(tmp_path, records)
    parser = TestsslParser(db_session)
    scan = parser.parse_file(str(path), path.name, project_id=test_project.id)
    row = db_session.query(models.WebInterface).filter(models.WebInterface.scan_id == scan.id).one()
    assert row.tls_weak_protocol is False
    assert row.host_id not in weak_tls_host_ids(db_session, test_project.id)
