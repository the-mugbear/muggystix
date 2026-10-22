"""Regression tests for v2.86.8 scope-detail + out-of-scope pagination.

Two endpoints picked up new pagination + filter knobs:

  * ``GET /projects/{pid}/scopes/host-mappings`` — added
    ``subnet_id``, ``skip``, ``limit`` (le=2000); back-compat default
    returns up to 2000 mappings in scope.  (Keyed by the project alone since
    2.489.0: it was ``/scopes/{id}/host-mappings``.)
  * ``GET /projects/{pid}/scans/out-of-scope`` — added ``skip``,
    ``limit`` (le=2000), ``search``; pre-fix returned every row.

These tests confirm the limit caps reject oversize, the filters work
in isolation, and a moderate seed pages correctly.
"""
from __future__ import annotations

from app.db import models


def test_scope_host_mappings_rejects_oversize_limit(client, test_project):
    """Mappings endpoint caps at le=2000."""
    r = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/host-mappings",
        params={"limit": 999_999},
    )
    assert r.status_code == 422, r.text


def test_scope_host_mappings_are_the_projects_own_and_create_no_scope(client, db_session, test_project):
    """The route names no scope (2.489.0).  A project with no scope yet lists
    nothing and gets none from this read; another project's subnet lists
    nothing either, by its id or without it."""
    from app.db.models_project import Project

    url = f"/api/v1/projects/{test_project.id}/scopes/host-mappings"
    assert client.get(url).json()["total"] == 0
    assert db_session.query(models.Scope).filter(models.Scope.project_id == test_project.id).count() == 0

    other = Project(name="Other", slug="other-mappings")
    db_session.add(other)
    db_session.flush()
    theirs = models.Scope(project_id=other.id)
    db_session.add(theirs)
    db_session.flush()
    their_subnet = models.Subnet(scope_id=theirs.id, cidr="10.44.0.0/24")
    their_host = models.Host(project_id=other.id, ip_address="10.44.0.5", state="up")
    db_session.add_all([their_subnet, their_host])
    db_session.flush()
    db_session.add(models.HostSubnetMapping(host_id=their_host.id, subnet_id=their_subnet.id))
    db_session.flush()

    assert client.get(url).json()["total"] == 0
    assert client.get(url, params={"subnet_id": their_subnet.id}).json()["items"] == []
    # The old spelling is gone, not an alias.
    assert client.get(f"/api/v1/projects/{test_project.id}/scopes/{theirs.id}/host-mappings").status_code == 404


def test_scope_host_mappings_subnet_id_filter(client, db_session, test_project):
    """subnet_id query param restricts the result to one subnet's mappings."""
    scope = models.Scope(project_id=test_project.id, description="")
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
        f"/api/v1/projects/{test_project.id}/scopes/host-mappings",
    )
    assert r_all.status_code == 200, r_all.text
    body_all = r_all.json()
    assert len(body_all["items"]) == 2
    assert body_all["total"] == 2

    # Filtered to subnet A — only the one mapping.
    r_a = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/host-mappings",
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
    scope = models.Scope(project_id=test_project.id, description="")
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
        f"/api/v1/projects/{test_project.id}/scopes/host-mappings",
        params={"skip": 0, "limit": 2},
    ).json()
    page2 = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/host-mappings",
        params={"skip": 2, "limit": 2},
    ).json()
    page3 = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/host-mappings",
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
