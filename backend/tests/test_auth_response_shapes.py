"""Response shapes on four auth routes (owner decision 56d, 2026-10-10).

``POST /auth/register`` answers the SAME row the users list returns
(``UserListItem``), so a page that created an account can put the answer into
its table as it is.  ``GET /auth/sessions``, ``POST /auth/change-password`` and
``POST /auth/2fa/disable`` declare what they return; their payloads did not
change.  No answer carries a password, a hash or a 2FA secret.
"""
from datetime import datetime

import pytest
from sqlalchemy import text

from app.api.v1.endpoints.users import UserListItem
from app.core.security import create_session, verify_token, create_access_token
from app.main import app

from tests.conftest import TEST_USER_PASSWORD

NEW_PASSWORD = "A-fresh-Passw0rd!-for-linus"
SECRET_WORDS = ("password", "hash", "secret", "token")


@pytest.fixture(autouse=True)
def _user_ids_start_past_the_fixture(db_session):
    """``test_user`` is inserted with an explicit id of 1, which the sequence
    does not know about: the first account the route creates would collide."""
    db_session.execute(text("SELECT setval(pg_get_serial_sequence('users', 'id'), 1000)"))


def _register(client, **over):
    body = {"username": "linus", "password": NEW_PASSWORD, "full_name": "Linus T.", "role": "member"}
    body.update(over)
    return client.post("/api/v1/auth/register", json=body)


def test_register_answers_the_users_list_row(client, test_user):
    created = _register(client)
    assert created.status_code == 200, created.text
    row = created.json()

    assert set(row) == set(UserListItem.model_fields)
    assert row["username"] == "linus" and row["role"] == "member"
    assert row["created_by_id"] == test_user.id
    assert row["totp_enabled"] is False
    assert row["email"] is None

    listed = {u["id"]: u for u in client.get("/api/v1/users/").json()}
    assert listed[row["id"]] == row
    assert client.get(f"/api/v1/users/{row['id']}").json() == row


def test_register_never_answers_a_secret(client):
    created = _register(client)
    assert created.status_code == 200, created.text
    assert NEW_PASSWORD not in created.text
    assert not [k for k in created.json() if any(word in k.lower() for word in SECRET_WORDS)]


def test_sessions_change_password_and_2fa_disable_declare_their_answer():
    paths = app.openapi()["paths"]
    for method, path in (
        ("post", "/api/v1/auth/register"),
        ("get", "/api/v1/auth/sessions"),
        ("post", "/api/v1/auth/change-password"),
        ("post", "/api/v1/auth/2fa/disable"),
    ):
        schema = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema, f"{method.upper()} {path} declares no response model"

    register = paths["/api/v1/auth/register"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    users = paths["/api/v1/users/{user_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert register == users  # one shape


def test_sessions_payload_is_what_it_was(client, db_session, test_user):
    token = create_access_token({"sub": str(test_user.id)})
    mine = create_session(db=db_session, user=test_user, token_jti=verify_token(token)["jti"])

    rows = client.get("/api/v1/auth/sessions", headers={"Authorization": f"Bearer {token}"})
    assert rows.status_code == 200
    (row,) = rows.json()
    assert set(row) == {
        "id", "ip_address", "user_agent", "created_at", "last_activity", "expires_at", "current",
    }
    assert row["id"] == mine.id and row["current"] is True
    # The same instants (a declared model writes UTC as "Z", where the bare
    # dict wrote "+00:00": one moment, two spellings).
    for key in ("created_at", "last_activity", "expires_at"):
        assert datetime.fromisoformat(row[key].replace("Z", "+00:00")) == getattr(mine, key), key
    # A session row never names its token.
    assert "token_jti" not in row


def test_change_password_and_2fa_disable_payloads_are_what_they_were(client, test_user):
    disabled = client.post("/api/v1/auth/2fa/disable", json={"password": TEST_USER_PASSWORD})
    assert disabled.status_code == 200, disabled.text
    assert disabled.json() == {"disabled": True}

    changed = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": TEST_USER_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json() == {
        "message": "Password successfully changed. All sessions have been revoked — please log in again."
    }
