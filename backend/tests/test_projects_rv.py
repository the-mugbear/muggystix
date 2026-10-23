"""Tests for the project endpoint review fixes (RV-3, RV-11).

RV-3: create_project requires global admin; a project created archived is archived.
RV-11: project listing computes member counts in one grouped query.

The ``client`` fixture authenticates as a global admin.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership
from app.api.v1.endpoints.auth import require_role

_USER_ID_SEQ = [4000]


def _make_user(db_session, username, role=UserRole.MEMBER):
    _USER_ID_SEQ[0] += 1
    u = User(
        id=_USER_ID_SEQ[0],
        username=username,
        email=f"{username}@example.com",
        full_name=username.title(),
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=role,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(u)
    db_session.flush()
    return u


def test_create_project_requires_global_admin(db_session):
    """A non-admin hitting the create gate is rejected (RV-3)."""
    member = _make_user(db_session, "rv3-member")
    checker = require_role(UserRole.ADMIN)
    with pytest.raises(HTTPException) as exc:
        checker(current_user=member)
    assert exc.value.status_code == 403


def test_a_project_created_archived_is_archived(client, db_session):
    """status='archived' IS the project being archived — there is no second
    flag to keep in step (``projects.is_archived`` was dropped in 2.489.0), and
    no answer carries one."""
    r = client.post(
        "/api/v1/projects/",
        json={"name": "rv3-archived-proj", "status": "archived"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "archived"
    assert "is_archived" not in body
    # The project's own routes answer 410, as for any archived project.
    assert client.get(f"/api/v1/projects/{body['id']}/hosts/").status_code == 410

    # Default listing (no include_archived) must exclude it.
    listing = client.get("/api/v1/projects/").json()
    names = [p["name"] for p in listing]
    assert "rv3-archived-proj" not in names


def test_in_progress_is_no_longer_a_project_status(client, db_session):
    """v2.399.0 merged 'in_progress' into 'active' and kept the old name as an
    alias with no end date; ``ck_projects_status`` forbids it in the table, and
    the alias went with review 2026-10-23b (PLT-8): it is refused like any
    other unknown status."""
    assert client.post(
        "/api/v1/projects/", json={"name": "status-alias", "status": "in_progress"},
    ).status_code == 400
    r = client.post("/api/v1/projects/", json={"name": "status-alias", "status": "active"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.put(f"/api/v1/projects/{pid}", json={"status": "completed"})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert client.put(f"/api/v1/projects/{pid}", json={"status": "in_progress"}).status_code == 400
    assert client.put(f"/api/v1/projects/{pid}", json={"status": "paused"}).status_code == 400


def test_list_projects_member_counts_batched(client, db_session, test_project):
    """RV-11 — member_count is correct from the single grouped query."""
    u1 = _make_user(db_session, "rv11-a")
    u2 = _make_user(db_session, "rv11-b")
    db_session.add_all([
        ProjectMembership(project_id=test_project.id, user_id=u1.id, role="analyst"),
        ProjectMembership(project_id=test_project.id, user_id=u2.id, role="viewer"),
    ])
    db_session.flush()

    listing = client.get("/api/v1/projects/").json()
    card = next(p for p in listing if p["id"] == test_project.id)
    assert card["member_count"] == 2


def test_rename_onto_a_taken_slug_allocates_a_suffix(client, db_session):
    """v2.341.0 (review) — names and slugs are separately unique, but rename
    only checked the name: renaming "Other" to "foo-bar" passed the name check
    and died on the slug constraint held by "Foo Bar" (a 500).  Rename now
    goes through the same allocator creation uses."""
    r = client.post("/api/v1/projects/", json={"name": "Foo Bar"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["slug"] == "foo-bar"
    r = client.post("/api/v1/projects/", json={"name": "Other"})
    assert r.status_code in (200, 201), r.text
    other_id = r.json()["id"]

    r = client.put(f"/api/v1/projects/{other_id}", json={"name": "foo-bar"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "foo-bar"
    assert r.json()["slug"] == "foo-bar-1"

    # Renaming back to its own current name keeps its own slug (no suffix creep).
    r = client.put(f"/api/v1/projects/{other_id}", json={"name": "foo-bar"})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "foo-bar-1"


def test_create_response_carries_the_callers_role(client):
    """v2.404.0 — the UI makes a project it just created the active one from
    the create response, so the response says what the caller may do there
    (it carried no role, and Project settings then read the creator as a
    non-admin until a reload)."""
    r = client.post("/api/v1/projects/", json={"name": "Fresh engagement"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["my_role"] == "admin"
    assert body["member_count"] == 1
