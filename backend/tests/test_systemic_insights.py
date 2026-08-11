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
from app.services.systemic_insight_service import (
    compute_systemic_insights,
    _OUTLIER_ABS_DENSITY,
)


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

    # SMB signing disabled on one host in each site → spans both sites.
    a1.smb_signing = "disabled"
    b1.smb_signing = "disabled"

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
    # Phase 1: every condition carries its pattern family + spread classification,
    # and classification is the single source of is_blind_spot.
    assert by_key["eol_os"]["family"] == "lifecycle_patching"
    assert by_key["eol_os"]["classification"] == "estate_wide"
    assert by_key["weak_auth"]["family"] == "identity_auth"
    assert by_key["smb_signing"]["family"] == "lateral_movement"
    for c in out["conditions"]:
        assert (c["classification"] == "estate_wide") == c["is_blind_spot"]
        assert c["classification"] in ("isolated", "recurring", "estate_wide")
    # Weak auth touches one site only → a condition, but not an estate blind spot.
    assert "weak_auth" in by_key
    assert by_key["weak_auth"]["site_spread"] == 1
    assert by_key["weak_auth"]["is_blind_spot"] is False
    assert "weak_auth" not in blind_keys

    # SMB signing disabled spans both sites → a condition and an estate blind spot.
    assert by_key["smb_signing"]["site_spread"] == 2
    assert by_key["smb_signing"]["affected_hosts"] == 2
    assert "smb_signing" in blind_keys

    # Diagnostic profiles surface per-subnet conditions.
    profiles = {p["subnet_id"]: p for p in out["diagnostic_profiles"]}
    assert sn_a.id in profiles
    assert "eol_os" in profiles[sn_a.id]["conditions"]

    # family_summary rolls conditions up to their pattern family, carrying the
    # program-level control + root-cause hypothesis + worst classification.
    fam = {f["family"]: f for f in out["family_summary"]}
    assert "lifecycle_patching" in fam
    assert fam["lifecycle_patching"]["conditions"] == ["eol_os"]
    assert fam["lifecycle_patching"]["affected_hosts"] == 3
    assert fam["lifecycle_patching"]["classification"] == "estate_wide"
    assert fam["lifecycle_patching"]["recommended_control"]
    assert fam["lifecycle_patching"]["root_cause_hypothesis"]
    # Worst-first: estate-wide families sort ahead of isolated ones.
    ranks = [f["classification"] for f in out["family_summary"]]
    assert ranks == sorted(ranks, key=lambda c: {"estate_wide": 0, "recurring": 1, "isolated": 2}[c])


def test_no_subnets_not_adopted(db_session, test_project):
    out = compute_systemic_insights(db_session, test_project.id)
    assert out == {"adopted": False}


def test_technology_monoculture_blind_spot(db_session, test_project):
    """A versioned technology concentrated estate-wide surfaces as a
    technology-monoculture blind spot; a version-less library does not."""
    pid = test_project.id
    scope = Scope(project_id=pid, name="scope")
    db_session.add(scope)
    db_session.flush()
    sn = Subnet(scope_id=scope.id, cidr="10.8.8.0/24", site=None, site_id=None)
    db_session.add(sn)
    scan = models.Scan(project_id=pid, filename="s", tool_name="httpx", scan_type="web_fingerprint")
    db_session.add(scan)
    db_session.flush()
    # 5 hosts all running the same versioned tech + a version-less lib.
    for i in range(1, 6):
        h = _host(db_session, pid, f"10.8.8.{i}")
        _map(db_session, h, sn)
        db_session.add(models.WebInterface(
            host_id=h.id, project_id=pid, scan_id=scan.id, url=f"https://10.8.8.{i}",
            protocol="https", technologies=["nginx 1.14.0", "Bootstrap"],
        ))
    db_session.commit()

    out = compute_systemic_insights(db_session, pid)
    keys = {b["key"] for b in out["blind_spots"]}
    assert "tech:nginx 1.14.0" in keys
    assert "tech:Bootstrap" not in keys           # version-less → filtered as noise
    tech = next(b for b in out["blind_spots"] if b["key"] == "tech:nginx 1.14.0")
    assert tech["family"] == "technology_monoculture"
    assert tech["affected_hosts"] == 5


def test_tiny_estate_never_yields_blind_spot(db_session, test_project):
    """A one-host estate with a weakness is a condition, never an "estate-wide
    blind spot" — a single host can't evidence an estate-level pattern."""
    pid = test_project.id
    scope = Scope(project_id=pid, name="scope")
    db_session.add(scope)
    db_session.flush()
    sn = Subnet(scope_id=scope.id, cidr="10.9.9.0/24", site=None, site_id=None)
    db_session.add(sn)
    db_session.flush()
    h = _host(db_session, pid, "10.9.9.1", "Windows XP")   # EOL
    _map(db_session, h, sn)
    db_session.commit()

    out = compute_systemic_insights(db_session, pid)
    assert out["estate"]["hosts_in_scope"] == 1
    by_key = {c["key"]: c for c in out["conditions"]}
    # The condition is still surfaced...
    assert "eol_os" in by_key
    # ...but never promoted to a blind spot on a sub-threshold estate.
    assert by_key["eol_os"]["is_blind_spot"] is False
    assert out["blind_spots"] == []


def test_outlier_flagged_when_estate_median_is_zero(db_session, test_project):
    """A mostly-clean estate with one concentrated-issue subnet must still flag
    that subnet — the old rule required median_density>0 and suppressed it."""
    pid = test_project.id
    scope = Scope(project_id=pid, name="scope")
    db_session.add(scope)
    db_session.flush()
    bad = Subnet(scope_id=scope.id, cidr="10.5.5.0/24", site=None, site_id=None)
    clean1 = Subnet(scope_id=scope.id, cidr="10.6.6.0/24", site=None, site_id=None)
    clean2 = Subnet(scope_id=scope.id, cidr="10.7.7.0/24", site=None, site_id=None)
    db_session.add_all([bad, clean1, clean2])
    db_session.flush()

    # Bad subnet: 3 hosts, every one EOL → density 1.0 incidence/host.
    for i in range(1, 4):
        h = _host(db_session, pid, f"10.5.5.{i}", "Windows XP")
        _map(db_session, h, bad)
    # Two clean subnets, 3 healthy hosts each → density 0 → estate median 0.
    for sn, base in ((clean1, "10.6.6."), (clean2, "10.7.7.")):
        for i in range(1, 4):
            h = _host(db_session, pid, f"{base}{i}", "Ubuntu")
            _map(db_session, h, sn)
    db_session.commit()

    out = compute_systemic_insights(db_session, pid)
    outlier_subnets = {o["subnet_id"] for o in out["segment_outliers"]}
    assert bad.id in outlier_subnets
    bad_row = next(o for o in out["segment_outliers"] if o["subnet_id"] == bad.id)
    # No non-zero baseline → no "×median"; density carries the signal instead.
    assert bad_row["times_median"] is None
    assert bad_row["issue_density"] >= _OUTLIER_ABS_DENSITY


def test_host_inherits_site_from_labelled_parent_subnet(db_session, test_project):
    """A host whose most-specific subnet is UNLABELLED but sits inside a
    labelled parent inherits the parent's site — its subnet stays most-specific.
    (Unifies the resolver with the attention model; review B1.)"""
    from app.services.subnet_insight_service import resolve_host_locations

    scope = Scope(project_id=test_project.id, name="scope")
    db_session.add(scope)
    site = Site(project_id=test_project.id, name="DC-East", criticality_tier=1)
    db_session.add(site)
    db_session.flush()
    parent = Subnet(scope_id=scope.id, cidr="10.1.0.0/16", site="DC-East", site_id=site.id)
    child = Subnet(scope_id=scope.id, cidr="10.1.2.0/24", site=None, site_id=None)
    db_session.add_all([parent, child])
    host = models.Host(project_id=test_project.id, ip_address="10.1.2.5", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(HostSubnetMapping(host_id=host.id, subnet_id=parent.id))
    db_session.add(HostSubnetMapping(host_id=host.id, subnet_id=child.id))
    db_session.commit()

    loc = resolve_host_locations(db_session, test_project.id)[host.id]
    assert loc["subnet_id"] == child.id        # most-specific subnet
    assert loc["cidr"] == "10.1.2.0/24"
    assert loc["site"] == "DC-East"            # inherited from the labelled /16
    assert loc["site_id"] == site.id
