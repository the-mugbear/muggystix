"""Review 2026-10-10 — the agent key and the account behind it.

* H4 — a key minted by Resume outlived the session's lifetime cap.
* H5 — deactivating an account did not end its agent sessions or its browser
  sessions, and its key could still be renewed.
"""
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db.models_agent import AgentSession
from app.db.models_auth import APIKey, User, UserRole, UserSession
from app.db.models_project import ProjectMembership, ProjectRole
from app.services import agent_session_service as sessions
from app.services.agent_key_ttl import session_renewal_deadline


def _aware(t):
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _live_key(db, session_id):
    return db.query(APIKey).filter(
        APIKey.agent_session_id == session_id, APIKey.is_active.is_(True),
    ).one()


# --------------------------------------------------------------------------- H4

def _session_near_the_cap(client, db, project, *, hours_left):
    started = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert started.status_code == 201, started.text
    session = db.get(AgentSession, started.json()["agent_session_id"])
    session.started_at = datetime.now(timezone.utc) - timedelta(
        hours=settings.AGENT_SESSION_MAX_LIFETIME_HOURS - hours_left,
    )
    db.commit()
    return session


def test_a_resumed_key_ends_with_the_session(client, db_session, test_project):
    """Resume at cap − 1 h: the replacement key was good for the whole TTL
    asked for, a day or more past the deadline nothing else then checked."""
    session = _session_near_the_cap(client, db_session, test_project, hours_left=1)
    deadline = session_renewal_deadline(session)

    resumed = client.post(
        f"/api/v1/projects/{test_project.id}/agent-sessions/{session.id}/resume", json={"ttl_hours": 24},
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()

    db_session.expire_all()
    key = _live_key(db_session, session.id)
    assert _aware(key.expires_at) <= deadline
    assert _aware(key.expires_at) > datetime.now(timezone.utc)
    # The answer says what the key really has, not what was asked for.
    assert body["key_ttl_hours"] == 1
    assert _aware(datetime.fromisoformat(body["key_expires_at"])) <= deadline
    # The key works, for the hour that is left.
    assert client.get("/api/v1/agent/identity", headers={"X-API-Key": body["api_key"]}).status_code == 200


def test_a_resume_well_inside_the_cap_gets_the_ttl_it_asked_for(client, db_session, test_project):
    session = _session_near_the_cap(client, db_session, test_project, hours_left=100)
    resumed = client.post(
        f"/api/v1/projects/{test_project.id}/agent-sessions/{session.id}/resume", json={"ttl_hours": 6},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["key_ttl_hours"] == 6
    db_session.expire_all()
    left = _aware(_live_key(db_session, session.id).expires_at) - datetime.now(timezone.utc)
    assert timedelta(hours=5, minutes=59) < left <= timedelta(hours=6)


def test_resume_takes_the_ttl_a_new_session_takes(client, db_session, test_project):
    session = _session_near_the_cap(client, db_session, test_project, hours_left=100)
    for bad in (25, 168, 0):
        resp = client.post(
            f"/api/v1/projects/{test_project.id}/agent-sessions/{session.id}/resume", json={"ttl_hours": bad},
        )
        assert resp.status_code == 422, f"{bad}: {resp.status_code} {resp.text[:200]}"
        assert "ttl_hours" in resp.text


def test_the_mint_is_where_the_key_is_cut(db_session, test_project, test_user):
    """Whoever calls the one mint, with whatever TTL."""
    agent = sessions.resolve_project_agent(db_session, project_id=test_project.id, user=test_user)
    session = sessions.create_agent_session(
        db_session, project_id=test_project.id, agent_id=agent.id, started_by_id=test_user.id,
    )
    session.started_at = datetime.now(timezone.utc) - timedelta(
        hours=settings.AGENT_SESSION_MAX_LIFETIME_HOURS - 2,
    )
    db_session.flush()
    sessions.mint_session_key(db_session, agent=agent, session=session, ttl_hours=168)
    db_session.commit()
    assert _aware(_live_key(db_session, session.id).expires_at) <= session_renewal_deadline(session)


# --------------------------------------------------------------------------- H5

def _analyst(db, project, user_id=94001):
    user = User(
        id=user_id, username=f"operator-{user_id}", email=f"operator-{user_id}@example.com",
        hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=UserRole.MEMBER, is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=ProjectRole.ANALYST.value))
    db.commit()
    return user


def _session_key(db, project, user):
    agent = sessions.resolve_project_agent(db, project_id=project.id, user=user)
    session = sessions.create_agent_session(
        db, project_id=project.id, agent_id=agent.id, started_by_id=user.id,
    )
    raw = sessions.mint_session_key(db, agent=agent, session=session)
    db.commit()
    return raw, session


def _renew(client, raw):
    return client.post("/api/v1/agent/session/renew", headers={"X-API-Key": raw})


def _identity(client, raw):
    return client.get("/api/v1/agent/identity", headers={"X-API-Key": raw})


def test_deactivating_an_account_ends_what_is_signed_in_as_it(client, db_session, test_project):
    user = _analyst(db_session, test_project)
    raw, session = _session_key(db_session, test_project, user)
    db_session.add(UserSession(
        user_id=user.id, token_jti="jti-94001-live",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=8),
    ))
    db_session.commit()
    assert _identity(client, raw).status_code == 200
    assert _renew(client, raw).status_code == 200

    off = client.put(f"/api/v1/users/{user.id}", json={"is_active": False})
    assert off.status_code == 200, off.text

    assert _renew(client, raw).status_code == 401          # was 200
    assert _identity(client, raw).status_code in (401, 403)
    db_session.expire_all()
    ended = db_session.get(AgentSession, session.id)
    assert ended.status == "ended"
    assert "account was deactivated by an administrator" in ended.notes
    browser = db_session.query(UserSession).filter_by(token_jti="jti-94001-live").one()
    assert browser.revoked_at is not None and browser.revoked_reason == "account_deactivated"

    # Switching the account back on brings nothing back.
    on = client.put(f"/api/v1/users/{user.id}", json={"is_active": True})
    assert on.status_code == 200, on.text
    assert _identity(client, raw).status_code == 401       # was 200
    assert _renew(client, raw).status_code == 401
    db_session.expire_all()
    assert db_session.query(UserSession).filter_by(token_jti="jti-94001-live").one().revoked_at is not None
    # A session they start afterwards is theirs and works.
    fresh, _ = _session_key(db_session, test_project, db_session.get(User, user.id))
    assert _identity(client, fresh).status_code == 200


def test_an_update_that_does_not_deactivate_ends_nothing(client, db_session, test_project):
    user = _analyst(db_session, test_project)
    raw, session = _session_key(db_session, test_project, user)
    assert client.put(f"/api/v1/users/{user.id}", json={"full_name": "Renamed"}).status_code == 200
    assert client.put(f"/api/v1/users/{user.id}", json={"is_active": True}).status_code == 200
    assert _identity(client, raw).status_code == 200
    db_session.expire_all()
    assert db_session.get(AgentSession, session.id).status == "active"


def test_renewal_refuses_a_key_whose_operator_is_inactive(client, db_session, test_project):
    """The renewal route is mounted outside the operator gate, so it checks
    for itself — however the account came to be inactive."""
    user = _analyst(db_session, test_project)
    raw, _session = _session_key(db_session, test_project, user)
    db_session.get(User, user.id).is_active = False        # not through the route
    db_session.commit()

    resp = _renew(client, raw)
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["error"] == "operator_inactive"
    assert resp.json()["detail"]["recoverable"] is False


def test_an_admin_password_reset_still_revokes_browser_sessions(client, db_session, test_project):
    """The shared step, on the path that had it: old == new."""
    user = _analyst(db_session, test_project)
    raw, session = _session_key(db_session, test_project, user)
    db_session.add(UserSession(
        user_id=user.id, token_jti="jti-94001-reset",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=8),
    ))
    db_session.commit()
    reset = client.post(
        f"/api/v1/users/{user.id}/reset-password", json={"new_password": "Another-Password-456!"},
    )
    assert reset.status_code == 200, reset.text
    db_session.expire_all()
    browser = db_session.query(UserSession).filter_by(token_jti="jti-94001-reset").one()
    assert browser.revoked_reason == "admin_password_reset"
    ended = db_session.get(AgentSession, session.id)
    assert ended.status == "ended" and "password was reset by an administrator" in ended.notes
    assert _renew(client, raw).status_code == 401
