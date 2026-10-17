"""A TOTP code works once, and password re-authentication with a live token is
counted (review 2026-10-10, H26).

* ``totp_service.verify_code`` accepted a code for its 30-second step and the
  two beside it and kept no memory, so the same six digits completed
  ``/auth/login/2fa`` again and again for about 90 seconds.
  ``users.totp_last_step`` is the memory (revision ``a4f8c2e6b0d1``);
  ``totp_service.accept_code`` is the one place a code is accepted.
* ``/auth/change-password``, ``/auth/2fa/disable`` and
  ``/auth/2fa/recovery-codes`` checked the password of an already signed-in
  caller with no limit: a stolen bearer token was an unmetered password
  oracle.  They share one budget, counted from the audit log
  (``security.reauth_password``).
"""
from __future__ import annotations

import time

import pyotp

from app.api.v1.endpoints.auth import _2FA_CHALLENGE_PURPOSE
from app.core.security import REAUTH_THROTTLE_FAILURES, create_access_token
from app.db.models_auth import AuditLog, User, UserRole
from app.services import totp_service
from tests.conftest import TEST_USER_PASSWORD


def _totp_user(db_session):
    secret = totp_service.generate_secret()
    user = User(
        id=2,  # explicit: test_user is id=1 and doesn't bump the sequence
        username="totp-replay", email="totp-replay@example.com", full_name="TOTP Replay",
        hashed_password="x", role=UserRole.MEMBER, is_active=True,
        totp_enabled=True, totp_secret_encrypted=totp_service.encrypt_secret(secret),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user, pyotp.TOTP(secret)


def _second_step(client, user, code):
    challenge = create_access_token(data={"sub": str(user.id), "purpose": _2FA_CHALLENGE_PURPOSE})
    return client.post("/api/v1/auth/login/2fa", json={"challenge_token": challenge, "code": code})


def test_the_same_code_signs_in_once(client, db_session):
    user, totp = _totp_user(db_session)
    now = time.time()
    code = totp.at(now)

    first = _second_step(client, user, code)
    assert first.status_code == 200, first.text
    assert first.json()["access_token"]

    again = _second_step(client, user, code)
    assert again.status_code == 401
    assert again.json()["detail"] == "Invalid authentication code"
    # Refused like any wrong code — so replays use up the second factor's budget.
    assert db_session.query(AuditLog).filter(
        AuditLog.action == "login_2fa_failed", AuditLog.user_id == user.id,
    ).count() == 1

    # The next step's code is a new code.
    later = _second_step(client, user, totp.at(now + 30))
    assert later.status_code == 200, later.text


def test_a_code_older_than_the_last_accepted_one_is_refused(client, db_session):
    """The window still reaches back one step for a slow clock — but not
    behind a code that has already been accepted."""
    user, totp = _totp_user(db_session)
    now = time.time()

    assert _second_step(client, user, totp.at(now)).status_code == 200
    assert _second_step(client, user, totp.at(now - 30)).status_code == 401
    db_session.expire_all()
    assert db_session.get(User, user.id).totp_last_step == int(now) // 30


def test_accept_code_spends_the_step_and_a_wrong_code_spends_nothing(db_session):
    user, totp = _totp_user(db_session)
    now = time.time()

    assert totp_service.accept_code(db_session, user, "000000" if totp.at(now) != "000000" else "111111", now=now) is False
    db_session.expire_all()
    assert db_session.get(User, user.id).totp_last_step is None

    assert totp_service.accept_code(db_session, user, totp.at(now), now=now) is True
    assert totp_service.accept_code(db_session, user, totp.at(now), now=now) is False
    # Thirty seconds on, the code of the step just accepted is still inside the
    # window, and still refused.
    assert totp_service.accept_code(db_session, user, totp.at(now), now=now + 30) is False
    assert totp_service.accept_code(db_session, user, totp.at(now + 30), now=now + 30) is True


def test_the_code_that_confirmed_an_enrolment_does_not_also_sign_in(client, db_session, test_user):
    setup = client.post("/api/v1/auth/2fa/setup", json={"password": TEST_USER_PASSWORD})
    assert setup.status_code == 200, setup.text
    totp = pyotp.TOTP(setup.json()["secret"])
    now = time.time()
    code = totp.at(now)

    assert client.post("/api/v1/auth/2fa/enable", json={"code": code}).status_code == 200
    assert _second_step(client, test_user, code).status_code == 401
    assert _second_step(client, test_user, totp.at(now + 30)).status_code == 200


def test_a_quick_re_enrolment_accepts_its_first_code(client, db_session, test_user):
    """Second review 2026-10-14 (nitpick): the last step accepted outlived
    turning 2FA off, so enrolling again within the same 30 seconds refused
    the new secret's first code."""
    setup = client.post("/api/v1/auth/2fa/setup", json={"password": TEST_USER_PASSWORD}).json()
    first = pyotp.TOTP(setup["secret"])
    assert client.post("/api/v1/auth/2fa/enable", json={"code": first.now()}).status_code == 200
    assert client.post("/api/v1/auth/2fa/disable", json={"password": TEST_USER_PASSWORD}).status_code == 200
    db_session.expire_all()
    assert db_session.get(User, test_user.id).totp_last_step is None
    again = pyotp.TOTP(client.post("/api/v1/auth/2fa/setup", json={"password": TEST_USER_PASSWORD}).json()["secret"])
    assert client.post("/api/v1/auth/2fa/enable", json={"code": again.now()}).status_code == 200


def test_last_login_is_stamped_when_the_sign_in_completes_not_at_the_password(client, db_session):
    """Review 2026-10-16, N11: a right password on a 2FA account stamped
    `last_login` before the second factor was given (or ever given)."""
    from app.core.security import get_password_hash

    user, totp = _totp_user(db_session)
    user.hashed_password = get_password_hash("half-a-sign-in")
    db_session.commit()

    half = client.post("/api/v1/auth/login", json={"username": user.username, "password": "half-a-sign-in"})
    assert half.status_code == 200 and "challenge_token" in half.json()
    db_session.expire_all()
    assert db_session.get(User, user.id).last_login is None

    assert _second_step(client, user, totp.now()).status_code == 200
    db_session.expire_all()
    assert db_session.get(User, user.id).last_login is not None


def test_binding_an_authenticator_needs_the_password_and_a_wrong_code_is_recorded(client, db_session, test_user):
    """Review 2026-10-16, N9: /2fa/setup asked for no password, so a stolen
    session token on an account without 2FA could bind its own authenticator;
    a wrong code at /enable was recorded nowhere."""
    assert client.post("/api/v1/auth/2fa/setup", json={}).status_code == 422
    wrong = client.post("/api/v1/auth/2fa/setup", json={"password": "not-the-password"})
    assert wrong.status_code == 403
    assert db_session.get(User, test_user.id).totp_secret_encrypted is None
    # The wrong password is counted by the shared re-authentication limit.
    assert db_session.query(AuditLog).filter_by(user_id=test_user.id, action="password_reauth_failed").count() == 1

    setup = client.post("/api/v1/auth/2fa/setup", json={"password": TEST_USER_PASSWORD})
    assert setup.status_code == 200
    totp = pyotp.TOTP(setup.json()["secret"])
    bad = "000000" if totp.now() != "000000" else "111111"
    assert client.post("/api/v1/auth/2fa/enable", json={"code": bad}).status_code == 400
    assert db_session.query(AuditLog).filter_by(user_id=test_user.id, action="2fa_enable_failed").count() == 1


# ---------------------------------------------------------------------------
# Password re-authentication with a live token
# ---------------------------------------------------------------------------

def _wrong_disables(client, times: int) -> list:
    return [
        client.post("/api/v1/auth/2fa/disable", json={"password": f"not-the-password-{i}"}).status_code
        for i in range(times)
    ]


def test_wrong_passwords_on_2fa_disable_are_throttled(client, db_session, test_user):
    assert _wrong_disables(client, REAUTH_THROTTLE_FAILURES) == [403] * REAUTH_THROTTLE_FAILURES

    # The budget is spent: the next guess is refused before it is checked —
    # even the RIGHT password answers 429, so nothing is learned from it.
    throttled = client.post("/api/v1/auth/2fa/disable", json={"password": TEST_USER_PASSWORD})
    assert throttled.status_code == 429
    assert "Too many incorrect passwords" in throttled.json()["detail"]

    failures = db_session.query(AuditLog).filter(
        AuditLog.action == "password_reauth_failed", AuditLog.user_id == test_user.id,
    ).all()
    assert len(failures) == REAUTH_THROTTLE_FAILURES
    assert {f.details["purpose"] for f in failures} == {"2fa_disable"}
    assert db_session.query(AuditLog).filter(
        AuditLog.action == "login_throttled", AuditLog.user_id == test_user.id,
    ).count() == 1


def test_the_three_reauth_routes_share_one_budget(client, db_session, test_user):
    test_user.totp_enabled = True   # /2fa/recovery-codes answers 400 without it
    db_session.commit()
    spread = [
        client.post("/api/v1/auth/2fa/disable", json={"password": "wrong-1"}).status_code,
        client.post("/api/v1/auth/2fa/recovery-codes", json={"password": "wrong-2"}).status_code,
        client.post("/api/v1/auth/change-password", json={
            "current_password": "wrong-3", "new_password": "A-New-Password-456!",
        }).status_code,
    ]
    assert spread == [403, 403, 400]
    assert _wrong_disables(client, REAUTH_THROTTLE_FAILURES - 3) == [403] * (REAUTH_THROTTLE_FAILURES - 3)

    for path, body in (
        ("/api/v1/auth/change-password",
         {"current_password": TEST_USER_PASSWORD, "new_password": "A-New-Password-456!"}),
        ("/api/v1/auth/2fa/recovery-codes", {"password": TEST_USER_PASSWORD}),
        ("/api/v1/auth/2fa/disable", {"password": TEST_USER_PASSWORD}),
    ):
        assert client.post(path, json=body).status_code == 429, path
    # Nothing was changed by the throttled requests.
    db_session.expire_all()
    assert db_session.get(User, test_user.id).totp_enabled is True


def test_other_accounts_failures_at_the_same_address_do_not_lock_this_one(client, db_session, test_user):
    """Review 2026-10-14 (nitpick): the re-authentication limit is per
    ACCOUNT, but it also counted every account's failures from the caller's
    address — 20 of them behind one NAT locked everyone there out of
    change-password and 2FA."""
    from datetime import datetime, timezone

    for n in range(30):
        db_session.add(AuditLog(
            action="password_reauth_failed", user_id=None, success=False,
            details={"username": f"someone-{n}", "purpose": "2fa_disable"},
            ip_address="testclient", timestamp=datetime.now(timezone.utc),
        ))
    db_session.commit()
    done = client.post("/api/v1/auth/2fa/disable", json={"password": TEST_USER_PASSWORD})
    assert done.status_code == 200, done.text


def test_a_few_wrong_passwords_do_not_lock_the_owner_out(client, test_user):
    """Control: under the limit the right password still works, so the 429
    above is the limit and not a broken route."""
    assert _wrong_disables(client, REAUTH_THROTTLE_FAILURES - 1) == [403] * (REAUTH_THROTTLE_FAILURES - 1)
    done = client.post("/api/v1/auth/2fa/disable", json={"password": TEST_USER_PASSWORD})
    assert done.status_code == 200 and done.json() == {"disabled": True}
