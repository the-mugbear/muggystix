"""Review 2026-10-01 R26 — project and membership changes are audited.

``endpoints/projects.py`` wrote no audit row at all: a project could be
created, archived or deleted, and a person added to it, promoted or removed,
without a record — while ``/.well-known`` advertises a persistent audit trail
and every account change in ``users.py`` is audited.

Each row is staged in the same transaction as the change (``commit=False``),
so these tests also pin that a REFUSED change writes nothing.

The ``client`` fixture authenticates as a global admin.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.models_auth import AuditLog, User, UserRole
from app.db.models_project import Project, ProjectMembership

_USER_ID_SEQ = [4700]


def _make_user(db_session, username):
    _USER_ID_SEQ[0] += 1
    user = User(
        id=_USER_ID_SEQ[0],
        username=username,
        email=f"{username}@example.com",
        full_name=username.title(),
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()
    return user


def _rows(db_session, action, project_id=None):
    query = db_session.query(AuditLog).filter(AuditLog.action == action)
    if project_id is not None:
        query = query.filter(AuditLog.resource_id == str(project_id))
    return query.order_by(AuditLog.id).all()


def _project_actions(db_session, project_id):
    return [
        row.action
        for row in db_session.query(AuditLog)
        .filter(AuditLog.resource_id == str(project_id), AuditLog.action.like("project_%"))
        .order_by(AuditLog.id)
        .all()
    ]


def _create(client, name, **extra):
    response = client.post("/api/v1/projects/", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_creating_a_project_is_audited(client, db_session, test_user):
    project = _create(client, "r26-created")

    rows = _rows(db_session, "project_created", project["id"])
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == test_user.id
    assert row.resource_type == "project"
    assert row.success is True
    assert row.details["name"] == "r26-created"
    assert row.details["slug"] == project["slug"]
    assert row.details["creator_role"] == "admin"


def test_a_refused_create_writes_no_audit_row(client, db_session):
    _create(client, "r26-duplicate")
    before = db_session.query(AuditLog).filter(AuditLog.action == "project_created").count()

    response = client.post("/api/v1/projects/", json={"name": "r26-duplicate"})

    assert response.status_code == 400
    assert db_session.query(AuditLog).filter(AuditLog.action == "project_created").count() == before


def test_updating_a_project_records_old_and_new_values(client, db_session):
    project = _create(client, "r26-update", description="first")

    response = client.put(
        f"/api/v1/projects/{project['id']}",
        json={"name": "r26-update-renamed", "description": "second"},
    )
    assert response.status_code == 200, response.text

    rows = _rows(db_session, "project_updated", project["id"])
    assert len(rows) == 1
    changes = rows[0].details["changes"]
    assert changes["name"] == {"old": "r26-update", "new": "r26-update-renamed"}
    assert changes["description"] == {"old": "first", "new": "second"}
    # Only what changed is recorded.
    assert set(changes) == {"name", "description"}


def test_an_update_that_changes_nothing_writes_no_audit_row(client, db_session):
    project = _create(client, "r26-noop", description="same")

    response = client.put(f"/api/v1/projects/{project['id']}", json={"description": "same"})

    assert response.status_code == 200, response.text
    assert _project_actions(db_session, project["id"]) == ["project_created"]


def test_archiving_and_unarchiving_have_their_own_actions(client, db_session):
    _create(client, "r26-stays-active")  # archiving the only active project is refused
    project = _create(client, "r26-archive")

    archived = client.put(f"/api/v1/projects/{project['id']}", json={"status": "archived"})
    assert archived.status_code == 200, archived.text
    restored = client.put(f"/api/v1/projects/{project['id']}", json={"status": "active"})
    assert restored.status_code == 200, restored.text

    assert _project_actions(db_session, project["id"]) == [
        "project_created", "project_archived", "project_unarchived",
    ]
    row = _rows(db_session, "project_archived", project["id"])[0]
    assert row.details["changes"]["status"] == {"old": "active", "new": "archived"}
    assert row.details["changes"]["is_archived"] == {"old": False, "new": True}


def test_deleting_a_project_keeps_who_its_members_were(client, db_session, test_user):
    _create(client, "r26-survivor")  # the last project cannot be deleted
    project = _create(client, "r26-delete")
    analyst = _make_user(db_session, "r26-delete-analyst")
    added = client.post(
        f"/api/v1/projects/{project['id']}/members",
        json={"user_id": analyst.id, "role": "analyst"},
    )
    assert added.status_code == 201, added.text

    response = client.delete(f"/api/v1/projects/{project['id']}")
    assert response.status_code == 200, response.text
    assert db_session.query(Project).filter(Project.id == project["id"]).first() is None

    rows = _rows(db_session, "project_deleted", project["id"])
    assert len(rows) == 1
    details = rows[0].details
    assert rows[0].user_id == test_user.id
    assert details["name"] == "r26-delete"
    assert details["member_count"] == 2
    assert {(m["username"], m["role"]) for m in details["members"]} == {
        (test_user.username, "admin"), ("r26-delete-analyst", "analyst"),
    }


def test_a_refused_delete_writes_no_audit_row(client, db_session):
    for project in db_session.query(Project).all():
        db_session.delete(project)
    db_session.flush()
    only = _create(client, "r26-only-project")

    response = client.delete(f"/api/v1/projects/{only['id']}")

    assert response.status_code == 400
    assert _rows(db_session, "project_deleted") == []


def test_membership_changes_record_the_role_before_and_after(client, db_session, test_user):
    project = _create(client, "r26-members")
    member = _make_user(db_session, "r26-member")
    base = f"/api/v1/projects/{project['id']}/members"

    added = client.post(base, json={"user_id": member.id, "role": "viewer"})
    assert added.status_code == 201, added.text
    changed = client.put(f"{base}/{member.id}", json={"role": "analyst"})
    assert changed.status_code == 200, changed.text
    removed = client.delete(f"{base}/{member.id}")
    assert removed.status_code == 200, removed.text
    assert db_session.query(ProjectMembership).filter(
        ProjectMembership.project_id == project["id"],
        ProjectMembership.user_id == member.id,
    ).first() is None

    assert _project_actions(db_session, project["id"]) == [
        "project_created",
        "project_member_added",
        "project_member_role_changed",
        "project_member_removed",
    ]
    roles = {
        action: (row.details["old_role"], row.details["new_role"])
        for action in (
            "project_member_added", "project_member_role_changed", "project_member_removed",
        )
        for row in _rows(db_session, action, project["id"])
    }
    assert roles == {
        "project_member_added": (None, "viewer"),
        "project_member_role_changed": ("viewer", "analyst"),
        "project_member_removed": ("analyst", None),
    }
    for action in roles:
        row = _rows(db_session, action, project["id"])[0]
        assert row.user_id == test_user.id
        assert row.resource_type == "project_membership"
        assert row.details["user_id"] == member.id
        assert row.details["username"] == "r26-member"
        assert row.details["project_name"] == "r26-members"


def test_setting_the_same_role_again_writes_no_audit_row(client, db_session):
    project = _create(client, "r26-same-role")
    member = _make_user(db_session, "r26-same-role-member")
    base = f"/api/v1/projects/{project['id']}/members"
    assert client.post(base, json={"user_id": member.id, "role": "analyst"}).status_code == 201

    response = client.put(f"{base}/{member.id}", json={"role": "analyst"})

    assert response.status_code == 200, response.text
    assert _rows(db_session, "project_member_role_changed", project["id"]) == []


def test_refused_membership_changes_write_no_audit_row(client, db_session, test_user):
    project = _create(client, "r26-refused")
    base = f"/api/v1/projects/{project['id']}/members"

    # Already a member (the creator), an unknown role, and the last admin.
    assert client.post(base, json={"user_id": test_user.id, "role": "viewer"}).status_code == 400
    assert client.put(f"{base}/{test_user.id}", json={"role": "owner"}).status_code == 400
    assert client.delete(f"{base}/{test_user.id}").status_code == 400

    assert _project_actions(db_session, project["id"]) == ["project_created"]
