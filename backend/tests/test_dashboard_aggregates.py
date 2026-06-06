"""Regression tests for the v2.86.12 dashboard / coverage aggregate fixes.

Two endpoints changed shape:

* ``GET /dashboard/team-review`` — was unbounded ``.all()`` followed by
  ``len(distinct_hosts)`` to compute the total.  Now caps the row
  fetch at ``limit`` (default 500, le=2000) and computes
  ``total_hosts_in_review`` via SQL so the figure stays correct even
  when the cap is hit.
* ``GET /projects/{pid}/coverage/`` — was loading each scope's
  subnets + a distinct-host-count query per scope (N+1 over the scope
  list).  Now loads subnets in one batched query and the distinct
  counts in one GROUP BY keyed by scope_id.

These tests pin the behaviour:

* Team-review row cap rejects oversize ``limit`` (422).
* Team-review ``total_hosts_in_review`` is correct when the row cap
  hides reviewers.
* Coverage emits per-scope counts that match the pre-fix per-scope
  query result.
"""
from __future__ import annotations

from app.db import models
from app.db.models import FollowStatus


# Module-level counter for explicit user ids so we don't collide with
# conftest's hardcoded id=1.  Postgres' sequence isn't bumped past
# explicit inserts, so the first auto-allocated id otherwise collides
# with conftest.  Same workaround pattern used by other tests in the
# suite that hit this conftest infra quirk.
_USER_ID_SEQ = [1000]


def _make_user(db_session, username):
    from app.db.models_auth import User, UserRole
    from datetime import datetime, timezone
    _USER_ID_SEQ[0] += 1
    u = User(
        id=_USER_ID_SEQ[0],
        username=username,
        email=f"{username}@example.com",
        full_name=username.capitalize(),
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(u)
    db_session.flush()
    return u


def _make_host(db_session, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db_session.add(h)
    db_session.flush()
    return h


def _make_in_review_follow(db_session, user_id, host_id):
    f = models.HostFollow(
        user_id=user_id, host_id=host_id,
        status=FollowStatus.IN_REVIEW,
    )
    db_session.add(f)
    db_session.flush()
    return f


def test_team_review_rejects_oversize_limit(client, test_project):
    r = client.get(
        f"/api/v1/projects/{test_project.id}/dashboard/team-review",
        params={"limit": 999_999},
    )
    assert r.status_code == 422, r.text


def test_team_review_total_includes_rows_beyond_cap(
    client, db_session, test_project,
):
    """When the response row cap clips reviewers, the distinct-host
    total still reflects the whole roster."""
    alice = _make_user(db_session, "alice-tr")
    for i in range(5):
        h = _make_host(db_session, test_project.id, f"10.0.0.{10 + i}")
        _make_in_review_follow(db_session, alice.id, h.id)

    r = client.get(
        f"/api/v1/projects/{test_project.id}/dashboard/team-review",
        params={"limit": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_hosts_in_review"] == 5, body
    assert len(body["reviewers"]) == 1
    assert body["reviewers"][0]["host_count"] == 2


def test_team_review_distinct_count_dedupes_two_reviewers_on_same_host(
    client, db_session, test_project,
):
    """One host watched by two reviewers should count as 1 in
    ``total_hosts_in_review`` (distinct host_id, not row count)."""
    alice = _make_user(db_session, "alice-dedupe")
    bob = _make_user(db_session, "bob-dedupe")
    shared = _make_host(db_session, test_project.id, "10.0.5.1")
    alice_only = _make_host(db_session, test_project.id, "10.0.5.2")
    _make_in_review_follow(db_session, alice.id, shared.id)
    _make_in_review_follow(db_session, bob.id, shared.id)
    _make_in_review_follow(db_session, alice.id, alice_only.id)

    r = client.get(
        f"/api/v1/projects/{test_project.id}/dashboard/team-review",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_hosts_in_review"] == 2, body


def test_coverage_per_scope_counts_match_batched_path(
    client, db_session, test_project,
):
    """Each scope's ``discovered_in_scope`` should equal the actual
    number of distinct mapped hosts.  The v2.86.12 batched GROUP BY
    must produce the same per-scope counts the old per-scope query did."""
    scope_a = models.Scope(project_id=test_project.id, name="A", description="")
    scope_b = models.Scope(project_id=test_project.id, name="B", description="")
    db_session.add_all([scope_a, scope_b])
    db_session.flush()
    subnet_a = models.Subnet(scope_id=scope_a.id, cidr="10.0.0.0/24", description="")
    subnet_b = models.Subnet(scope_id=scope_b.id, cidr="10.0.1.0/24", description="")
    db_session.add_all([subnet_a, subnet_b])
    db_session.flush()
    ha1 = _make_host(db_session, test_project.id, "10.0.0.5")
    ha2 = _make_host(db_session, test_project.id, "10.0.0.6")
    hb1 = _make_host(db_session, test_project.id, "10.0.1.5")
    db_session.add_all([
        models.HostSubnetMapping(host_id=ha1.id, subnet_id=subnet_a.id),
        models.HostSubnetMapping(host_id=ha2.id, subnet_id=subnet_a.id),
        models.HostSubnetMapping(host_id=hb1.id, subnet_id=subnet_b.id),
    ])
    db_session.flush()

    r = client.get(f"/api/v1/projects/{test_project.id}/coverage/")
    assert r.status_code == 200, r.text
    body = r.json()
    scopes_by_name = {s["scope_name"]: s for s in body["scopes"]}
    assert scopes_by_name["A"]["discovered_in_scope"] == 2
    assert scopes_by_name["A"]["subnet_count"] == 1
    assert scopes_by_name["B"]["discovered_in_scope"] == 1
    assert scopes_by_name["B"]["subnet_count"] == 1


def test_coverage_scope_with_no_subnets_returns_zero_counts(
    client, db_session, test_project,
):
    """A scope with zero subnets should appear with 0 counts (not be
    dropped from the response)."""
    empty = models.Scope(project_id=test_project.id, name="empty", description="")
    db_session.add(empty)
    db_session.flush()

    r = client.get(f"/api/v1/projects/{test_project.id}/coverage/")
    body = r.json()
    scopes_by_name = {s["scope_name"]: s for s in body["scopes"]}
    assert "empty" in scopes_by_name
    assert scopes_by_name["empty"]["subnet_count"] == 0
    assert scopes_by_name["empty"]["discovered_in_scope"] == 0
    assert scopes_by_name["empty"]["coverage_percent"] is None
