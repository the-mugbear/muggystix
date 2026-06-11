"""Second-factor brute-force throttle + atomic recovery-code consumption.

The password step was already throttled (login_throttle_exceeded over the
audit-log window); the /login/2fa step was not, so an attacker holding a valid
5-min challenge could spray the 1e6 TOTP space. And recovery-code consumption
was a non-atomic read-then-write. These tests pin both fixes.
"""
from datetime import datetime, timezone

from app.core.security import create_access_token, LOGIN_THROTTLE_PER_USERNAME
from app.api.v1.endpoints.auth import _2FA_CHALLENGE_PURPOSE, _verify_second_factor
from app.services import totp_service
from app.db.models_auth import User, UserRole, AuditLog, UserRecoveryCode


def _totp_user(db_session):
    user = User(
        id=2,  # explicit: test_user is id=1 and doesn't bump the sequence
        username="totp-user",
        email="totp@example.com",
        full_name="TOTP User",
        hashed_password="x",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
        totp_enabled=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _challenge(user):
    return create_access_token(data={"sub": str(user.id), "purpose": _2FA_CHALLENGE_PURPOSE})


def test_2fa_throttled_after_repeated_failures(client, db_session):
    user = _totp_user(db_session)
    # Seed enough recent failed-login events for this username to trip the
    # per-username throttle.
    for _ in range(LOGIN_THROTTLE_PER_USERNAME):
        db_session.add(AuditLog(
            action="login_failed",
            details={"username": user.username},
            timestamp=datetime.now(timezone.utc),
        ))
    db_session.commit()

    resp = client.post(
        "/api/v1/auth/login/2fa",
        json={"challenge_token": _challenge(user), "code": "123456"},
    )
    assert resp.status_code == 429


def test_2fa_wrong_code_is_401_when_not_throttled(client, db_session):
    """Control: without the seeded failures the same request reaches code
    verification and fails 401 (not 429) — so 429 above is the throttle, not a
    rejected challenge."""
    user = _totp_user(db_session)
    resp = client.post(
        "/api/v1/auth/login/2fa",
        json={"challenge_token": _challenge(user), "code": "123456"},
    )
    assert resp.status_code == 401


def test_recovery_code_consumed_exactly_once(db_session):
    user = _totp_user(db_session)
    code = "abcde-fghij"
    db_session.add(UserRecoveryCode(
        user_id=user.id, code_hash=totp_service.hash_recovery_code(code),
    ))
    db_session.commit()

    assert _verify_second_factor(db_session, user, code) is True   # consumes it
    assert _verify_second_factor(db_session, user, code) is False  # already used
