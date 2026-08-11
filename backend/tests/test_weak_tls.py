"""Weak-TLS protocol detection (Phase 5): derivation, the condition set, and
its systemic-condition membership in the encryption-&-trust family."""
import pytest

from app.db import models
from app.services.cert_fields import derive_weak_protocol
from app.services.host_condition_sets import weak_tls_host_ids


@pytest.mark.parametrize("blob,expected", [
    ({"tls_version": "TLS 1.0"}, True),
    ({"version": "tls10"}, True),
    ({"tls_version": "SSLv3"}, True),
    ({"tls_version": "TLSv1.2"}, False),
    ({"version": "tls13"}, False),
    ({"versions": ["TLSv1.2", "TLSv1.0"]}, True),   # any weak → weak
    ({"versions": ["TLSv1.2", "TLSv1.3"]}, False),
    ({"cipher": "ECDHE"}, None),                     # no version info → unknown
    ({}, None),
    (None, None),
])
def test_derive_weak_protocol(blob, expected):
    assert derive_weak_protocol(blob) is expected


def _web(db, host, project_id, *, url, weak, scan):
    db.add(models.WebInterface(
        host_id=host.id, project_id=project_id, scan_id=scan.id, url=url,
        protocol="https", tls_weak_protocol=weak,
    ))
    db.flush()


def test_weak_tls_host_ids_latest_wins(db_session, test_project):
    pid = test_project.id
    scan = models.Scan(project_id=pid, filename="s", tool_name="httpx", scan_type="web_fingerprint")
    db_session.add(scan)
    h1 = models.Host(project_id=pid, ip_address="10.0.0.1", state="up")
    h2 = models.Host(project_id=pid, ip_address="10.0.0.2", state="up")
    db_session.add_all([h1, h2])
    db_session.flush()
    _web(db_session, h1, pid, url="https://a", weak=True, scan=scan)
    _web(db_session, h2, pid, url="https://b", weak=False, scan=scan)
    db_session.commit()

    ids = weak_tls_host_ids(db_session, pid)
    assert h1.id in ids and h2.id not in ids


def test_weak_tls_is_encryption_trust_condition(db_session, test_project):
    """A weak-TLS host surfaces as a systemic condition rolled up to the
    encryption-&-trust family."""
    from app.db.models import Scope, Subnet, HostSubnetMapping
    from app.services.systemic_insight_service import compute_systemic_insights

    pid = test_project.id
    scope = Scope(project_id=pid, name="s")
    db_session.add(scope)
    db_session.flush()
    sn = Subnet(scope_id=scope.id, cidr="10.5.0.0/24", site=None, site_id=None)
    db_session.add(sn)
    scan = models.Scan(project_id=pid, filename="s", tool_name="httpx", scan_type="web_fingerprint")
    db_session.add(scan)
    db_session.flush()
    h = models.Host(project_id=pid, ip_address="10.5.0.1", state="up")
    db_session.add(h)
    db_session.flush()
    db_session.add(HostSubnetMapping(host_id=h.id, subnet_id=sn.id))
    _web(db_session, h, pid, url="https://x", weak=True, scan=scan)
    db_session.commit()

    out = compute_systemic_insights(db_session, pid)
    by_key = {c["key"]: c for c in out["conditions"]}
    assert "weak_tls" in by_key
    assert by_key["weak_tls"]["family"] == "encryption_trust"
    assert by_key["weak_tls"]["affected_hosts"] == 1
