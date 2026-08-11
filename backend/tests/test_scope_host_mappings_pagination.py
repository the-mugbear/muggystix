"""Regression tests for v2.86.8 scope-detail + out-of-scope pagination.

Two endpoints picked up new pagination + filter knobs:

  * ``GET /projects/{pid}/scopes/{id}/host-mappings`` — added
    ``subnet_id``, ``skip``, ``limit`` (le=2000); back-compat default
    returns up to 2000 mappings in scope.
  * ``GET /projects/{pid}/scans/out-of-scope`` — added ``skip``,
    ``limit`` (le=2000), ``search``; pre-fix returned every row.

These tests confirm the limit caps reject oversize, the filters work
in isolation, and a moderate seed pages correctly.
"""
from __future__ import annotations

from app.db import models


def test_scope_host_mappings_rejects_oversize_limit(client, test_project):
    """Mappings endpoint caps at le=2000."""
    # v2.244.0 — POST /scopes/ was removed (scopes stopped being user-managed
    # in v2.9.4). Address the project's implicit scope instead.
    r_make = client.get(f"/api/v1/projects/{test_project.id}/scopes/default")
    assert r_make.status_code == 200, r_make.text
    scope_id = r_make.json()["id"]
    r = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/{scope_id}/host-mappings",
        params={"limit": 999_999},
    )
    assert r.status_code == 422, r.text


def test_scope_host_mappings_subnet_id_filter(client, db_session, test_project):
    """subnet_id query param restricts the result to one subnet's mappings."""
    scope = models.Scope(project_id=test_project.id, name="s", description="")
    db_session.add(scope)
    db_session.flush()
    sa = models.Subnet(scope_id=scope.id, cidr="10.0.0.0/24", description="")
    sb = models.Subnet(scope_id=scope.id, cidr="10.0.1.0/24", description="")
    db_session.add_all([sa, sb])
    db_session.flush()
    ha = models.Host(project_id=test_project.id, ip_address="10.0.0.5", state="up")
    hb = models.Host(project_id=test_project.id, ip_address="10.0.1.5", state="up")
    db_session.add_all([ha, hb])
    db_session.flush()
    db_session.add_all([
        models.HostSubnetMapping(host_id=ha.id, subnet_id=sa.id),
        models.HostSubnetMapping(host_id=hb.id, subnet_id=sb.id),
    ])
    db_session.flush()

    # Unfiltered — both mappings come back.  v2.86.13 — endpoint
    # returns Paginated[T] envelope.
    r_all = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/host-mappings",
    )
    assert r_all.status_code == 200, r_all.text
    body_all = r_all.json()
    assert len(body_all["items"]) == 2
    assert body_all["total"] == 2

    # Filtered to subnet A — only the one mapping.
    r_a = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/host-mappings",
        params={"subnet_id": sa.id},
    )
    assert r_a.status_code == 200, r_a.text
    body_a = r_a.json()
    rows = body_a["items"]
    assert len(rows) == 1, rows
    assert rows[0]["subnet_id"] == sa.id
    assert body_a["total"] == 1, "total should reflect the filter, not the unfiltered count"


def test_scope_host_mappings_skip_limit_paginate(client, db_session, test_project):
    """skip + limit page through the result set in order."""
    scope = models.Scope(project_id=test_project.id, name="s", description="")
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr="10.0.0.0/24", description="")
    db_session.add(subnet)
    db_session.flush()
    # Five hosts mapped to the same subnet.
    for i in range(5):
        h = models.Host(project_id=test_project.id, ip_address=f"10.0.0.{i+1}", state="up")
        db_session.add(h)
        db_session.flush()
        db_session.add(models.HostSubnetMapping(host_id=h.id, subnet_id=subnet.id))
    db_session.flush()

    # v2.86.13 — envelope shape: .json()["items"] holds the row list,
    # .json()["total"] holds the matching-count regardless of slice.
    page1 = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/host-mappings",
        params={"skip": 0, "limit": 2},
    ).json()
    page2 = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/host-mappings",
        params={"skip": 2, "limit": 2},
    ).json()
    page3 = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/{scope.id}/host-mappings",
        params={"skip": 4, "limit": 2},
    ).json()
    assert len(page1["items"]) == 2
    assert page1["total"] == 5
    assert page1["has_more"] is True
    assert len(page2["items"]) == 2
    assert page2["has_more"] is True
    assert len(page3["items"]) == 1, "tail page should partial-fill"
    assert page3["has_more"] is False
    # No overlap across pages.
    ids_total = [r["id"] for r in page1["items"] + page2["items"] + page3["items"]]
    assert len(ids_total) == len(set(ids_total)), "pagination produced duplicate rows"


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
    whichever way the derivation moves, it moves for both.
    """
    scope = models.Scope(project_id=test_project.id, name="corp")
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
        f"/api/v1/projects/{test_project.id}/export/out-of-scope?format_type=txt"
    )
    exported_ips = {ln for ln in exported.text.splitlines() if ln.strip()}

    assert listed_ips == {"203.0.113.9"}, listed_ips
    assert listed_ips == exported_ips, (listed_ips, exported_ips)
    assert listed["total"] == 1
    # host_id must pivot to the host detail endpoint.
    assert listed["items"][0]["host_id"] == outside.id
