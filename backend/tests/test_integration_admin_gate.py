"""Integration credentials are admin-only infrastructure.

create/update/delete/test on /integrations require a global admin — they manage
scanner/LLM secrets and the test probe is a network-egress primitive that a
lower-priv user could turn into an internal port/timing oracle.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db
from app.db.models_auth import User, UserRole
from app.api.v1.endpoints.auth import get_current_user


@pytest.fixture
def member_client(db_session):
    member = User(
        id=2,  # explicit: avoids the id=1 sequence collision with test_user
        username="plain-member",
        email="plain@example.com",
        full_name="Plain Member",
        hashed_password="x",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
    )
    db_session.add(member)
    db_session.commit()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: member
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_member_cannot_test_integration(member_client):
    resp = member_client.post(
        "/api/v1/integrations/test",
        json={"integration_type": "ollama", "base_url": "http://10.0.0.5:8080"},
    )
    assert resp.status_code == 403


def test_member_cannot_create_integration(member_client):
    resp = member_client.post(
        "/api/v1/integrations/",
        json={"name": "x", "integration_type": "ollama", "base_url": "http://example.com"},
    )
    assert resp.status_code == 403
