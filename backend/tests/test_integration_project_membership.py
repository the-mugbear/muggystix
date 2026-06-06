"""Regression test for v2.90.4 code-review #4 — integration
project_id requires project membership.

Pre-fix: ``POST /integrations/`` accepted any ``project_id`` and
silently bound the credential to it.  Secrets stayed owned by
current_user (so this wasn't a leak), but a user could orphan
credentials under projects they had no membership in — weakening
the project boundary.

Post-fix: ``_assert_project_member_or_admin`` rejects non-members
(global admins still bypass).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership


def _make_user(db_session, username: str, role=UserRole.MEMBER) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        full_name=username.title(),
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=role,
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_assert_project_member_passes_for_member(db_session, test_project):
    from app.api.v1.endpoints.integrations import _assert_project_member_or_admin

    user = _make_user(db_session, "imember")
    db_session.add(
        ProjectMembership(
            project_id=test_project.id, user_id=user.id, role="analyst",
        )
    )
    db_session.flush()
    # No exception raised.
    _assert_project_member_or_admin(db_session, user, test_project.id)


def test_assert_project_member_rejects_non_member(db_session, test_project):
    from app.api.v1.endpoints.integrations import _assert_project_member_or_admin

    user = _make_user(db_session, "ioutsider")  # no membership in test_project
    with pytest.raises(HTTPException) as exc:
        _assert_project_member_or_admin(db_session, user, test_project.id)
    assert exc.value.status_code == 403


def test_assert_project_member_bypassed_for_global_admin(db_session, test_project):
    from app.api.v1.endpoints.integrations import _assert_project_member_or_admin

    admin = _make_user(db_session, "iglobal", role=UserRole.ADMIN)
    # No membership row needed — global admins bypass.
    _assert_project_member_or_admin(db_session, admin, test_project.id)


def test_assert_project_member_no_op_when_project_id_is_none(db_session):
    """An integration created without a project_id (user-scoped only)
    must NOT trigger the membership check."""
    from app.api.v1.endpoints.integrations import _assert_project_member_or_admin

    user = _make_user(db_session, "ipersonal")
    # No exception, no DB read needed.
    _assert_project_member_or_admin(db_session, user, None)


def test_assert_project_member_rejects_membership_in_other_project(db_session):
    """Belonging to project A doesn't grant access to project B."""
    from app.api.v1.endpoints.integrations import _assert_project_member_or_admin

    user = _make_user(db_session, "icross")
    project_a = Project(
        name="project-a", slug="project-a", description="",
    )
    project_b = Project(
        name="project-b", slug="project-b", description="",
    )
    db_session.add_all([project_a, project_b])
    db_session.flush()
    db_session.add(
        ProjectMembership(
            project_id=project_a.id, user_id=user.id, role="analyst",
        )
    )
    db_session.flush()
    with pytest.raises(HTTPException) as exc:
        _assert_project_member_or_admin(db_session, user, project_b.id)
    assert exc.value.status_code == 403
