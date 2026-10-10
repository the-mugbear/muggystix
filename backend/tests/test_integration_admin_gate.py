"""The people's routes for scanner integrations: who reads, who writes.

The installation has one list (2.482.0).  Every signed-in user reads it —
never a secret, and with who configured each.  create / update / delete /
test need a GLOBAL admin, on any row: they manage scanner secrets, and the
test probe is a network-egress primitive that a lower-priv user could turn
into an internal port/timing oracle.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db
from app.db.models_auth import User, UserRole
from app.db.models_integrations import IntegrationCredential
from app.api.v1.endpoints.auth import get_current_user
from app.services.integration_service import IntegrationService

#: Recognisable, and in no response a person can read.
SECRET_ONE = "zebra-access-key-4471"
SECRET_TWO = "zebra-secret-key-9920"


def _member(db_session, user_id=2, username="plain-member"):
    member = User(
        id=user_id,  # explicit: avoids the id=1 sequence collision with test_user
        username=username,
        email=f"{username}@example.com",
        full_name="Plain Member",
        hashed_password="x",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
    )
    db_session.add(member)
    db_session.commit()
    return member


@pytest.fixture
def member_client(db_session):
    member = _member(db_session)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: member
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _integration(db_session, *, created_by_id, name="Shared Nessus"):
    return IntegrationService(db_session).create(
        created_by_id=created_by_id, name=name, integration_type="nessus",
        base_url="https://192.0.2.10:8834", secret=SECRET_ONE, secret2=SECRET_TWO,
        extra_config={"max_hosts_per_scan": 256},
    )


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


def test_a_member_reads_the_installations_list_without_any_secret(member_client, db_session):
    """Someone else configured it; the member sees it, who configured it, that
    secrets are set — and no secret, searched for in the serialized answer."""
    admin = _member(db_session, user_id=7, username="configuring-admin")
    row = _integration(db_session, created_by_id=admin.id)

    resp = member_client.get("/api/v1/integrations/")
    assert resp.status_code == 200, resp.text
    (item,) = resp.json()
    assert item["id"] == row.id
    assert item["created_by"] == "configuring-admin"
    assert item["has_secret"] is True and item["has_secret2"] is True
    assert item["extra_config"] == {"max_hosts_per_scan": 256}
    # No ownership fields, and no key a secret could travel under.
    assert set(item) == {
        "id", "name", "integration_type", "base_url", "has_secret", "has_secret2",
        "extra_config", "is_active", "created_by", "created_at", "updated_at",
    }
    assert SECRET_ONE not in resp.text and SECRET_TWO not in resp.text
    assert row.secret_encrypted not in resp.text


def test_a_member_cannot_change_or_delete_an_integration(member_client, db_session):
    row = _integration(db_session, created_by_id=None)
    assert member_client.patch(
        f"/api/v1/integrations/{row.id}", json={"name": "taken"},
    ).status_code == 403
    assert member_client.delete(f"/api/v1/integrations/{row.id}").status_code == 403
    db_session.expire_all()
    assert db_session.get(IntegrationCredential, row.id).name == "Shared Nessus"


def test_a_global_admin_changes_and_deletes_a_row_someone_else_created(client, db_session):
    """``client`` is the global admin; the row was configured by another
    account.  Writes are no longer limited to one's own rows."""
    other = _member(db_session, user_id=8, username="first-admin")
    row = _integration(db_session, created_by_id=other.id)

    resp = client.patch(f"/api/v1/integrations/{row.id}", json={"name": "Renamed", "clear_secret2": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed" and body["created_by"] == "first-admin"
    assert body["has_secret"] is True and body["has_secret2"] is False
    assert SECRET_ONE not in resp.text

    assert client.delete(f"/api/v1/integrations/{row.id}").status_code == 204
    db_session.expire_all()
    assert db_session.get(IntegrationCredential, row.id) is None


def test_creating_records_who_configured_it_and_takes_no_project(client, db_session, test_user):
    resp = client.post("/api/v1/integrations/", json={
        "name": "Mine", "integration_type": "generic_api",
        "base_url": "https://192.0.2.10:8834", "secret": SECRET_ONE,
        # The per-project model is gone: a key the body no longer has is ignored.
        "project_id": 1,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created_by"] == test_user.username
    assert "project_id" not in body and SECRET_ONE not in resp.text
    assert db_session.get(IntegrationCredential, body["id"]).created_by_id == test_user.id


def test_deleting_the_account_that_configured_an_integration_keeps_it(client, db_session):
    """``created_by_id`` is provenance (SET NULL): the scanner is the
    installation's, not that person's."""
    other = _member(db_session, user_id=9, username="leaving-admin")
    row = _integration(db_session, created_by_id=other.id)

    assert client.delete(f"/api/v1/users/{other.id}").status_code == 200
    db_session.expire_all()
    kept = db_session.get(IntegrationCredential, row.id)
    assert kept is not None and kept.created_by_id is None

    (item,) = client.get("/api/v1/integrations/").json()
    assert item["id"] == row.id and item["created_by"] is None
