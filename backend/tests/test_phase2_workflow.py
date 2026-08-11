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

def test_tag_lifecycle_over_the_surviving_surface(client, db_session, test_project):
    """Tags: created by bulk-tagging, then renamed and deleted.

    v2.242.0 retired the singular routes (POST /tags, POST /{id}/tags,
    DELETE /{id}/tags/{tag_id}) — no UI ever called them; every tag action in
    the product goes through /bulk/tags. This covers the same ground over the
    surface that actually ships.
    """
    pid = test_project.id
    base = f"/api/v1/projects/{pid}/hosts"
    host = _mk_host(db_session, pid, "10.1.1.1")
    db_session.commit()

    # Tags come into existence by being applied — there is no create-an-empty-tag
    # path any more, which is why every tag has at least one host.
    r = client.post(
        f"{base}/bulk/tags",
        json={"host_ids": [host.id], "names": ["prod"], "action": "add"},
    )
    assert r.status_code == 200, r.text

    listing = client.get(f"{base}/tags").json()
    tag = next(t for t in listing if t["name"] == "prod")
    assert tag["host_count"] == 1
    tag_id = tag["id"]

    # tag filter returns the host, and its payload carries the tag
    listed = client.get(f"{base}/", params={"tags": str(tag_id)}).json()["items"]
    assert len(listed) == 1 and listed[0]["id"] == host.id
    assert any(t["id"] == tag_id for t in listed[0]["tags"])

    # rename — the gap the tag-management UI exists to close
    r = client.patch(f"{base}/tags/{tag_id}", json={"name": "production"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "production"
    assert r.json()["host_count"] == 1, "rename must not drop assignments"

    # remove from the host via bulk
    r = client.post(
        f"{base}/bulk/tags",
        json={"host_ids": [host.id], "tag_ids": [tag_id], "action": "remove"},
    )
    assert r.status_code == 200, r.text
    assert client.get(f"{base}/", params={"tags": str(tag_id)}).json()["items"] == []

    # delete the tag definition
    assert client.delete(f"{base}/tags/{tag_id}").status_code == 204
    assert all(t["id"] != tag_id for t in client.get(f"{base}/tags").json())


def test_renaming_a_tag_onto_an_existing_name_conflicts(client, db_session, test_project):
    pid = test_project.id
    base = f"/api/v1/projects/{pid}/hosts"
    host = _mk_host(db_session, pid, "10.1.1.2")
    db_session.commit()
    client.post(f"{base}/bulk/tags",
                json={"host_ids": [host.id], "names": ["alpha", "beta"], "action": "add"})
    tags = {t["name"]: t["id"] for t in client.get(f"{base}/tags").json()}
    r = client.patch(f"{base}/tags/{tags['alpha']}", json={"name": "beta"})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

def test_assign_host_to_member(client, db_session, test_project):
    """Assignment through the bulk route — the only one the UI calls.

    v2.242.0 retired POST/DELETE /hosts/{id}/assign. Everything asserted here
    (follow row, assigned_by, in_review status, notification, assigned_to
    filter) is behaviour the bulk path owns.
    """
    pid = test_project.id
    base = f"/api/v1/projects/{pid}/hosts"
    member = _mk_user(db_session, "alice", member_of=pid)
    host = _mk_host(db_session, pid, "10.2.2.2")
    db_session.commit()

    r = client.post(
        f"{base}/bulk/assign",
        json={"host_ids": [host.id], "assignee_user_id": member.id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["affected"] == 1

    follow = (
        db_session.query(HostFollow)
        .filter(HostFollow.host_id == host.id, HostFollow.user_id == member.id)
        .first()
    )
    assert follow is not None and follow.assigned_at is not None
    assert follow.assigned_by_id == 1  # the admin client user
    assert str(getattr(follow.status, "value", follow.status)) == "in_review"

    notif = (
        db_session.query(Notification)
        .filter(Notification.user_id == member.id, Notification.type == "assignment")
        .first()
    )
    assert notif is not None

    listed = client.get(f"{base}/", params={"assigned_to": str(member.id)}).json()["items"]
    assert [h["id"] for h in listed] == [host.id]
    assert any(a["user_id"] == member.id for a in listed[0]["assignees"])


def test_assigning_a_host_fires_the_host_assigned_webhook(
    client, db_session, test_project, monkeypatch,
):
    """The event was advertised and never delivered.

    `host_assigned` is offered in the integrations picker ("A host was
    assigned to someone"), but it was dispatched only from the singular
    POST /hosts/{id}/assign route, which no UI ever called — every assignment
    goes through /bulk/assign, which did not dispatch. So a user could
    subscribe and receive nothing, forever. This pins the fix.
    """
    sent = []
    import app.api.v1.endpoints.host_bulk as host_bulk
    monkeypatch.setattr(
        host_bulk, "safe_dispatch",
        lambda db, **kw: sent.append(kw),
    )

    pid = test_project.id
    member = _mk_user(db_session, "bob", member_of=pid)
    h1 = _mk_host(db_session, pid, "10.2.9.1")
    h2 = _mk_host(db_session, pid, "10.2.9.2")
    db_session.commit()

    client.post(
        f"/api/v1/projects/{pid}/hosts/bulk/assign",
        json={"host_ids": [h1.id, h2.id], "assignee_user_id": member.id},
    )

    assert len(sent) == 1, "one event per operation, not one per host"
    ev = sent[0]
    assert ev["event"] == "host_assigned"
    assert ev["context"]["assignee_user_id"] == member.id
    assert ev["context"]["host_count"] == 2
    assert set(ev["context"]["host_ids"]) == {h1.id, h2.id}
    # host_id is only meaningful for a single-host assign
    assert "host_id" not in ev["context"]


def test_single_host_assign_webhook_keeps_the_simple_shape(
    client, db_session, test_project, monkeypatch,
):
    sent = []
    import app.api.v1.endpoints.host_bulk as host_bulk
    monkeypatch.setattr(host_bulk, "safe_dispatch", lambda db, **kw: sent.append(kw))

    pid = test_project.id
    member = _mk_user(db_session, "carol", member_of=pid)
    host = _mk_host(db_session, pid, "10.2.9.3")
    db_session.commit()

    client.post(
        f"/api/v1/projects/{pid}/hosts/bulk/assign",
        json={"host_ids": [host.id], "assignee_user_id": member.id},
    )
    assert sent[0]["context"]["host_id"] == host.id


def test_assign_to_non_member_rejected(client, db_session, test_project):
    pid = test_project.id
    outsider = _mk_user(db_session, "outsider")  # no membership, not admin
    host = _mk_host(db_session, pid, "10.3.3.3")
    db_session.commit()
    r = client.post(
        f"/api/v1/projects/{pid}/hosts/bulk/assign",
        json={"host_ids": [host.id], "assignee_user_id": outsider.id},
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
