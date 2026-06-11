"""Tests for the systemic-insights service.

Builds a tiny two-site estate and asserts the cross-sectional tiers:
  * an end-of-life-OS weakness that spans BOTH sites is promoted to an estate
    blind spot;
  * a weak-auth weakness confined to one site is a systemic *condition* but NOT
    a blind spot (spread, not count, is the discriminator);
  * the diagnostic profiles surface per-subnet conditions.
"""
from datetime import datetime, timezone

from app.db import models
from app.db.models import Scope, Subnet, Site, HostSubnetMapping
from app.db.models_confidence import NetexecResult
from app.services.systemic_insight_service import compute_systemic_insights


def _host(db, project_id, ip, os_name=None):
    h = models.Host(ip_address=ip, state="up", project_id=project_id, os_name=os_name)
    db.add(h)
    db.flush()
    return h


def _estate(db, project_id):
    """Two sites, one /24 subnet each; returns (scan, subnetA, subnetB)."""
    scope = Scope(project_id=project_id, name="scope")
    db.add(scope)
    site1 = Site(project_id=project_id, name="HQ", criticality_tier=1)
    site2 = Site(project_id=project_id, name="Branch", criticality_tier=3)
    db.add_all([site1, site2])
    db.flush()
    sn_a = Subnet(scope_id=scope.id, cidr="10.1.1.0/24", site="HQ", site_id=site1.id)
    sn_b = Subnet(scope_id=scope.id, cidr="10.2.2.0/24", site="Branch", site_id=site2.id)
    db.add_all([sn_a, sn_b])
    scan = models.Scan(project_id=project_id, filename="s", tool_name="t", scan_type="nmap")
    db.add(scan)
    db.flush()
    return scan, sn_a, sn_b


def _map(db, host, subnet):
    db.add(HostSubnetMapping(host_id=host.id, subnet_id=subnet.id))


def test_eol_spanning_sites_is_blind_spot_weak_auth_is_not(db_session, test_project):
    pid = test_project.id
    scan, sn_a, sn_b = _estate(db_session, pid)

    # Subnet A / site HQ
    a1 = _host(db_session, pid, "10.1.1.1", "Windows XP Professional")  # EOL
    a2 = _host(db_session, pid, "10.1.1.2", "Windows 10 Pro")          # EOL
    a3 = _host(db_session, pid, "10.1.1.3", "Ubuntu")                   # fine
    # Subnet B / site Branch
    b1 = _host(db_session, pid, "10.2.2.1", "Windows 7")               # EOL
    b2 = _host(db_session, pid, "10.2.2.2", "Ubuntu")                   # fine
    b3 = _host(db_session, pid, "10.2.2.3", "Linux 5.15")              # fine
    for h in (a1, a2, a3):
        _map(db_session, h, sn_a)
    for h in (b1, b2, b3):
        _map(db_session, h, sn_b)

    # Weak auth confined to subnet A only (guest session on a3).
    db_session.add(NetexecResult(
        scan_id=scan.id, host_id=a3.id, protocol="smb", port=445,
        auth_success=True, username="guest",
        discovered_at=datetime.now(timezone.utc),
    ))
    db_session.flush()

    out = compute_systemic_insights(db_session, pid)

    assert out["adopted"] is True
    assert out["estate"]["hosts_in_scope"] == 6
    assert out["estate"]["sites"] == 2

    blind_keys = {b["key"] for b in out["blind_spots"]}
    assert "eol_os" in blind_keys                      # EOL spans both sites → blind spot

    by_key = {c["key"]: c for c in out["conditions"]}
    assert by_key["eol_os"]["site_spread"] == 2
    assert by_key["eol_os"]["affected_hosts"] == 3
    # Weak auth touches one site only → a condition, but not an estate blind spot.
    assert "weak_auth" in by_key
    assert by_key["weak_auth"]["site_spread"] == 1
    assert by_key["weak_auth"]["is_blind_spot"] is False
    assert "weak_auth" not in blind_keys

    # Diagnostic profiles surface per-subnet conditions.
    profiles = {p["subnet_id"]: p for p in out["diagnostic_profiles"]}
    assert sn_a.id in profiles
    assert "eol_os" in profiles[sn_a.id]["conditions"]


def test_no_subnets_not_adopted(db_session, test_project):
    out = compute_systemic_insights(db_session, test_project.id)
    assert out == {"adopted": False}
