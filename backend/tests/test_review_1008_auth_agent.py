"""Review 2026-10-08, auth + agent surface.

* A password change, an admin password reset and an admin two-factor reset end
  the user's agent sessions, and the agent auth chain refuses a key issued
  before its operator's password last changed.
* The pre-assist browse reads refuse a filter they cannot understand.
* One operator and one project per key; a key whose session and agent name
  different projects is refused.
* An agent call's authentication and authorization are one read.
* The sign-in lockout is per (username, client address).
* The prompt version is a constant; an oversized image is refused by its
  declared size.
"""
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from starlette.requests import Request

from app.api import deps
from app.core.security import (
    LOGIN_2FA_THROTTLE_PER_USERNAME,
    LOGIN_LOCKOUT_FAILURES,
    LOGIN_THROTTLE_PER_USERNAME,
    create_access_token,
    get_password_hash,
    login_lockout_active,
)
from app.db import models
from app.db.models_agent import Agent, AgentSession
from app.db.models_auth import APIKey, AuditLog, User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.main import app
from app.services import agent_session_service as sessions
from tests.conftest import TEST_USER_PASSWORD

NEW_PASSWORD = "Another-Password-456!"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _member(db, project, *, user_id=91001, role=ProjectRole.ANALYST.value, password=None):
    user = User(
        id=user_id,
        username=f"operator-{user_id}",
        email=f"operator-{user_id}@example.com",
        hashed_password=get_password_hash(password) if password else "$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
    db.commit()
    return user


def _session_key(db, project, user):
    """A live project session for ``user`` minted the way the start route does."""
    agent = sessions.resolve_project_agent(db, project_id=project.id, user=user)
    session = sessions.create_agent_session(
        db, project_id=project.id, agent_id=agent.id, started_by_id=user.id,
    )
    raw = sessions.mint_session_key(db, agent=agent, session=session)
    db.commit()
    return raw, session


def _identity(client, raw):
    return client.get("/api/v1/agent/identity", headers={"X-API-Key": raw})


def _mcp_call(client, raw):
    return client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "agent_identity", "arguments": {}}},
        headers={"X-API-Key": raw},
    )


def _assert_refused_everywhere(client, raw):
    assert _identity(client, raw).status_code == 401
    assert client.post("/api/v1/agent/session/renew", headers={"X-API-Key": raw}).status_code == 401
    # The MCP transport forwards the key, so it is refused there too: a call
    # with no usable credential is a real HTTP 401.
    over_mcp = _mcp_call(client, raw)
    refused = over_mcp.status_code == 401 or over_mcp.json().get("result", {}).get("isError")
    assert refused, over_mcp.text


# ---------------------------------------------------------------------------
# A. replaced credentials end the user's agent sessions
# ---------------------------------------------------------------------------

def test_an_admin_password_reset_ends_the_users_agent_sessions(client, db_session, test_project):
    user = _member(db_session, test_project)
    raw, session = _session_key(db_session, test_project, user)
    assert _identity(client, raw).status_code == 200
    assert _mcp_call(client, raw).json()["result"]["isError"] is False

    reset = client.post(f"/api/v1/users/{user.id}/reset-password", json={"new_password": NEW_PASSWORD})
    assert reset.status_code == 200, reset.text

    _assert_refused_everywhere(client, raw)
    db_session.expire_all()
    session = db_session.get(AgentSession, session.id)
    assert session.status == "ended" and session.end_reason == "operator"
    assert "password was reset by an administrator" in session.notes

    # A session the user starts after the reset is theirs and works.
    fresh, _ = _session_key(db_session, test_project, user)
    assert _identity(client, fresh).status_code == 200


def test_changing_your_own_password_ends_your_agent_sessions(client, db_session, test_project, test_user):
    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "before"})
    assert started.status_code == 201, started.text
    raw = started.json()["api_key"]
    session_id = _identity(client, raw).json()["session_id"]

    changed = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": TEST_USER_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200, changed.text

    _assert_refused_everywhere(client, raw)
    session = db_session.get(AgentSession, session_id)
    db_session.refresh(session)
    assert session.status == "ended"
    assert "changed their password" in session.notes

    again = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "after"})
    assert again.status_code == 201, again.text
    assert _identity(client, again.json()["api_key"]).status_code == 200


def test_an_admin_two_factor_reset_ends_the_users_agent_sessions(client, db_session, test_project):
    user = _member(db_session, test_project)
    raw, session = _session_key(db_session, test_project, user)
    assert _identity(client, raw).status_code == 200

    assert client.post(f"/api/v1/users/{user.id}/reset-2fa").status_code == 200

    _assert_refused_everywhere(client, raw)
    db_session.expire_all()
    assert db_session.get(AgentSession, session.id).status == "ended"


def test_the_gate_refuses_a_key_issued_before_the_password_changed(client, db_session, test_project):
    """The second lock on the same door: even with its session still active,
    a key older than its operator's password is refused — and cannot renew."""
    user = _member(db_session, test_project)
    raw, session = _session_key(db_session, test_project, user)
    assert _identity(client, raw).status_code == 200

    user.password_changed_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    db_session.commit()

    refused = _identity(client, raw)
    assert refused.status_code == 401
    assert refused.json()["detail"]["error"] == "operator_credentials_changed"
    assert refused.json()["detail"]["recoverable"] is False
    renew = client.post("/api/v1/agent/session/renew", headers={"X-API-Key": raw})
    assert renew.status_code == 401
    assert renew.json()["detail"]["error"] == "operator_credentials_changed"
    assert db_session.get(AgentSession, session.id).status == "active"

    # A key issued after the change — the operator's own resume — works.
    user.password_changed_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    db_session.commit()
    agent = db_session.get(Agent, session.agent_id)
    resumed = sessions.resume_agent_session(db_session, session, agent=agent, resumed_by=user)
    db_session.commit()
    assert _identity(client, raw).status_code == 401  # the rotated-out key
    assert _identity(client, resumed).status_code == 200


def test_a_password_change_with_no_timestamp_refuses_nothing(client, db_session, test_project):
    user = _member(db_session, test_project)
    raw, _ = _session_key(db_session, test_project, user)
    user.password_changed_at = None
    db_session.commit()
    assert _identity(client, raw).status_code == 200


# ---------------------------------------------------------------------------
# B. a filter that cannot be understood is refused, not answered with nothing
# ---------------------------------------------------------------------------

def test_agent_scans_refuses_a_created_after_it_cannot_read(client, db_session, test_project):
    user = _member(db_session, test_project)
    raw, _ = _session_key(db_session, test_project, user)
    db_session.add(models.Scan(project_id=test_project.id, filename="recent.xml"))
    db_session.commit()
    headers = {"X-API-Key": raw}

    bad = client.get("/api/v1/agent/scans", params={"created_after": "yesterday"}, headers=headers)
    assert bad.status_code == 422, bad.text
    assert "yesterday" in bad.json()["detail"] and "created_after" in bad.json()["detail"]

    good = client.get(
        "/api/v1/agent/scans", params={"created_after": "2000-01-01T00:00:00Z"}, headers=headers,
    )
    assert good.status_code == 200 and len(good.json()) == 1
    assert len(client.get("/api/v1/agent/scans", headers=headers).json()) == 1


@pytest.mark.parametrize("name", ["ports", "services", "subnets"])
def test_agent_hosts_refuses_a_list_filter_that_names_nothing(client, db_session, test_project, name):
    user = _member(db_session, test_project)
    raw, _ = _session_key(db_session, test_project, user)
    db_session.add(models.Host(project_id=test_project.id, ip_address="10.91.0.1", state="up"))
    db_session.commit()

    resp = client.get("/api/v1/agent/hosts", params={name: " , "}, headers={"X-API-Key": raw})
    assert resp.status_code == 422, resp.text
    assert name in resp.json()["detail"]


# ---------------------------------------------------------------------------
# D. one operator, one project
# ---------------------------------------------------------------------------

def test_a_key_whose_session_and_agent_disagree_on_the_project_is_refused(client, db_session, test_project):
    user = _member(db_session, test_project)
    other = Project(name="other-project", slug="other-project", description="x")
    db_session.add(other)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=other.id, user_id=user.id, role=ProjectRole.ANALYST.value))
    raw, session = _session_key(db_session, test_project, user)
    assert _identity(client, raw).status_code == 200

    session.project_id = other.id
    db_session.commit()
    assert _identity(client, raw).status_code == 403


def test_agent_writes_are_attributed_to_the_one_operator(client, db_session, test_project):
    user = _member(db_session, test_project)
    raw, _ = _session_key(db_session, test_project, user)
    host = models.Host(project_id=test_project.id, ip_address="10.91.0.2", state="up")
    db_session.add(host)
    db_session.commit()
    headers = {"X-API-Key": raw}

    identity = _identity(client, raw).json()
    note = client.post(f"/api/v1/agent/hosts/{host.id}/notes", headers=headers, json={"body": "n"})
    assert note.status_code == 201, note.text
    assert identity["operator"]["id"] == note.json()["author_id"] == user.id


# ---------------------------------------------------------------------------
# E. the auth chain is one read and one rate-bucket write
# ---------------------------------------------------------------------------

def _agent_request(raw, method="GET", path="/identity"):
    class _Route:
        pass
    route = _Route()
    route.path = path
    return Request({
        "type": "http", "method": method, "path": f"/api/v1/agent{path}", "query_string": b"",
        "headers": [(b"x-api-key", raw.encode()), (b"user-agent", b"curl/8.5.0")],
        "route": route,
    })


def _run_chain(db, raw, **kwargs):
    request = _agent_request(raw, **kwargs)
    agent = deps.get_current_agent(request=request, credentials=None, db=db)
    agent = deps.check_agent_rate_limit(agent=agent, db=db)
    agent = deps.enforce_agent_operator_access(request=request, agent=agent)
    return request, agent


def test_an_agent_calls_auth_path_is_two_statements(db_session, test_project):
    user = _member(db_session, test_project)
    raw, session = _session_key(db_session, test_project, user)
    # The first call also stamps last-used and the client; measure a later one.
    _run_chain(db_session, raw)
    agent_id, project_id, user_id = session.agent_id, test_project.id, user.id

    statements = []

    def _count(conn, cursor, statement, params, context, executemany):
        if re.match(r"\s*(SELECT|INSERT|UPDATE|DELETE)\b", statement, re.I):
            statements.append(" ".join(statement.split())[:140])

    engine = db_session.get_bind().engine
    event.listen(engine, "before_cursor_execute", _count)
    try:
        request, agent = _run_chain(db_session, raw, method="POST", path="/hosts/{host_id}/notes")
        # What a handler reads next is already loaded.
        assert (agent.id, agent.project_id, agent.owner_id) == (agent_id, project_id, user_id)
        assert agent.name and agent.rate_limit_rpm
        assert request.state.key_operator_id == user_id
        assert request.state.agent_project_id == project_id
        assert request.state.key_operator_role == ProjectRole.ANALYST.value
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert len(statements) == 2, "\n".join(statements)
    assert statements[0].upper().startswith("SELECT")
    assert "agent_rate_buckets" in statements[1]


def _selects_during(db, call):
    statements = []

    def _count(conn, cursor, statement, params, context, executemany):
        if re.match(r"\s*SELECT\b", statement, re.I):
            statements.append(" ".join(statement.split()))

    engine = db.get_bind().engine
    event.listen(engine, "before_cursor_execute", _count)
    try:
        response = call()
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    return response, statements


@pytest.mark.parametrize("path", [
    "/api/v1/agent/assist/hosts?limit=5&state=up",       # project only
    "/api/v1/agent/assist/coverage",                     # project only
    "/api/v1/agent/assist/workbench/terrain",            # project only
    "/api/v1/agent/proposals?mine=true",                 # the session's id
    "/api/v1/agent/host-tests",                          # the session, for attribution
])
def test_an_assist_read_does_not_read_the_session_or_the_operator_again(
    client, db_session, test_project, path,
):
    user = _member(db_session, test_project)
    raw, _ = _session_key(db_session, test_project, user)
    db_session.add(models.Host(project_id=test_project.id, ip_address="10.71.0.1", state="up"))
    db_session.commit()
    headers = {"X-API-Key": raw}
    assert client.get(path, headers=headers).status_code == 200  # stamps last-used

    response, statements = _selects_during(db_session, lambda: client.get(path, headers=headers))
    assert response.status_code == 200, response.text
    # The key's read joins the session, the operator and the membership; no
    # handler selects any of them again.
    again = [
        s for s in statements
        if re.search(r"\bFROM (agent_sessions|users|project_memberships)\b", s)
    ]
    assert again == [], "\n".join(again)
    assert sum("FROM api_keys" in s for s in statements) == 1, "\n".join(statements)


def test_reading_the_session_itself_costs_no_second_read_of_it(client, db_session, test_project):
    user = _member(db_session, test_project)
    raw, session = _session_key(db_session, test_project, user)
    headers = {"X-API-Key": raw}
    path = "/api/v1/agent/assist/session"
    assert client.get(path, headers=headers).status_code == 200

    response, statements = _selects_during(db_session, lambda: client.get(path, headers=headers))
    assert response.json()["id"] == session.id
    assert response.json()["operator"]["id"] == user.id
    assert not [s for s in statements if re.search(r"\bFROM agent_sessions\b", s)], "\n".join(statements)


def test_a_dsl_read_judges_follow_for_the_operator_without_reading_the_session(
    client, db_session, test_project,
):
    user = _member(db_session, test_project)
    raw, _ = _session_key(db_session, test_project, user)
    mine = models.Host(project_id=test_project.id, ip_address="10.71.1.1", state="up")
    other = models.Host(project_id=test_project.id, ip_address="10.71.1.2", state="up")
    db_session.add_all([mine, other])
    db_session.flush()
    db_session.add(models.HostFollow(host_id=mine.id, user_id=user.id, status="in_review"))
    db_session.commit()
    headers = {"X-API-Key": raw}
    path = "/api/v1/agent/assist/hosts?q=follow:mine"
    assert client.get(path, headers=headers).status_code == 200

    response, statements = _selects_during(db_session, lambda: client.get(path, headers=headers))
    assert [h["ip_address"] for h in response.json()["items"]] == ["10.71.1.1"]
    assert not [s for s in statements if re.search(r"\bFROM agent_sessions\b", s)]


# ---------------------------------------------------------------------------
# F. the sign-in lockout is per (username, client address)
# ---------------------------------------------------------------------------

def _login(client, password, username="test-admin"):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def test_wrong_passwords_lock_out_the_address_that_guessed_not_the_account(client, db_session, test_user):
    for _ in range(LOGIN_LOCKOUT_FAILURES):
        assert _login(client, "wrong-password").status_code == 401

    # The guessing address is locked out, even with the right password …
    locked = _login(client, TEST_USER_PASSWORD)
    assert locked.status_code == 429, locked.text
    # … and so is a guess at a name that is no account: the answer does not
    # say which usernames exist.
    for _ in range(LOGIN_LOCKOUT_FAILURES):
        assert _login(client, "wrong-password", username="nobody-here").status_code == 401
    assert _login(client, "wrong-password", username="nobody-here").status_code == 429

    # … while the account's owner signs in from their own address.
    with TestClient(app, client=("198.51.100.7", 50000)) as elsewhere:
        owner = _login(elsewhere, TEST_USER_PASSWORD)
    assert owner.status_code == 200, owner.text
    assert owner.json()["access_token"]



def test_the_account_row_holds_no_lockout_state():
    """The lockout is per (username, address) from the audit log; a counter on
    the account would let any address lock its owner out again."""
    assert not {"failed_login_attempts", "locked_until"} & set(User.__table__.columns.keys())


def test_a_correct_password_resets_that_addresses_count(client, db_session, test_user):
    for _ in range(LOGIN_LOCKOUT_FAILURES - 1):
        assert _login(client, "wrong-password").status_code == 401
    assert _login(client, TEST_USER_PASSWORD).status_code == 200
    for _ in range(LOGIN_LOCKOUT_FAILURES - 1):
        assert _login(client, "wrong-password").status_code == 401
    assert _login(client, TEST_USER_PASSWORD).status_code == 200


def test_old_failures_do_not_lock(db_session, test_user):
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    for _ in range(LOGIN_LOCKOUT_FAILURES):
        db_session.add(AuditLog(action="login_failed", details={"username": "test-admin"},
                                ip_address="203.0.113.9", timestamp=stale, success=False))
    db_session.commit()
    assert login_lockout_active(db_session, "test-admin", "203.0.113.9") is False

    db_session.add(AuditLog(action="login_failed", details={"username": "test-admin"},
                            ip_address="203.0.113.9", success=False))
    for _ in range(LOGIN_LOCKOUT_FAILURES - 1):
        db_session.add(AuditLog(action="login_failed", details={"username": "test-admin"},
                                ip_address="203.0.113.9", success=False))
    db_session.commit()
    assert login_lockout_active(db_session, "test-admin", "203.0.113.9") is True
    assert login_lockout_active(db_session, "test-admin", "203.0.113.10") is False


def test_guessing_spread_over_many_addresses_still_meets_a_ceiling(client, db_session, test_user):
    for i in range(LOGIN_THROTTLE_PER_USERNAME):
        db_session.add(AuditLog(action="login_failed", details={"username": "test-admin"},
                                ip_address=f"203.0.113.{i % 250}", success=False))
    db_session.commit()
    assert _login(client, TEST_USER_PASSWORD).status_code == 429


def test_password_guessing_does_not_use_up_the_second_factors_budget(client, db_session):
    user = User(id=91050, username="totp-owner", email="totp-owner@example.com",
                hashed_password="x", role=UserRole.MEMBER, is_active=True,
                is_verified=True, totp_enabled=True)
    db_session.add(user)
    for i in range(LOGIN_2FA_THROTTLE_PER_USERNAME + 2):
        db_session.add(AuditLog(action="login_failed", details={"username": user.username},
                                ip_address=f"203.0.113.{i}", success=False))
    db_session.commit()

    challenge = create_access_token(
        data={"sub": str(user.id), "purpose": deps.TWO_FACTOR_CHALLENGE_PURPOSE},
    )
    resp = client.post("/api/v1/auth/login/2fa", json={"challenge_token": challenge, "code": "123456"})
    assert resp.status_code == 401, resp.text  # a wrong code, not a throttle


# ---------------------------------------------------------------------------
# G / H. the prompt version; an oversized image
# ---------------------------------------------------------------------------

def test_the_prompt_version_is_a_dotted_number_with_a_note():
    from app.services import agent_prompt_service

    assert re.fullmatch(r"\d+\.\d+\.\d+", agent_prompt_service.PROMPT_VERSION)
    assert agent_prompt_service.PROMPT_CHANGES.strip()
    assert sessions.PROMPT_VERSION == agent_prompt_service.PROMPT_VERSION


def test_an_oversized_image_is_refused_by_its_declared_size():
    from app.api.v1.endpoints import mcp_assist

    class _Response:
        headers = {"content-type": "image/png",
                   "content-length": str(mcp_assist._MAX_INLINE_IMAGE_BYTES + 1)}

        @property
        def content(self):
            raise AssertionError("the body of an oversized image must not be read")

    result = mcp_assist._tool_image_result(_Response(), "/api/v1/agent/assist/x")
    assert result["isError"] is True
    assert f"{mcp_assist._MAX_INLINE_IMAGE_BYTES + 1:,} bytes" in result["content"][0]["text"]

    class _Small:
        headers = {"content-type": "image/png", "content-length": "4"}
        content = b"\x89PNG"

    ok = mcp_assist._tool_image_result(_Small(), "/api/v1/agent/assist/x")
    assert ok["isError"] is False and ok["content"][1]["type"] == "image"
