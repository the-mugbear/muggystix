"""Regression tests for v2.86.8 out-of-scope pagination.

``GET /projects/{pid}/scans/out-of-scope`` takes ``skip``, ``limit``
(le=2000) and ``search``; before v2.86.8 it returned every row.  These tests
confirm the limit cap, the search and that the list agrees with the Hosts
page's ``out_of_scope_only`` export.  (Until 2.523.0 this file was
``test_scope_host_mappings_pagination.py`` and also covered
``GET /scopes/host-mappings``, removed then.)
"""
from __future__ import annotations

from app.db import models


def test_the_host_mappings_route_is_gone(client, test_project):
    """``GET /scopes/host-mappings`` was removed in 2.523.0 (owner, 2026-10-22:
    no caller; ``/hosts?subnets=<cidr>`` lists a subnet's hosts).  Its four
    tests (the limit cap, project isolation, the subnet filter, paging) went
    with it; ``Paginated``'s ``has_more`` is pinned by test_paging_one_envelope."""
    r = client.get(f"/api/v1/projects/{test_project.id}/scopes/host-mappings")
    assert r.status_code == 404, r.text


def test_out_of_scope_rejects_oversize_limit(client, test_project):
    r = client.get(
        f"/api/v1/projects/{test_project.id}/scans/out-of-scope",
        params={"limit": 999_999},
    )
    assert r.status_code == 422, r.text


def test_out_of_scope_search_filters_by_ip_and_hostname(
    client, db_session, test_project,
):
    # v2.239.0 — real hosts with no subnet mapping.  ``reason`` is no longer
    # searchable because it's now a constant derived explanation rather than
    # per-row free text, so searching it would match everything or nothing.
    db_session.add_all([
        models.Host(
            project_id=test_project.id, ip_address="10.10.0.1",
            hostname="thing.example", state="up",
        ),
        models.Host(
            project_id=test_project.id, ip_address="172.16.0.1",
            hostname="other.example", state="up",
        ),
    ])
    db_session.flush()

    # v2.86.13 — envelope shape.  ``.items`` holds the matching rows,
    # ``.total`` reflects the filtered query count.
    r_ip = client.get(
        f"/api/v1/projects/{test_project.id}/scans/out-of-scope",
        params={"search": "10.10"},
    )
    body_ip = r_ip.json()
    ips = {h["ip_address"] for h in body_ip["items"]}
    assert ips == {"10.10.0.1"}, ips
    assert body_ip["total"] == 1

    # Match by hostname.
    r_name = client.get(
        f"/api/v1/projects/{test_project.id}/scans/out-of-scope",
        params={"search": "other.example"},
    )
    body_name = r_name.json()
    ips = {h["ip_address"] for h in body_name["items"]}
    assert ips == {"172.16.0.1"}, ips
    assert body_name["total"] == 1


def test_out_of_scope_is_derived_and_agrees_with_the_export(
    client, db_session, test_project,
):
    """v2.239.0 regression — the JSON listing must derive out-of-scope hosts.

    It previously read ``out_of_scope_hosts``, a table host deduplication
    stopped writing, so it answered "nothing is out of scope" for every
    project while ``/export/out-of-scope`` — computing the same property from
    ``hosts_v2`` — returned the real list.  Two endpoints, same question,
    opposite answers, and only the wrong one is in the JSON API that agents
    consume.

    Asserting the two agree pins the invariant rather than the implementation:
    whichever way the derivation moves, it moves for both.  Since 2.521.0
    the file is the Hosts page's (`out_of_scope_only=true` + tool-ready), the
    door the Scope page's "View all out-of-scope hosts" opens.
    """
    scope = models.Scope(project_id=test_project.id)
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr="10.0.0.0/24")
    db_session.add(subnet)
    db_session.flush()

    inside = models.Host(project_id=test_project.id, ip_address="10.0.0.5", state="up")
    outside = models.Host(project_id=test_project.id, ip_address="203.0.113.9", state="up")
    db_session.add_all([inside, outside])
    db_session.flush()
    # Only the in-scope host gets a mapping — this is what correlation does.
    db_session.add(models.HostSubnetMapping(host_id=inside.id, subnet_id=subnet.id))
    db_session.flush()

    listed = client.get(
        f"/api/v1/projects/{test_project.id}/scans/out-of-scope"
    ).json()
    listed_ips = {h["ip_address"] for h in listed["items"]}

    exported = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/tool-ready/ip-list?out_of_scope_only=true"
    )
    assert exported.status_code == 200, exported.text
    exported_ips = {ln for ln in exported.text.splitlines() if ln.strip()}

    assert listed_ips == {"203.0.113.9"}, listed_ips
    assert listed_ips == exported_ips, (listed_ips, exported_ips)
    assert listed["total"] == 1
    # host_id must pivot to the host detail endpoint.
    assert listed["items"][0]["host_id"] == outside.id
