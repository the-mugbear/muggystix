"""Contract tests for the testssl.sh JSON parser (Phase 5b)."""
from __future__ import annotations

import json

import pytest

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


@pytest.mark.parametrize("order", ["a_first", "b_first"])
def test_two_sni_names_on_one_ip_port_are_two_endpoints(db_session, test_project, tmp_path, order):
    """v2.416.0 — one IP:443 serving two names with different certificates and
    different weaknesses.  Keyed by the IP URL, the second name collided and
    its checks rolled back with it: which tenant survived depended on record
    order.  Both endpoints and both sets of observations survive now."""
    from app.db.models_vulnerability import Vulnerability

    a = [
        {"id": "TLS1", "ip": "a.example.com/10.7.0.9", "port": "443", "severity": "LOW", "finding": "offered (deprecated)"},
        {"id": "cert_notAfter", "ip": "a.example.com/10.7.0.9", "port": "443", "severity": "INFO", "finding": "2031-01-01 00:00"},
    ]
    b = [
        {"id": "TLS1_2", "ip": "b.example.com/10.7.0.9", "port": "443", "severity": "OK", "finding": "offered"},
        {"id": "heartbleed", "ip": "b.example.com/10.7.0.9", "port": "443", "severity": "HIGH", "finding": "VULNERABLE"},
    ]
    path = _fixture(tmp_path, a + b if order == "a_first" else b + a)
    parser = TestsslParser(db_session)
    scan = parser.parse_file(str(path), path.name, project_id=test_project.id)

    rows = {r.url: r for r in db_session.query(models.WebInterface).filter(models.WebInterface.scan_id == scan.id)}
    assert set(rows) == {"https://a.example.com:443", "https://b.example.com:443"}
    assert {r.ip_address for r in rows.values()} == {"10.7.0.9"}
    assert rows["https://a.example.com:443"].tls_weak_protocol is True
    assert rows["https://b.example.com:443"].tls_weak_protocol is False
    assert parser.last_parse_stats["skipped"] == 0

    # Each name's checks are on its own named endpoint.
    vulns = db_session.query(Vulnerability).filter(Vulnerability.scan_id == scan.id).all()
    by_name = {}
    for v in vulns:
        by_name.setdefault(rows_name(db_session, v.name_id), []).append(v.check_id or v.title)
    assert "tls_deprecated_protocol" in by_name["a.example.com"]
    assert any("heartbleed" in str(t).lower() for t in by_name["b.example.com"])


def test_one_name_on_two_ips_is_two_endpoints(db_session, test_project, tmp_path):
    records = [
        {"id": "TLS1", "ip": "a.example.com/10.7.0.11", "port": "443", "severity": "LOW", "finding": "offered"},
        {"id": "TLS1", "ip": "a.example.com/10.7.0.12", "port": "443", "severity": "LOW", "finding": "offered"},
    ]
    path = _fixture(tmp_path, records)
    parser = TestsslParser(db_session)
    scan = parser.parse_file(str(path), path.name, project_id=test_project.id)
    rows = db_session.query(models.WebInterface).filter(models.WebInterface.scan_id == scan.id).all()
    assert sorted(r.ip_address for r in rows) == ["10.7.0.11", "10.7.0.12"]
    assert {r.url for r in rows} == {"https://a.example.com:443"}
    assert parser.last_parse_stats["skipped"] == 0


def rows_name(db, name_id):
    if name_id is None:
        return None
    return db.get(models.DNSName, name_id).fqdn


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
