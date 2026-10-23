"""Deleting an account keeps its host review marks, attributed to no one
(owner, 2026-10-22).

``host_follows.user_id`` was ``ON DELETE CASCADE`` (and the ORM relationship
``User.host_follows`` deleted the rows in Python first): deleting a user who
had reviewed hosts turned those hosts back into unreviewed, "untouched" hosts,
and moved Posture's "Hosts reviewed".  Since revision ``e8c4a2f6d1b9`` the key
is SET NULL and the rows stay — status, ``reviewed_at`` and the note — with no
reviewer.  What ends with the person is the assignment the rows carried.

Pinned here:

* the rows are kept with ``user_id`` NULL, their review intact, the
  assignment cleared;
* Posture's reviewed count, the cross-project engagement figure, the DSL's
  ``follow:`` / ``has:untouched`` words and the Hosts row read the kept marks
  exactly as before the delete;
* ``follow:mine``, ``follow:revisit`` and ``assigned:`` of any living person
  never match them, and ``assigned:none`` lists the hosts again;
* a note on such a host notifies the living reviewers and nobody for the
  kept row;
* the Hosts row and the followers list name the reviewer as nobody (null),
  which the page reads as "a former member";
* Oversight's per-person rows skip the kept marks instead of failing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models import FollowStatus, HostFollow
from app.db.models_auth import User, UserRole
from app.db.models_project import Notification, ProjectMembership, ProjectRole
from app.main import app
from app.services import engagement_metrics_service as em
from app.services.posture_service import compute_posture


def _person(db, uid, username):
    user = User(
        id=uid, username=username, email=f"{username}@example.com", full_name=username.title(),
        hashed_password="x", role=UserRole.MEMBER, is_active=True,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def world(db_session, test_project, test_user):
    """Three hosts: ``reviewed`` (Reviewed by the leaving analyst, with a note,
    and In review by the one who stays), ``in_review`` (In review by the
    leaving analyst) and ``untouched``."""
    db = db_session
    leaving = _person(db, 2, "leaving")
    staying = _person(db, 3, "staying")
    for person in (leaving, staying, test_user):
        db.add(ProjectMembership(project_id=test_project.id, user_id=person.id, role=ProjectRole.ANALYST.value))
    hosts = {}
    for name, ip in (("reviewed", "10.81.0.1"), ("in_review", "10.81.0.2"), ("untouched", "10.81.0.3")):
        host = models.Host(project_id=test_project.id, ip_address=ip, state="up")
        db.add(host)
        db.flush()
        hosts[name] = host.id
    reviewed_at = datetime.now(timezone.utc) - timedelta(days=2)
    db.add_all([
        HostFollow(
            host_id=hosts["reviewed"], user_id=leaving.id, status=FollowStatus.REVIEWED,
            reviewed_at=reviewed_at, review_summary="SMB signing checked; fine.",
            assigned_at=reviewed_at, assigned_by_id=leaving.id,
        ),
        HostFollow(
            host_id=hosts["in_review"], user_id=leaving.id, status=FollowStatus.IN_REVIEW,
            assigned_at=reviewed_at, assigned_by_id=leaving.id,
        ),
        HostFollow(host_id=hosts["reviewed"], user_id=staying.id, status=FollowStatus.IN_REVIEW),
    ])
    db.commit()
    return {
        "base": f"/api/v1/projects/{test_project.id}", "hosts": hosts,
        "leaving": leaving.id, "staying": staying, "admin": test_user, "reviewed_at": reviewed_at,
    }


def _ips(client, base, q):
    resp = client.get(f"{base}/hosts/", params={"q": q})
    assert resp.status_code == 200, resp.text
    return sorted(h["ip_address"] for h in resp.json()["items"])


def _reviewed_counts(db, project_id):
    posture = compute_posture(db, project_id, use_cache=False)["headline"]["review_coverage"]["reviewed"]
    engagement = em.project_engagement(db, [project_id])[project_id]
    return posture, engagement.hosts_reviewed, engagement.hosts_in_review


def test_deleting_a_reviewer_keeps_their_marks_and_every_count(client, db_session, test_project, world):
    db, base = db_session, world["base"]
    before = _reviewed_counts(db, test_project.id)
    untouched_before = _ips(client, base, "has:untouched")
    reviewed_before = _ips(client, base, "follow:reviewed")
    in_review_before = _ips(client, base, "follow:in_review")
    assert before[0] == 1 and untouched_before == ["10.81.0.3"]

    gone = client.delete(f"/api/v1/users/{world['leaving']}")
    assert gone.status_code == 200, gone.text
    db.expire_all()

    kept = {
        f.host_id: f for f in db.query(HostFollow).filter(HostFollow.user_id.is_(None)).all()
    }
    assert set(kept) == {world["hosts"]["reviewed"], world["hosts"]["in_review"]}
    mark = kept[world["hosts"]["reviewed"]]
    assert mark.status == FollowStatus.REVIEWED
    assert mark.reviewed_at == world["reviewed_at"]
    assert mark.review_summary == "SMB signing checked; fine."
    # The assignment ends with the person; the review does not.
    assert {(f.assigned_at, f.assigned_by_id) for f in kept.values()} == {(None, None)}

    # Every project figure that reads any member's mark reads the same.
    assert _reviewed_counts(db, test_project.id) == before
    assert em.project_engagement(db, [test_project.id])[test_project.id].hosts_tested == 2
    assert _ips(client, base, "has:untouched") == untouched_before
    assert _ips(client, base, "follow:reviewed") == reviewed_before
    assert _ips(client, base, "follow:in_review") == in_review_before

    # Nobody living owns the kept marks.
    app.dependency_overrides[get_current_user] = lambda: world["staying"]
    try:
        assert _ips(client, base, "follow:mine") == ["10.81.0.1"]  # their own In review only
        assert _ips(client, base, "follow:revisit") == []
        assert _ips(client, base, "assigned:any") == []
        assert _ips(client, base, "assigned:none") == ["10.81.0.1", "10.81.0.2", "10.81.0.3"]
    finally:
        app.dependency_overrides[get_current_user] = lambda: world["admin"]
    assert _ips(client, base, "follow:mine") == []


def test_the_hosts_row_and_the_followers_list_name_nobody(client, db_session, world):
    base, hosts = world["base"], world["hosts"]
    assert client.delete(f"/api/v1/users/{world['leaving']}").status_code == 200

    rows = {h["id"]: h for h in client.get(f"{base}/hosts/").json()["items"]}
    reviewed = rows[hosts["reviewed"]]
    assert reviewed["team_review_status"] == "reviewed"
    assert reviewed["reviewed_by"] == [{"user_id": None, "name": None}]
    assert reviewed["other_reviewers"] == [{"user_id": world["staying"].id, "name": "Staying"}]
    assert rows[hosts["in_review"]]["other_reviewers"] == [{"user_id": None, "name": None}]
    assert rows[hosts["reviewed"]]["assignees"] == []

    followers = client.get(f"{base}/hosts/{hosts['reviewed']}/followers")
    assert followers.status_code == 200, followers.text
    listed = {(f["user_id"], f["username"], f["status"]) for f in followers.json()["followers"]}
    assert listed == {(world["staying"].id, "staying", "in_review"), (None, None, "reviewed")}


def test_a_note_on_the_host_tells_the_living_reviewers_and_nobody_else(client, db_session, world):
    db, base, host_id = db_session, world["base"], world["hosts"]["reviewed"]
    assert client.delete(f"/api/v1/users/{world['leaving']}").status_code == 200

    posted = client.post(f"{base}/hosts/{host_id}/notes", json={"body": "Re-checked the share list."})
    assert posted.status_code in (200, 201), posted.text
    assert posted.json().get("mention_warning") is None, posted.json()
    told = db.query(Notification.user_id).filter(Notification.type == "host_note").all()
    assert sorted(uid for (uid,) in told) == [world["staying"].id]


def test_oversight_people_rows_skip_a_mark_with_no_person(client, db_session, test_project, world):
    """``tester_rows`` is one row per PERSON; a kept mark has none.  It used to
    put ``None`` among the user ids and fail sorting them."""
    assert client.delete(f"/api/v1/users/{world['leaving']}").status_code == 200
    rows = em.tester_rows(db_session, [test_project.id], em.Window())
    assert [r.user_id for r in rows] == [world["staying"].id]
    dashboard = client.get("/api/v1/oversight/dashboard")
    assert dashboard.status_code == 200, dashboard.text


def test_the_user_delete_deletes_no_follow_row(client, db_session, world):
    """Nothing in the delete path (the ORM relationship included) deletes a
    review mark — a count before and after, across every row."""
    before = db_session.query(HostFollow).count()
    assert client.delete(f"/api/v1/users/{world['leaving']}").status_code == 200
    db_session.expire_all()
    assert db_session.query(HostFollow).count() == before == 3
