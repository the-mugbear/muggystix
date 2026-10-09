"""A sign-in session ends a fixed time after the user's last ACTIVITY.

Until 2.472.0 it ended ``ACCESS_TOKEN_EXPIRE_MINUTES`` after sign-in, however
busy the user was.  Now ``POST /auth/session/renew`` starts that time again,
and the client calls it when the person at the keyboard does something.

What is pinned here:

* a renewal moves the session's end and answers a token for the SAME session
  (one row, one ``jti``), so revoking it still ends every copy;
* an ordinary request does NOT move the end — pages poll, and an unattended
  tab must not keep itself signed in;
* a renewal never revives a session that is over or revoked, and a token that
  is not a session (a 2FA challenge) cannot renew.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.api import deps
from app.api.deps import get_current_user
from app.core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    verify_token,
)
from app.db.models_auth import AuditLog, UserSession
from app.main import app
from tests.conftest import TEST_USER_PASSWORD

LIFETIME = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
RENEW = "/api/v1/auth/session/renew"


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@pytest.fixture
def signed_in(client, db_session, test_user):
    """A real sign-in: the token is checked by the real ``get_current_user``."""
    app.dependency_overrides.pop(get_current_user)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": test_user.username, "password": TEST_USER_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return client, token


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _row(db_session, token):
    db_session.expire_all()
    return db_session.query(UserSession).filter(
        UserSession.token_jti == verify_token(token)["jti"]
    ).one()


def _age(db_session, token, *, left: timedelta):
    """As if time had passed: the session now has ``left`` to run."""
    row = _row(db_session, token)
    row.expires_at = datetime.now(timezone.utc) + left
    db_session.commit()


def test_a_renewal_moves_the_end_and_answers_a_token_for_the_same_session(signed_in, db_session):
    client, token = signed_in
    _age(db_session, token, left=timedelta(minutes=3))

    before = datetime.now(timezone.utc)
    resp = client.post(RENEW, headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == ACCESS_TOKEN_EXPIRE_MINUTES * 60

    # The same session: one row, the same jti, a full lifetime from now.
    renewed = verify_token(body["access_token"])
    assert renewed["jti"] == verify_token(token)["jti"]
    assert db_session.query(UserSession).count() == 1
    row = _row(db_session, token)
    assert _aware(row.expires_at) >= before + LIFETIME
    # The token's own expiry agrees with the row's (whole seconds).
    assert abs(renewed["exp"] - _aware(row.expires_at).timestamp()) < 2

    # The new token is the session; so is the old one, until its own expiry.
    for live in (body["access_token"], token):
        assert client.get("/api/v1/auth/profile", headers=_bearer(live)).status_code == 200


def test_an_ordinary_request_does_not_move_the_end(signed_in, db_session, monkeypatch):
    client, token = signed_in
    _age(db_session, token, left=timedelta(minutes=3))
    end = _aware(_row(db_session, token).expires_at)
    # Every request writes its sign of life, as if the debounce had run out.
    monkeypatch.setattr(deps, "_USER_SESSION_ACTIVITY_DEBOUNCE_SECONDS", 0.0)

    for path in ("/api/v1/auth/profile", "/api/v1/auth/sessions"):
        assert client.get(path, headers=_bearer(token)).status_code == 200

    assert _aware(_row(db_session, token).expires_at) == end


def test_revoking_the_session_ends_the_renewed_token_too(signed_in, db_session):
    client, token = signed_in
    renewed = client.post(RENEW, headers=_bearer(token)).json()["access_token"]

    assert client.post("/api/v1/auth/logout", headers=_bearer(renewed)).status_code == 200

    for dead in (renewed, token):
        assert client.get("/api/v1/auth/profile", headers=_bearer(dead)).status_code == 401
        assert client.post(RENEW, headers=_bearer(dead)).status_code == 401


def test_a_session_that_is_over_is_not_revived(signed_in, db_session):
    client, token = signed_in
    _age(db_session, token, left=timedelta(seconds=-1))

    assert client.post(RENEW, headers=_bearer(token)).status_code == 401
    assert _aware(_row(db_session, token).expires_at) < datetime.now(timezone.utc)


def test_a_token_that_is_not_a_session_cannot_renew(signed_in, test_user):
    client, _ = signed_in
    challenge = create_access_token(
        data={"sub": str(test_user.id), "purpose": deps.TWO_FACTOR_CHALLENGE_PURPOSE},
    )
    # A well-signed token for the right user whose jti names no session.
    stray = create_access_token(data={"sub": str(test_user.id)})

    for token in (challenge, stray):
        assert client.post(RENEW, headers=_bearer(token)).status_code == 401
    assert client.post(RENEW).status_code in (401, 403)


def test_a_renewal_is_not_an_audit_event(signed_in, db_session):
    client, token = signed_in
    before = db_session.query(AuditLog).count()

    assert client.post(RENEW, headers=_bearer(token)).status_code == 200

    db_session.expire_all()
    assert db_session.query(AuditLog).count() == before
