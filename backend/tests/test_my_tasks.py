"""Tests for the authoritative ``GET /dashboard/my-tasks`` (refactor P1).

The endpoint is the UNION of three buckets, each tagged with a reason:
  - assigned   — TestPlanEntry.assigned_to_id == caller
  - in_review  — entry on a host the caller marked In Review
  - triage     — unassigned critical/high entry

The ``client`` fixture authenticates as ``test_user`` (id=1, admin), so
"the caller" below is user id 1.  ``test_plan`` is an approved plan.
"""
from __future__ import annotations

from app.db import models
from app.db.models import FollowStatus
from app.db.models_agent import TestPlanEntry

# Avoid colliding with conftest's hardcoded user id=1 (see the same
# workaround in test_dashboard_aggregates.py).
_USER_ID_SEQ = [2000]


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


def _make_entry(db_session, plan_id, host_id, priority,
                assigned_to_id=None, status="proposed", phase="enumeration"):
    e = TestPlanEntry(
        test_plan_id=plan_id,
        host_id=host_id,
        priority=priority,
        test_phase=phase,
        proposed_tests=["technique-a"],
        rationale="because",
        status=status,
        assigned_to_id=assigned_to_id,
    )
    db_session.add(e)
    db_session.flush()
    return e


def _in_review(db_session, user_id, host_id):
    f = models.HostFollow(
        user_id=user_id, host_id=host_id, status=FollowStatus.IN_REVIEW,
    )
    db_session.add(f)
    db_session.flush()
    return f


def _url(pid):
    return f"/api/v1/projects/{pid}/dashboard/my-tasks"


def test_my_tasks_unions_three_buckets_with_reasons(
    client, db_session, test_project, test_plan, test_user,
):
    other = _make_user(db_session, "someone-else")
    h_assigned = _make_host(db_session, test_project.id, "10.1.0.1")
    h_review = _make_host(db_session, test_project.id, "10.1.0.2")
    h_triage = _make_host(db_session, test_project.id, "10.1.0.3")
    h_both = _make_host(db_session, test_project.id, "10.1.0.4")
    h_other = _make_host(db_session, test_project.id, "10.1.0.5")

    # assigned to caller, host NOT in review, low priority
    e_assigned = _make_entry(db_session, test_plan.id, h_assigned.id, "low",
                             assigned_to_id=test_user.id)
    # in-review host, unassigned, low priority (NOT triage — low)
    e_review = _make_entry(db_session, test_plan.id, h_review.id, "low")
    _in_review(db_session, test_user.id, h_review.id)
    # unassigned critical, host not in review → triage
    e_triage = _make_entry(db_session, test_plan.id, h_triage.id, "critical")
    # assigned to caller AND in-review host → both reasons
    e_both = _make_entry(db_session, test_plan.id, h_both.id, "high",
                         assigned_to_id=test_user.id)
    _in_review(db_session, test_user.id, h_both.id)
    # assigned to someone else, medium, not in review → excluded
    _make_entry(db_session, test_plan.id, h_other.id, "medium",
                assigned_to_id=other.id)

    r = client.get(_url(test_project.id))
    assert r.status_code == 200, r.text
    body = r.json()

    by_entry = {it["entry_id"]: it for it in body["items"]}
    # The 4 matching entries appear; the someone-else medium does not.
    assert set(by_entry) == {e_assigned.id, e_review.id, e_triage.id, e_both.id}
    assert body["total_open"] == 4

    assert by_entry[e_assigned.id]["reasons"] == ["assigned"]
    assert by_entry[e_review.id]["reasons"] == ["in_review"]
    assert by_entry[e_triage.id]["reasons"] == ["triage"]
    assert by_entry[e_both.id]["reasons"] == ["assigned", "in_review"]

    counts = body["reason_counts"]
    assert counts["assigned"] == 2   # e_assigned, e_both
    assert counts["in_review"] == 2  # e_review, e_both
    assert counts["triage"] == 1     # e_triage


def test_my_tasks_excludes_terminal_entries(
    client, db_session, test_project, test_plan, test_user,
):
    """A completed/rejected entry assigned to the caller must not appear."""
    # Separate hosts — (test_plan_id, host_id) is unique (uq_plan_host).
    h_done = _make_host(db_session, test_project.id, "10.2.0.1")
    h_rejected = _make_host(db_session, test_project.id, "10.2.0.2")
    _make_entry(db_session, test_plan.id, h_done.id, "critical",
                assigned_to_id=test_user.id, status="completed")
    _make_entry(db_session, test_plan.id, h_rejected.id, "high",
                assigned_to_id=test_user.id, status="rejected", phase="reconnaissance")

    r = client.get(_url(test_project.id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["total_open"] == 0


def test_my_tasks_orders_assigned_before_triage(
    client, db_session, test_project, test_plan, test_user,
):
    """Reason rank wins over priority: an assigned low-priority task
    outranks an unassigned critical triage task."""
    h_assigned = _make_host(db_session, test_project.id, "10.3.0.1")
    h_triage = _make_host(db_session, test_project.id, "10.3.0.2")
    e_assigned = _make_entry(db_session, test_plan.id, h_assigned.id, "low",
                             assigned_to_id=test_user.id)
    e_triage = _make_entry(db_session, test_plan.id, h_triage.id, "critical")

    r = client.get(_url(test_project.id))
    body = r.json()
    order = [it["entry_id"] for it in body["items"]]
    assert order.index(e_assigned.id) < order.index(e_triage.id), order


def test_my_tasks_assigned_survives_limit_against_many_triage(
    client, db_session, test_project, test_plan, test_user,
):
    """Regression (RV-2): the SQL LIMIT must keep the top-ranked rows.

    Pre-fix the query over-fetched limit*4 rows with NO order_by, then
    sorted in Python — so when more than limit*4 entries qualified, an
    assigned (rank-0) task could be excluded by the unordered LIMIT
    before it was ever ranked.  Here limit=2 (limit*4=8) but 10 entries
    qualify; the single assigned task must still appear.
    """
    # 9 unassigned-critical triage entries (each on its own host).
    for i in range(9):
        h = _make_host(db_session, test_project.id, f"10.8.0.{i + 1}")
        _make_entry(db_session, test_plan.id, h.id, "critical")
    # 1 assigned LOW-priority task — lowest priority but highest reason.
    h_assigned = _make_host(db_session, test_project.id, "10.8.9.9")
    e_assigned = _make_entry(db_session, test_plan.id, h_assigned.id, "low",
                             assigned_to_id=test_user.id)

    r = client.get(_url(test_project.id) + "?limit=2")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = [it["entry_id"] for it in items]
    # Assigned outranks triage regardless of priority → it's first and
    # always within the limit.
    assert ids[0] == e_assigned.id, ids


def test_my_tasks_empty_when_nothing_matches(client, test_project):
    r = client.get(_url(test_project.id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["total_open"] == 0
    assert body["reason_counts"] == {"assigned": 0, "in_review": 0, "triage": 0}
