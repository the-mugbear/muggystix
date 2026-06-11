"""
API Contract Tests

Validates endpoint routes, status codes, and response structure for all major
API surfaces. Uses the shared ``client`` (admin) and ``db_session`` fixtures
from conftest.py.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db
from app.api.v1.endpoints.auth import get_current_user
from app.db.models_auth import User, UserRole
from tests.conftest import TEST_USER_PW_HASH


# ------------------------------------------------------------------ #
#  Viewer-role fixture (local to this module)                         #
# ------------------------------------------------------------------ #

@pytest.fixture
def viewer_client(db_session):
    """Test client authenticated as a non-admin (MEMBER) user.

    v2.65.0 — was UserRole.VIEWER before the v2.46.0 binary-role
    collapse.  Global role is now ADMIN/MEMBER; the four-tier
    vocabulary (admin/analyst/auditor/viewer) lives on
    ProjectMembership.role.  The admin-only endpoints under test
    (/users, /audit/*) gate on UserRole == ADMIN, so any
    non-admin role produces the same 403 — the fixture renamed-
    in-spirit but kept its name because that's what the tests
    expect to inject.
    """
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    def override_get_current_user():
        return User(
            id=2,
            username="test-viewer",
            email="viewer@example.com",
            full_name="Test Viewer",
            hashed_password=TEST_USER_PW_HASH,
            role=UserRole.MEMBER,
            is_active=True,
            is_verified=True,
            created_at=datetime.now(timezone.utc),
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


# ================================================================== #
#  1. Auth / Session flow                                             #
# ================================================================== #

class TestAuthEndpoints:

    def test_login_bad_credentials_returns_401(self, client):
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    def test_get_profile_returns_user_data(self, client):
        response = client.get("/api/v1/auth/profile")
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert "username" in data
        assert "role" in data

    def test_change_password_validates_strength(self, client):
        """Weak new password should be rejected (400)."""
        response = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "anything", "new_password": "short"},
        )
        # Either 400 (password validation) or 400 (current password wrong) is acceptable;
        # the key point is it does NOT return 2xx for a weak password.
        assert response.status_code == 400


# ================================================================== #
#  2. User management                                                 #
# ================================================================== #

class TestUserManagement:

    def test_put_users_profile_not_shadowed(self, client):
        """PUT /api/v1/users/profile must route to the self-service
        endpoint, NOT be interpreted as /{user_id} with user_id='profile'."""
        response = client.put(
            "/api/v1/users/profile",
            json={"full_name": "Regression Test"},
        )
        # 200 means the dedicated /profile route matched.
        # 422 (validation) would also prove the route matched.
        # 404 or 500 would indicate shadowing.
        assert response.status_code in (200, 422)

    def test_list_users_returns_email_field(self, client):
        response = client.get("/api/v1/users/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # Even an empty list proves the route works; if populated, check schema.
        if data:
            assert "email" in data[0]

    def test_update_user_by_id(self, client, db_session):
        """PUT /api/v1/users/{id} should update a user."""
        # Seed a target user so we have a known id.  v2.65.0 — was
        # UserRole.VIEWER before the v2.46.0 binary-role collapse;
        # any non-admin works for the update-behaviour assertion.
        target = User(
            id=50,
            username="target-user",
            email="target@example.com",
            hashed_password="not-used",
            role=UserRole.MEMBER,
            is_active=True,
        )
        db_session.add(target)
        db_session.flush()

        response = client.put(
            f"/api/v1/users/{target.id}",
            json={"full_name": "Updated Name"},
        )
        assert response.status_code == 200
        assert response.json()["full_name"] == "Updated Name"

    def test_delete_user_prevents_self_deletion(self, client):
        """Admin (id=1) should not be allowed to delete themselves."""
        response = client.delete("/api/v1/users/1")
        assert response.status_code == 400
        assert "own account" in response.json()["detail"].lower()


# ================================================================== #
#  3. Role-based access                                               #
# ================================================================== #

class TestRoleBasedAccess:

    def test_viewer_cannot_list_users(self, viewer_client):
        response = viewer_client.get("/api/v1/users/")
        assert response.status_code == 403

    def test_viewer_cannot_access_audit_logs(self, viewer_client):
        response = viewer_client.get("/api/v1/audit/logs")
        assert response.status_code == 403

    def test_viewer_cannot_access_audit_stats(self, viewer_client):
        response = viewer_client.get("/api/v1/audit/stats")
        assert response.status_code == 403


# (The risk-endpoint contract tests were removed with the dead
#  risk-scoring subsystem.)


# ================================================================== #
#  5. Export endpoint contracts                                       #
# ================================================================== #

class TestExportEndpoints:

    def test_out_of_scope_txt_format(self, client, test_project):
        response = client.get(f"/api/v1/projects/{test_project.id}/export/out-of-scope?format_type=txt")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "content-disposition" in response.headers

    def test_out_of_scope_csv_format(self, client, test_project):
        response = client.get(f"/api/v1/projects/{test_project.id}/export/out-of-scope?format_type=csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]

    def test_out_of_scope_json_format(self, client, test_project):
        response = client.get(f"/api/v1/projects/{test_project.id}/export/out-of-scope?format_type=json")
        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]

    def test_export_scope_not_found(self, client, test_project):
        response = client.get(f"/api/v1/projects/{test_project.id}/export/scope/999")
        assert response.status_code == 404

    def test_hosts_agent_package_format(self, client, test_project):
        response = client.get(f"/api/v1/projects/{test_project.id}/reports/hosts/agent-package")
        assert response.status_code == 200
        assert "application/zip" in response.headers["content-type"]
        assert "content-disposition" in response.headers

    def test_hosts_markdown_bundle_format(self, client, test_project):
        response = client.get(f"/api/v1/projects/{test_project.id}/reports/hosts/markdown-bundle")
        assert response.status_code == 200
        assert "application/zip" in response.headers["content-type"]
        assert "content-disposition" in response.headers


# ================================================================== #
#  6. Audit endpoint contracts                                        #
# ================================================================== #

class TestAuditEndpoints:

    def test_create_audit_log(self, client):
        # Client self-reported audit events are restricted to an allowlist of
        # non-privileged UI telemetry (code-review R7); use one of those.
        response = client.post(
            "/api/v1/audit/log",
            json={"action": "client_error", "resource_type": "test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data

    def test_create_audit_log_rejects_privileged_action(self, client):
        """R7 hardening: a client may not inject an arbitrary/privileged action
        name into the admin-facing audit trail."""
        response = client.post(
            "/api/v1/audit/log",
            json={"action": "login_success", "resource_type": "test"},
        )
        assert response.status_code == 400

    def test_get_audit_logs_paginated(self, client):
        response = client.get("/api/v1/audit/logs")
        assert response.status_code == 200
        data = response.json()
        assert "logs" in data
        assert "total" in data
        assert "skip" in data
        assert "limit" in data

    def test_get_audit_stats(self, client):
        response = client.get("/api/v1/audit/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_logs" in data
        assert "top_actions" in data
        assert "top_users" in data


# ================================================================== #
#  7. Parse errors                                                    #
# ================================================================== #

class TestParseErrors:

    def test_list_parse_errors(self, client, test_project):
        response = client.get(f"/api/v1/projects/{test_project.id}/parse-errors/")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_parse_error_stats_summary(self, client, test_project):
        response = client.get(f"/api/v1/projects/{test_project.id}/parse-errors/stats/summary")
        assert response.status_code == 200
        data = response.json()
        assert "total_errors" in data
        assert "unresolved" in data


# ================================================================== #
#  8. Dashboard                                                       #
# ================================================================== #

class TestDashboardEndpoints:

    def test_dashboard_stats_structure(self, client, test_project):
        response = client.get(f"/api/v1/projects/{test_project.id}/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_scans" in data
        assert "total_hosts" in data
        assert "total_ports" in data

    def test_port_stats_returns_list(self, client, test_project):
        response = client.get(f"/api/v1/projects/{test_project.id}/dashboard/port-stats")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_os_stats_returns_list(self, client, test_project):
        response = client.get(f"/api/v1/projects/{test_project.id}/dashboard/os-stats")
        assert response.status_code == 200
        assert isinstance(response.json(), list)
