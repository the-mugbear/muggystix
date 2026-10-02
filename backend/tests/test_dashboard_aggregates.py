"""Regression tests for the v2.86.12 dashboard / coverage aggregate fixes.

Two endpoints changed shape:

* ``GET /dashboard/team-review`` — the project review roster.  The route
  went in v2.244.0 and the roster itself (the workbench's ``team_review``)
  in v2.451.1; what is left of it here is the one fact that still has a
  reader — a host two teammates have in review is one host — pinned on the
  Hosts list ``follow:in_review``.
* ``GET /projects/{pid}/coverage/`` — was loading each scope's
  subnets + a distinct-host-count query per scope (N+1 over the scope
  list).  Now loads subnets in one batched query and the distinct
  counts in one GROUP BY keyed by scope_id.

These tests pin the behaviour:

* The team's In Review hosts count each host once.
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


def test_team_in_review_hosts_count_a_host_once_whoever_reviews_it(
    client, db_session, test_project,
):
    """One host in review by two teammates is ONE host in "what the team is
    reviewing".

    v2.451.1 — this was pinned on the workbench's ``team_review`` roster
    (``total_hosts_in_review``), removed with it.  The question is now
    answered by the Hosts list ``follow:in_review`` (any teammate's; the
    agents' ``assist_list_hosts q=follow:in_review``), so the guard moved
    there: distinct hosts, not follow rows.  (The roster's row-cap test went
    with the roster — the cap was its own argument.)"""
    alice = _make_user(db_session, "alice-dedupe")
    bob = _make_user(db_session, "bob-dedupe")
    shared = _make_host(db_session, test_project.id, "10.0.5.1")
    alice_only = _make_host(db_session, test_project.id, "10.0.5.2")
    _make_host(db_session, test_project.id, "10.0.5.3")  # nobody's
    _make_in_review_follow(db_session, alice.id, shared.id)
    _make_in_review_follow(db_session, bob.id, shared.id)
    _make_in_review_follow(db_session, alice.id, alice_only.id)
    db_session.commit()

    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/",
        params={"q": "follow:in_review", "limit": 50},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(i["ip_address"] for i in body["items"]) == ["10.0.5.1", "10.0.5.2"]
    assert body["total"] == 2, body["total"]


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
