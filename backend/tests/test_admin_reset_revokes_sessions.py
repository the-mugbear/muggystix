"""Admin password reset must revoke the target user's live sessions.

The self-service change-password path revokes all sessions so a stolen token
dies with the password change.  Administrative reset is a recovery / lockout
action and must do the same — otherwise an already-issued token for the victim
stays valid for its full lifetime (``must_change_password`` only gates the next
login, which a live token skips).

Driven through the ``client`` fixture, which authenticates as the global admin
(``require_role(UserRole.ADMIN)`` on the endpoint).
"""
from datetime import datetime, timezone, timedelta

from app.db.models_auth import User, UserRole, UserSession


def _target_with_session(db_session):
    target = User(
        id=2,  # explicit: test_user is id=1 and doesn't bump the sequence
        username="victim",
        email="victim@example.com",
        full_name="Victim",
        hashed_password="x",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
    )
    db_session.add(target)
    db_session.flush()
    session = UserSession(
        user_id=target.id,
        token_jti="jti-victim-1",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=8),
    )
    db_session.add(session)
    db_session.commit()
    return target, session


def test_admin_reset_revokes_live_sessions(client, db_session):
    target, session = _target_with_session(db_session)

    resp = client.post(
        f"/api/v1/users/{target.id}/reset-password",
        json={"new_password": "Recovered-Pw-9!x"},
    )
    assert resp.status_code == 200

    db_session.expire_all()
    refreshed = db_session.query(UserSession).filter(UserSession.id == session.id).first()
    assert refreshed.revoked_at is not None
    assert refreshed.revoked_reason == "admin_password_reset"
