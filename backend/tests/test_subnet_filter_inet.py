"""Regression tests for the v2.86.7 small-CIDR filter collapse.

Pre-fix ``parse_subnets`` expanded networks ≤ 1000 addresses into
``ip_address == "10.0.0.1" OR ip_address == "10.0.0.2" OR ...``
predicates — up to 1000+ ORs per CIDR, with explicit network +
broadcast special-cases.  v2.86.7 collapses every CIDR to a single
``hosts_v2.ip_address::inet <<= :cidr::inet`` predicate.

These tests confirm:
  * a /24 filter returns exactly the inside-the-CIDR hosts (including
    network + broadcast addresses, which the old code had to
    special-case),
  * a /22 filter (>1000 addresses; previously fell through to the inet
    branch) still works,
  * multiple comma-separated CIDRs OR together (host inside any of them
    qualifies),
  * an invalid CIDR falls back to a prefix-LIKE so the user typing an
    IP fragment still gets results.
"""
from __future__ import annotations

from app.db import models


def _seed_hosts(db_session, project_id):
    """Five hosts across two /24s + one host far outside."""
    rows = [
        # inside 10.0.0.0/24
        models.Host(project_id=project_id, ip_address="10.0.0.0", state="up"),    # network addr
        models.Host(project_id=project_id, ip_address="10.0.0.5", state="up"),
        models.Host(project_id=project_id, ip_address="10.0.0.255", state="up"),  # broadcast
        # inside 10.0.1.0/24 (adjacent — only in /23 or wider)
        models.Host(project_id=project_id, ip_address="10.0.1.50", state="up"),
        # far outside
        models.Host(project_id=project_id, ip_address="192.168.99.99", state="up"),
    ]
    for r in rows:
        db_session.add(r)
    db_session.flush()


def test_subnet_filter_24_includes_network_and_broadcast(
    client, db_session, test_project,
):
    _seed_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/",
        params={"subnets": "10.0.0.0/24", "include_total": "true"},
    )
    assert r.status_code == 200, r.text
    ips = {h["ip_address"] for h in r.json()["items"]}
    assert ips == {"10.0.0.0", "10.0.0.5", "10.0.0.255"}, ips


def test_subnet_filter_23_spans_two_octets(client, db_session, test_project):
    _seed_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/",
        params={"subnets": "10.0.0.0/23"},
    )
    ips = {h["ip_address"] for h in r.json()["items"]}
    # /23 = 512 addresses → 10.0.0.0–10.0.1.255 → all four inside-the-block hosts.
    assert ips == {"10.0.0.0", "10.0.0.5", "10.0.0.255", "10.0.1.50"}, ips


def test_subnet_filter_22_is_a_large_network(client, db_session, test_project):
    """/22 = 1024 addresses — previously hit the inet branch already.
    Now exercised by the same single code path."""
    _seed_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/",
        params={"subnets": "10.0.0.0/22"},
    )
    ips = {h["ip_address"] for h in r.json()["items"]}
    assert ips == {"10.0.0.0", "10.0.0.5", "10.0.0.255", "10.0.1.50"}, ips


def test_subnet_filter_multiple_cidrs_or_together(client, db_session, test_project):
    _seed_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/",
        params={"subnets": "10.0.0.0/24,192.168.99.0/24"},
    )
    ips = {h["ip_address"] for h in r.json()["items"]}
    assert ips == {"10.0.0.0", "10.0.0.5", "10.0.0.255", "192.168.99.99"}, ips


def test_subnet_filter_excludes_hosts_outside_cidr(client, db_session, test_project):
    _seed_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/",
        params={"subnets": "10.0.0.0/24"},
    )
    ips = {h["ip_address"] for h in r.json()["items"]}
    assert "10.0.1.50" not in ips, "host outside the /24 leaked into result"
    assert "192.168.99.99" not in ips, "host outside the /24 leaked into result"


def test_subnet_filter_invalid_cidr_falls_back_to_prefix_match(
    client, db_session, test_project,
):
    """Non-CIDR input (a partial IP) gets a LIKE prefix match — same
    behaviour as before the refactor.  Used by typeahead callers that
    pass a partial address as they type."""
    _seed_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/",
        params={"subnets": "192.168."},  # invalid CIDR → prefix LIKE
    )
    ips = {h["ip_address"] for h in r.json()["items"]}
    assert ips == {"192.168.99.99"}, ips
