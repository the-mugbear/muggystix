"""Phase 2 (workflow) feature tests: host tagging, assignment, bulk ops.

Exercises the new /hosts surfaces end-to-end through the HTTP client:
  - tag CRUD + per-host assign/remove + the `tags` list filter
  - host assignment (follow row + notification + `assigned_to` filter)
  - bulk tag / assign / follow + the /hosts/ids select-all helper
"""
from datetime import datetime, timezone

from sqlalchemy import func

from app.db import models
from app.db.models import HostFollow
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole, Notification


def _mk_host(db, project_id, ip):
    host = models.Host(ip_address=ip, state="up", project_id=project_id)
    db.add(host)
    db.flush()
    return host


def _mk_user(db, username, *, member_of=None, role=UserRole.MEMBER):
    # Explicit id past the current max — the conftest's test_user is
    # inserted with an explicit id=1, which doesn't advance Postgres'
    # SERIAL sequence, so an auto-id insert would collide on id=1.
    next_id = (db.query(func.max(User.id)).scalar() or 0) + 1
    user = User(
        id=next_id,
        username=username,
        email=f"{username}@example.com",
        full_name=username.title(),
        hashed_password="x",
        role=role,
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    if member_of is not None:
        db.add(ProjectMembership(project_id=member_of, user_id=user.id, role=ProjectRole.ANALYST.value))
        db.flush()
    return user


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------

def test_tag_crud_and_host_assignment(client, db_session, test_project):
    pid = test_project.id
    base = f"/api/v1/projects/{pid}/hosts"

    r = client.post(f"{base}/tags", json={"name": "prod", "color": "red"})
    assert r.status_code == 201, r.text
    tag_id = r.json()["id"]

    # duplicate name → 409
    assert client.post(f"{base}/tags", json={"name": "prod"}).status_code == 409

    listing = client.get(f"{base}/tags").json()
    assert any(t["id"] == tag_id and t["host_count"] == 0 for t in listing)

    host = _mk_host(db_session, pid, "10.1.1.1")
    db_session.commit()

    # assign existing tag + create-by-name in one call
    r = client.post(f"{base}/{host.id}/tags", json={"tag_ids": [tag_id], "names": ["dmz"]})
    assert r.status_code == 200, r.text
    assert {t["name"] for t in r.json()} == {"prod", "dmz"}

    # tag filter returns the host, and its payload carries the tag
    listed = client.get(f"{base}/", params={"tags": str(tag_id)}).json()["items"]
    assert len(listed) == 1 and listed[0]["id"] == host.id
    assert any(t["id"] == tag_id for t in listed[0]["tags"])

    # remove the tag from the host
    assert client.delete(f"{base}/{host.id}/tags/{tag_id}").status_code == 204
    assert client.get(f"{base}/", params={"tags": str(tag_id)}).json()["items"] == []

    # delete the tag definition
    assert client.delete(f"{base}/tags/{tag_id}").status_code == 204
    assert all(t["id"] != tag_id for t in client.get(f"{base}/tags").json())


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

def test_assign_host_to_member(client, db_session, test_project):
    pid = test_project.id
    base = f"/api/v1/projects/{pid}/hosts"
    member = _mk_user(db_session, "alice", member_of=pid)
    host = _mk_host(db_session, pid, "10.2.2.2")
    db_session.commit()

    r = client.post(f"{base}/{host.id}/assign", json={"assignee_user_id": member.id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == member.id
    assert body["status"] == "in_review"
    assert body["assigned_at"] is not None

    follow = (
        db_session.query(HostFollow)
        .filter(HostFollow.host_id == host.id, HostFollow.user_id == member.id)
        .first()
    )
    assert follow is not None and follow.assigned_at is not None
    assert follow.assigned_by_id == 1  # the admin client user

    # assignee got a notification
    notif = (
        db_session.query(Notification)
        .filter(Notification.user_id == member.id, Notification.type == "assignment")
        .first()
    )
    assert notif is not None and notif.source_type == "host"

    # assigned_to filter + assignees in the list payload
    listed = client.get(f"{base}/", params={"assigned_to": str(member.id)}).json()["items"]
    assert [h["id"] for h in listed] == [host.id]
    assert any(a["user_id"] == member.id for a in listed[0]["assignees"])

    # unassign clears it (keeps the follow row)
    assert client.delete(f"{base}/{host.id}/assign", params={"user_id": member.id}).status_code == 204
    assert client.get(f"{base}/", params={"assigned_to": str(member.id)}).json()["items"] == []
    assert (
        db_session.query(HostFollow)
        .filter(HostFollow.host_id == host.id, HostFollow.user_id == member.id)
        .first()
        is not None
    )


def test_assign_to_non_member_rejected(client, db_session, test_project):
    pid = test_project.id
    outsider = _mk_user(db_session, "outsider")  # no membership, not admin
    host = _mk_host(db_session, pid, "10.3.3.3")
    db_session.commit()
    r = client.post(
        f"/api/v1/projects/{pid}/hosts/{host.id}/assign",
        json={"assignee_user_id": outsider.id},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------

def test_bulk_tags_follow_and_ids(client, db_session, test_project):
    pid = test_project.id
    base = f"/api/v1/projects/{pid}/hosts"
    h1 = _mk_host(db_session, pid, "10.4.0.1")
    h2 = _mk_host(db_session, pid, "10.4.0.2")
    db_session.commit()
    ids = [h1.id, h2.id]

    # bulk tag (create-by-name, applied to both)
    r = client.post(f"{base}/bulk/tags", json={"host_ids": ids, "names": ["sweep"], "action": "add"})
    assert r.status_code == 200 and r.json()["affected"] == 2

    fd = client.get(f"{base}/filters/data").json()
    sweep = next(t for t in fd["tags"] if t["name"] == "sweep")
    assert sweep["host_count"] == 2

    # /ids select-all for the tag filter returns exactly the two hosts
    rids = client.get(f"{base}/ids", params={"tags": str(sweep["id"])}).json()
    assert set(rids["ids"]) == set(ids) and rids["total"] == 2 and rids["capped"] is False

    # bulk follow for the caller
    rf = client.post(f"{base}/bulk/follow", json={"host_ids": ids, "status": "in_review"})
    assert rf.status_code == 200 and rf.json()["affected"] == 2
    assert (
        db_session.query(HostFollow)
        .filter(HostFollow.host_id.in_(ids), HostFollow.user_id == 1)
        .count()
        == 2
    )

    # bulk tag remove
    rr = client.post(f"{base}/bulk/tags", json={"host_ids": ids, "tag_ids": [sweep["id"]], "action": "remove"})
    assert rr.status_code == 200 and rr.json()["affected"] == 2
    assert client.get(f"{base}/ids", params={"tags": str(sweep["id"])}).json()["total"] == 0


def test_bulk_assign(client, db_session, test_project):
    pid = test_project.id
    base = f"/api/v1/projects/{pid}/hosts"
    member = _mk_user(db_session, "bob", member_of=pid)
    h1 = _mk_host(db_session, pid, "10.5.0.1")
    h2 = _mk_host(db_session, pid, "10.5.0.2")
    db_session.commit()
    ids = [h1.id, h2.id]

    r = client.post(f"{base}/bulk/assign", json={"host_ids": ids, "assignee_user_id": member.id})
    assert r.status_code == 200 and r.json()["affected"] == 2

    assigned = (
        db_session.query(HostFollow)
        .filter(HostFollow.user_id == member.id, HostFollow.assigned_at.isnot(None))
        .count()
    )
    assert assigned == 2

    # one summary notification for the batch, not two
    notifs = (
        db_session.query(Notification)
        .filter(Notification.user_id == member.id, Notification.type == "assignment")
        .count()
    )
    assert notifs == 1
