"""Per-project RBAC on shared-state mutation endpoints.

viewer/auditor are read-only project roles (models_project.ProjectRole), but
the host-assign, bulk-assign, and tag-mutation endpoints historically gated on
*membership* only (``get_current_project``) — so a viewer could assign hosts,
fire notifications/webhooks, and edit tags.  These tests pin the fix: those
routes require analyst+, while a read (list tags) stays open to a viewer.

The default ``client`` fixture authenticates as a GLOBAL admin, which bypasses
``require_project_role`` — so here we override ``get_current_user`` to a plain
member and drive their per-project role via a ProjectMembership row.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db import models
from app.db.session import get_db
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership
from app.api.v1.endpoints.auth import get_current_user


@pytest.fixture
def member(db_session):
    """A non-admin user (global role 'member'); per-project role comes from
    the membership row each test creates."""
    user = User(
        id=2,  # explicit: test_user is id=1 and doesn't bump the sequence
        username="member-user",
        email="member@example.com",
        full_name="Member User",
        hashed_password="x",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def member_client(db_session, member):
    """A TestClient authenticated as ``member`` (not the admin test_user)."""
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: member
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _set_role(db_session, project, member, role):
    db_session.query(ProjectMembership).filter(
        ProjectMembership.project_id == project.id,
        ProjectMembership.user_id == member.id,
    ).delete()
    db_session.add(ProjectMembership(project_id=project.id, user_id=member.id, role=role))
    db_session.commit()


def _host(db_session, project):
    h = models.Host(ip_address="10.9.9.9", state="up", project_id=project.id)
    db_session.add(h)
    db_session.commit()
    db_session.refresh(h)
    return h


def _base(project):
    return f"/api/v1/projects/{project.id}/hosts"


def test_viewer_cannot_create_tag(member_client, db_session, test_project, member):
    _set_role(db_session, test_project, member, "viewer")
    resp = member_client.post(f"{_base(test_project)}/tags", json={"name": "prod"})
    assert resp.status_code == 403


def test_auditor_cannot_bulk_assign(member_client, db_session, test_project, member):
    _set_role(db_session, test_project, member, "auditor")
    host = _host(db_session, test_project)
    resp = member_client.post(
        f"{_base(test_project)}/bulk/assign",
        json={"host_ids": [host.id], "assignee_user_id": member.id},
    )
    assert resp.status_code == 403


def test_viewer_can_still_list_tags(member_client, db_session, test_project, member):
    _set_role(db_session, test_project, member, "viewer")
    resp = member_client.get(f"{_base(test_project)}/tags")
    assert resp.status_code == 200


def test_analyst_can_create_tag(member_client, db_session, test_project, member):
    _set_role(db_session, test_project, member, "analyst")
    resp = member_client.post(f"{_base(test_project)}/tags", json={"name": "prod"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "prod"
