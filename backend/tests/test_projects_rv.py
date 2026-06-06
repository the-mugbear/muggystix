"""Tests for the project endpoint review fixes (RV-3, RV-11).

RV-3: create_project requires global admin + sets is_archived from status.
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
        is_verified=True,
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


def test_create_archived_project_sets_is_archived(client, db_session):
    """status='archived' must set is_archived so it doesn't linger active."""
    r = client.post(
        "/api/v1/projects/",
        json={"name": "rv3-archived-proj", "status": "archived"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_archived"] is True

    # Default listing (no include_archived) must exclude it.
    listing = client.get("/api/v1/projects/").json()
    names = [p["name"] for p in listing]
    assert "rv3-archived-proj" not in names


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
