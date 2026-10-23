"""Review 2026-10-23b — the backend platform (PLT).

* PLT-1 — the 2FA step's limit was checked against committed failures with
  nothing serializing parallel attempts: ~150 codes at once all got a guess.
* PLT-2 — the scan upload's door checked a token's signature only, so a
  viewer, a non-member, a revoked session or a viewer operator's key made the
  backend spool up to 2 GB before the route's 401 / 403.
* PLT-3 — two copies of "a user's memberships, with a global admin's implied
  projects".
* PLT-4 — the subnet-label assignment routes looked ids up one at a time.
* PLT-5 — the Sites list grouped every project's subnets.
* PLT-6 — audit.py kept its own copy of ``get_client_info``.
* PLT-7 — ``POST /audit/log`` took an unbounded ``details`` and copied a
  client's ``username`` into the column the sign-in limiter reads.
* PLT-8 — nits: the last-admin removal race, a concurrent add's 500, ingest
  settings on an archived project, split commits on logout / session revoke,
  the audit log's paging tiebreak, the self-demotion message, nullable
  ``is_active`` flags, a blocking read on the event loop, a removed member
  left as a site's owner.
"""
from __future__ import annotations

import inspect
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import event, text
from starlette.requests import Request

from app.api import deps
from app.core.security import create_access_token, create_session, verify_token
from app.db import models
from app.db.models_auth import AuditLog, User, UserRecoveryCode, UserRole, UserSession
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.services import totp_service
from tests.test_review_2026_10_10_agent_accounts import _analyst, _session_key
from tests.two_connections import two_sessions  # noqa: F401  (fixture)

PAGE_UPLOAD = "/api/v1/projects/{pid}/upload/"
AGENT_UPLOAD = "/api/v1/agent/uploads"


def _request(path="/"):
    return Request({
        "type": "http", "method": "POST", "path": path, "headers": [],
        "client": ("203.0.113.77", 1), "query_string": b"",
    })


def _user(db, user_id, *, role=UserRole.MEMBER, **extra):
    user = User(
        id=user_id, username=f"plt-{user_id}", email=f"plt-{user_id}@example.com",
        hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=role, is_active=True, **extra,
    )
    db.add(user)
    db.commit()
    return user


def _member(db, project, user, role):
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
    db.commit()


def _signed_in(db, user):
    """A bearer token with a live session row, as a sign-in leaves."""
    token = create_access_token({"sub": str(user.id), "username": user.username})
    create_session(db, user, verify_token(token)["jti"])
    return {"authorization": f"Bearer {token}"}


def _blocked(pair, thread, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline and thread.is_alive():
        with pair._engine.connect() as probe:
            if probe.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar():
                return True
        time.sleep(0.05)
    return False


def _commits(db):
    seen = []
    event.listen(db, "after_commit", lambda session: seen.append(1))
    return seen


# ---------------------------------------------------------------------- PLT-1

def test_parallel_wrong_codes_are_counted_one_at_a_time(two_sessions, monkeypatch):
    """With the account's limit at 1: the first wrong code is being checked
    when the second arrives.  The second must wait for the first's failure
    row and then be refused — it used to be checked against the same
    committed total (zero) and get a guess of its own."""
    from app.api.v1.endpoints import auth

    _project, user = two_sessions.project()
    two_sessions.commit(lambda db: db.query(User).filter(User.id == user.id).update({User.totp_enabled: True}))
    monkeypatch.setattr(auth, "LOGIN_2FA_THROTTLE_PER_USERNAME", 1)
    entered, gate = [], threading.Event()

    def checking(db, u, code):
        entered.append(code)
        gate.wait(10)
        return False

    monkeypatch.setattr(auth, "_verify_second_factor", checking)
    challenge = create_access_token({"sub": str(user.id), "purpose": auth._2FA_CHALLENGE_PURPOSE})
    outcome = {}

    def attempt(name, db, code):
        try:
            auth.login_2fa(auth.TwoFactorLoginRequest(challenge_token=challenge, code=code), _request(), db)
        except HTTPException as exc:
            outcome[name] = exc.status_code
        finally:
            db.rollback()

    first = threading.Thread(target=attempt, args=("first", two_sessions.a, "111111"), daemon=True)
    first.start()
    deadline = time.time() + 10
    while not entered and time.time() < deadline:
        time.sleep(0.02)
    assert entered == ["111111"]
    second = threading.Thread(target=attempt, args=("second", two_sessions.b, "222222"), daemon=True)
    second.start()
    try:
        assert _blocked(two_sessions, second), "the second code did not wait for the first"
        assert entered == ["111111"]                  # was ["111111", "222222"]
    finally:
        gate.set()
        first.join(10)
        second.join(10)
    assert outcome == {"first": 401, "second": 429}
    db = two_sessions.fresh()
    try:
        db.query(AuditLog).filter(AuditLog.user_id == user.id).delete()
        db.commit()
    finally:
        db.close()


def test_a_wrong_second_factor_commits_nothing_before_its_failure_row(db_session):
    """A commit inside the check would end the attempt lock before the wrong
    code is recorded: neither a wrong recovery code nor a spent TOTP code
    commits (both used to)."""
    from app.api.v1.endpoints.auth import _verify_second_factor

    secret = totp_service.generate_secret()
    user = _user(db_session, 96001, totp_enabled=True,
                 totp_secret_encrypted=totp_service.encrypt_secret(secret))
    db_session.add(UserRecoveryCode(user_id=user.id, code_hash=totp_service.hash_recovery_code("right-code1")))
    db_session.commit()
    commits = _commits(db_session)

    assert _verify_second_factor(db_session, user, "wrong-code1") is False
    user.totp_last_step = 10 ** 12                      # every current code already spent
    db_session.flush()
    import pyotp
    assert _verify_second_factor(db_session, user, pyotp.TOTP(secret).now()) is False
    assert commits == []
    assert _verify_second_factor(db_session, user, "right-code1") is True
    assert commits == [1]                               # a spent code is committed at once


def test_a_challenge_minted_before_a_password_change_does_not_finish_signing_in(client, db_session):
    from app.api.v1.endpoints.auth import _2FA_CHALLENGE_PURPOSE

    user = _user(db_session, 96002, totp_enabled=True,
                 totp_secret_encrypted=totp_service.encrypt_secret(totp_service.generate_secret()))
    challenge = create_access_token({"sub": str(user.id), "purpose": _2FA_CHALLENGE_PURPOSE})
    body = {"challenge_token": challenge, "code": "123456"}
    assert client.post("/api/v1/auth/login/2fa", json=body).status_code == 401
    assert "challenge" not in client.post("/api/v1/auth/login/2fa", json=body).json()["detail"]

    user.password_changed_at = datetime.now(timezone.utc) + timedelta(seconds=5)   # a reset after it
    db_session.commit()
    refused = client.post("/api/v1/auth/login/2fa", json=body)
    assert refused.status_code == 401
    assert refused.json()["detail"] == "Invalid or expired 2FA challenge"        # was "Invalid authentication code"


# ---------------------------------------------------------------------- PLT-2

def test_the_page_upload_door_asks_what_the_route_asks(db_session, test_project):
    path = PAGE_UPLOAD.format(pid=test_project.id)
    analyst = _user(db_session, 96101)
    _member(db_session, test_project, analyst, ProjectRole.ANALYST.value)
    assert deps.large_upload_refusal(path, _signed_in(db_session, analyst)) is None

    viewer = _user(db_session, 96102)
    _member(db_session, test_project, viewer, ProjectRole.VIEWER.value)
    assert deps.large_upload_refusal(path, _signed_in(db_session, viewer)) == (
        403, "Insufficient project role. Required: analyst",                     # was None
    )
    stranger = _user(db_session, 96103)
    assert deps.large_upload_refusal(path, _signed_in(db_session, stranger)) == (
        403, "Not a member of this project",                                     # was None
    )
    admin = _user(db_session, 96104, role=UserRole.ADMIN)
    assert deps.large_upload_refusal(path, _signed_in(db_session, admin)) is None

    # A session ended by logout / a reset: the token still verifies.
    headers = _signed_in(db_session, analyst)
    jti = verify_token(headers["authorization"].split()[1])["jti"]
    db_session.query(UserSession).filter_by(token_jti=jti).update({"revoked_at": datetime.now(timezone.utc)})
    db_session.commit()
    assert deps.large_upload_refusal(path, headers) == (401, "Session expired or revoked")   # was None

    # A token with no session row at all (never signed in).
    bare = create_access_token({"sub": str(analyst.id)})
    assert deps.large_upload_refusal(path, {"authorization": f"Bearer {bare}"}) == (
        401, "Session expired or revoked",
    )

    headers = _signed_in(db_session, analyst)
    analyst.is_active = False
    db_session.commit()
    assert deps.large_upload_refusal(path, headers) == (401, "User not found or inactive")   # was None

    owing = _user(db_session, 96105, must_change_password=True)
    _member(db_session, test_project, owing, ProjectRole.ANALYST.value)
    assert deps.large_upload_refusal(path, _signed_in(db_session, owing)) == (403, "password_change_required")


def test_the_page_door_costs_two_reads(db_session, test_project):
    path = PAGE_UPLOAD.format(pid=test_project.id)
    analyst = _user(db_session, 96106)
    _member(db_session, test_project, analyst, ProjectRole.ANALYST.value)
    headers = _signed_in(db_session, analyst)
    statements = []
    conn = db_session.connection()
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(conn, "before_cursor_execute", listener)
    try:
        assert deps.large_upload_refusal(path, headers) is None
    finally:
        event.remove(conn, "before_cursor_execute", listener)
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 2, selects                   # the user with its session; the membership


def test_the_agent_upload_door_refuses_what_the_gate_refuses(db_session, test_project):
    operator = _analyst(db_session, test_project, user_id=96201)
    raw, session = _session_key(db_session, test_project, operator)
    assert deps.large_upload_refusal(AGENT_UPLOAD, {"x-api-key": raw}) is None

    membership = db_session.query(ProjectMembership).filter_by(
        project_id=test_project.id, user_id=operator.id,
    ).one()
    membership.role = ProjectRole.VIEWER.value
    db_session.commit()
    status, detail = deps.large_upload_refusal(AGENT_UPLOAD, {"x-api-key": raw})
    assert status == 403 and "read-only" in detail                               # was None

    membership.role = ProjectRole.ANALYST.value
    operator.password_changed_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    db_session.commit()
    status, detail = deps.large_upload_refusal(AGENT_UPLOAD, {"x-api-key": raw})
    assert status == 401 and detail["error"] == "operator_credentials_changed"   # was None

    operator.password_changed_at = datetime.now(timezone.utc) - timedelta(days=1)
    operator.is_active = False
    db_session.commit()
    status, detail = deps.large_upload_refusal(AGENT_UPLOAD, {"x-api-key": raw})
    assert status == 403 and "no longer an active user" in detail               # was None


def test_the_agent_door_ends_the_session_of_an_operator_who_left(db_session, test_project):
    from app.db.models_agent import AgentSession

    operator = _analyst(db_session, test_project, user_id=96202)
    operator.role = UserRole.ADMIN
    db_session.commit()
    raw, session = _session_key(db_session, test_project, operator)
    db_session.query(ProjectMembership).filter_by(project_id=test_project.id, user_id=operator.id).delete()
    operator.role = UserRole.MEMBER
    db_session.commit()
    status, detail = deps.large_upload_refusal(AGENT_UPLOAD, {"x-api-key": raw})
    assert status == 403 and "no longer a member" in detail
    db_session.expire_all()
    assert db_session.get(AgentSession, session.id).status == "ended"


def test_the_application_refuses_a_viewers_upload_before_its_route(client, db_session, test_project):
    viewer = _user(db_session, 96107)
    _member(db_session, test_project, viewer, ProjectRole.VIEWER.value)
    headers = _signed_in(db_session, viewer)
    r = client.post(
        PAGE_UPLOAD.format(pid=test_project.id),
        files={"file": ("scan.xml", b"<nmaprun/>")},
        headers={"Authorization": headers["authorization"]},
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "Insufficient project role. Required: analyst"}
    assert r.headers.get("connection") == "close"        # the middleware's answer, not the route's


# ---------------------------------------------------------------------- PLT-3

def test_both_membership_routes_are_one_list(client, db_session, test_user, test_project):
    from app.api.v1.endpoints.users import memberships_of

    archived = Project(name="plt3-archived", slug="plt3-archived", status="archived")
    other = Project(name="plt3-other", slug="plt3-other")
    db_session.add_all([archived, other])
    db_session.commit()
    mine = client.get("/api/v1/users/profile/projects")
    theirs = client.get(f"/api/v1/users/{test_user.id}/memberships")
    assert mine.status_code == theirs.status_code == 200
    assert mine.json() == theirs.json()
    names = [row["project_name"] for row in mine.json()]
    assert {"plt3-archived", "plt3-other"} <= set(names)      # a global admin's reach, archived included
    assert names == sorted(names, key=str.lower)
    assert [m.model_dump(mode="json") for m in memberships_of(db_session, test_user)] == mine.json()

    member = _user(db_session, 96301)
    _member(db_session, other, member, ProjectRole.AUDITOR.value)
    rows = client.get(f"/api/v1/users/{member.id}/memberships").json()
    assert [(r["project_name"], r["role"]) for r in rows] == [("plt3-other", "auditor")]


# ---------------------------------------------------------------------- PLT-4

def _scope_subnets(db, project, n):
    from app.services.default_scope import get_or_create_default_scope

    scope = get_or_create_default_scope(db, project.id)
    subnets = [models.Subnet(scope_id=scope.id, cidr=f"10.96.{i}.0/24") for i in range(n)]
    db.add_all(subnets)
    db.commit()
    return subnets


def _labels(db, project, n, tag):
    labels = [models.SubnetLabel(project_id=project.id, name=f"{tag}-{i}") for i in range(n)]
    db.add_all(labels)
    db.commit()
    return labels


def _statements_during(db, call):
    statements = []
    conn = db.connection()
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(conn, "before_cursor_execute", listener)
    try:
        result = call()
    finally:
        event.remove(conn, "before_cursor_execute", listener)
    return result, sum(1 for s in statements if s.lstrip().upper().startswith("SELECT"))


def test_label_assignment_checks_every_id_in_one_statement(client, db_session, test_project):
    base = f"/api/v1/projects/{test_project.id}/scopes"
    # Plain ids: a commit expires the objects, and re-reading them is not the route's cost.
    subnets = [s.id for s in _scope_subnets(db_session, test_project, 40)]
    labels = [lb.id for lb in _labels(db_session, test_project, 40, "plt4")]

    one, few = _statements_during(db_session, lambda: client.put(
        f"{base}/subnets/{subnets[0]}/labels", json={"label_ids": [labels[0]]}))
    many, more = _statements_during(db_session, lambda: client.put(
        f"{base}/subnets/{subnets[1]}/labels", json={"label_ids": labels}))
    assert one.status_code == many.status_code == 200
    assert len(many.json()) == 40
    assert more == few                         # was one SELECT per id (+39)

    apply_one, few = _statements_during(db_session, lambda: client.post(
        f"{base}/subnet-labels/{labels[1]}/subnets", json={"subnet_ids": [subnets[2]]}))
    apply_many, more = _statements_during(db_session, lambda: client.post(
        f"{base}/subnet-labels/{labels[2]}/subnets", json={"subnet_ids": subnets}))
    assert apply_one.status_code == apply_many.status_code == 200
    assert more == few

    # A duplicate id is one id; a foreign id refuses the whole request.
    dup = client.put(f"{base}/subnets/{subnets[3]}/labels",
                     json={"label_ids": [labels[0], labels[0]]})
    assert dup.status_code == 200 and len(dup.json()) == 1
    other = Project(name="plt4-other", slug="plt4-other")
    db_session.add(other)
    db_session.commit()
    foreign_label = _labels(db_session, other, 1, "plt4-foreign")[0].id
    assert client.put(f"{base}/subnets/{subnets[3]}/labels",
                      json={"label_ids": [labels[0], foreign_label]}).status_code == 404
    assert len(client.put(f"{base}/subnets/{subnets[3]}/labels",
                          json={"label_ids": [labels[0]]}).json()) == 1
    foreign_subnet = _scope_subnets(db_session, other, 1)[0].id
    assert client.post(f"{base}/subnet-labels/{labels[3]}/subnets",
                       json={"subnet_ids": [subnets[0], foreign_subnet]}).status_code == 404

    # The label set is capped (it had no cap at all).
    assert client.put(f"{base}/subnets/{subnets[4]}/labels",
                      json={"label_ids": list(range(1, 202))}).status_code == 422


# ---------------------------------------------------------------------- PLT-5

def test_site_counts_are_the_projects_own(client, db_session, test_project):
    """Old == new: the counts were right before (site ids are unique); the
    grouping now reads only this project's sites."""
    from app.services.default_scope import get_or_create_default_scope

    other = Project(name="plt5-other", slug="plt5-other")
    db_session.add(other)
    db_session.commit()
    mine = models.Site(project_id=test_project.id, name="plt5-hq")
    theirs = models.Site(project_id=other.id, name="plt5-branch")
    db_session.add_all([mine, theirs])
    db_session.commit()
    for project, site, n in ((test_project, mine, 2), (other, theirs, 3)):
        scope = get_or_create_default_scope(db_session, project.id)
        db_session.add_all([
            models.Subnet(scope_id=scope.id, cidr=f"10.{95 + n}.{i}.0/24", site_id=site.id) for i in range(n)
        ])
    db_session.commit()
    rows = client.get(f"/api/v1/projects/{test_project.id}/sites").json()
    assert [(r["name"], r["subnet_count"]) for r in rows] == [("plt5-hq", 2)]


# ---------------------------------------------------------------------- PLT-6

def test_the_audit_log_reads_the_one_client_address():
    from app.api.v1.endpoints import audit

    assert audit.get_client_info is deps.get_client_info


# ---------------------------------------------------------------------- PLT-7

def test_a_client_reported_event_is_bounded_and_kept_apart(client, db_session):
    too_big = client.post("/api/v1/audit/log", json={"action": "client_error", "details": {"x": "y" * 5000}})
    assert too_big.status_code == 422                                     # was 200
    long_id = client.post("/api/v1/audit/log", json={"action": "client_error", "resource_id": "r" * 51})
    assert long_id.status_code == 422                                     # was a 500 from the column

    tag = uuid.uuid4().hex
    r = client.post("/api/v1/audit/log", json={
        "action": "client_error", "details": {"username": f"victim-{tag}", "where": "page"},
    })
    assert r.status_code == 200, r.text
    row = db_session.get(AuditLog, r.json()["audit_id"])
    assert row.username is None                                           # was "victim-…"
    assert row.details == {"source": "client", "client": {"username": f"victim-{tag}", "where": "page"}}


# ---------------------------------------------------------------------- PLT-8

def test_two_removals_of_a_projects_two_admins_leave_one(two_sessions, monkeypatch):
    """The first removal is about to commit when the second arrives: the
    second waits on the admin rows, then sees one admin and refuses.  It used
    to count both (the first's delete was not committed) and remove the
    other — the project was left with no admin."""
    from app.api.v1.endpoints import projects

    project, first_admin = two_sessions.project(role=ProjectRole.ADMIN.value)
    tag = uuid.uuid4().hex[:8]

    def build(db):
        second = User(username=f"plt8-{tag}", email=f"plt8-{tag}@example.com",
                      hashed_password="x", role=UserRole.MEMBER, is_active=True)
        actor = User(username=f"plt8-actor-{tag}", email=f"plt8-actor-{tag}@example.com",
                     hashed_password="x", role=UserRole.ADMIN, is_active=True)
        db.add_all([second, actor])
        db.flush()
        db.add(ProjectMembership(project_id=project.id, user_id=second.id, role=ProjectRole.ADMIN.value))
        return second, actor

    db = two_sessions.fresh()
    try:
        second, actor = build(db)
        db.commit()
        second_id, actor_id = second.id, actor.id
    finally:
        db.close()
    two_sessions._user_ids.extend([second_id, actor_id])
    actor = type("Actor", (), {"id": actor_id, "role": UserRole.ADMIN, "username": "actor"})()

    gate, real_end = threading.Event(), projects.end_sessions_of_operator

    def held_open(db, user_id, **kw):
        if user_id == first_admin.id:
            gate.wait(10)                      # the first removal, before its commit
        return real_end(db, user_id, **kw)

    monkeypatch.setattr(projects, "end_sessions_of_operator", held_open)
    outcome = {}

    def remove(name, db, user_id):
        try:
            outcome[name] = projects.remove_member(project.id, user_id, _request(), db, actor)
        except HTTPException as exc:
            outcome[name] = exc.status_code
            db.rollback()

    first = threading.Thread(target=remove, args=("first", two_sessions.a, first_admin.id), daemon=True)
    first.start()
    time.sleep(0.5)
    second_t = threading.Thread(target=remove, args=("second", two_sessions.b, second_id), daemon=True)
    second_t.start()
    try:
        assert _blocked(two_sessions, second_t), "the second removal did not wait"   # it finished: both gone
    finally:
        gate.set()
        first.join(10)
        second_t.join(10)
    assert outcome["first"] == {"message": "Member removed from project"}
    assert outcome["second"] == 400
    check = two_sessions.fresh()
    try:
        admins = check.query(ProjectMembership).filter_by(project_id=project.id, role="admin").all()
        assert [m.user_id for m in admins] == [second_id]
    finally:
        check.close()


def test_a_member_added_twice_at_once_is_a_400_not_a_500(two_sessions):
    from app.api.v1.endpoints import projects

    project, admin = two_sessions.project(role=ProjectRole.ADMIN.value)
    tag = uuid.uuid4().hex[:8]
    newcomer = two_sessions.commit(lambda db: _new_user(db, f"plt8-add-{tag}"))
    two_sessions._user_ids.append(newcomer.id)
    actor = type("Actor", (), {"id": admin.id, "role": UserRole.MEMBER, "username": admin.username})()

    two_sessions.a.add(ProjectMembership(project_id=project.id, user_id=newcomer.id, role="viewer"))
    two_sessions.a.flush()                                 # the first add, not yet committed
    outcome = {}

    def add():
        try:
            outcome["second"] = projects.add_member(
                project.id, projects.MembershipCreate(user_id=newcomer.id, role="analyst"),
                _request(), two_sessions.b, actor,
            )
        except HTTPException as exc:
            outcome["second"] = exc.status_code
        except Exception as exc:  # noqa: BLE001 — the old 500
            outcome["second"] = type(exc).__name__
        finally:
            two_sessions.b.rollback()

    thread = threading.Thread(target=add, daemon=True)
    thread.start()
    assert _blocked(two_sessions, thread)
    two_sessions.a.commit()
    thread.join(10)
    assert outcome["second"] == 400                        # was IntegrityError


def _new_user(db, name):
    user = User(username=name, email=f"{name}@example.com", hashed_password="x",
                role=UserRole.MEMBER, is_active=True)
    db.add(user)
    return user


def test_ingest_settings_of_an_archived_project_are_a_410(client, db_session, test_project):
    url = f"/api/v1/projects/{test_project.id}/ingest-settings"
    assert client.patch(url, json={"skip_informational_findings": True}).status_code == 200
    test_project.status = "archived"
    db_session.commit()
    assert client.patch(url, json={"skip_informational_findings": False}).status_code == 410   # was 200
    db_session.refresh(test_project)
    assert test_project.skip_informational_findings is True


def test_logout_and_a_session_revoke_commit_once_with_their_audit_row(db_session, test_user):
    from fastapi.security import HTTPAuthorizationCredentials

    from app.api.v1.endpoints import auth

    token = create_access_token({"sub": str(test_user.id)})
    jti = verify_token(token)["jti"]
    create_session(db_session, test_user, jti)
    commits = _commits(db_session)
    auth.logout(_request(), test_user, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db_session)
    assert commits == [1]                                                 # was two
    assert db_session.query(UserSession).filter_by(token_jti=jti).one().revoked_reason == "logout"

    other = create_session(db_session, test_user, uuid.uuid4().hex)
    commits.clear()
    auth.revoke_session_endpoint(other.id, _request(), test_user, db_session)
    assert commits == [1]
    assert db_session.query(AuditLog).filter_by(action="session_revoked", resource_id=str(other.id)).count() == 1


def test_audit_log_pages_break_ties_by_id(client, db_session):
    action = f"plt8-tie-{uuid.uuid4().hex[:6]}"
    at = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    rows = [AuditLog(action=action, timestamp=at) for _ in range(4)]
    db_session.add_all(rows)
    db_session.commit()
    pages = [
        client.get("/api/v1/audit/logs", params={"action": action, "skip": skip, "limit": 1}).json()["logs"]
        for skip in range(4)
    ]
    assert [p[0]["id"] for p in pages] == sorted((r.id for r in rows), reverse=True)


def test_the_only_admin_demoting_themselves_is_told_so(client, db_session, test_user):
    r = client.put(f"/api/v1/users/{test_user.id}", json={"role": "member"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Cannot change your own admin role"     # was "…no active administrator"


def test_an_account_or_key_written_without_its_flag_is_active(db_session):
    db_session.execute(text(
        "INSERT INTO users (id, username, hashed_password, role) VALUES (96801, 'plt8-raw', 'x', 'member')"
    ))
    assert db_session.execute(text("SELECT is_active FROM users WHERE id = 96801")).scalar() is True
    with pytest.raises(Exception):
        with db_session.begin_nested():
            db_session.execute(text("UPDATE users SET is_active = NULL WHERE id = 96801"))
    from app.db.models_auth import APIKey
    for column in (User.__table__.c.is_active, APIKey.__table__.c.is_active):
        assert column.nullable is False


def test_the_identity_document_does_not_run_on_the_event_loop():
    from app import main

    assert not inspect.iscoroutinefunction(main.well_known_identity)


def test_a_removed_member_no_longer_owns_the_projects_sites(client, db_session, test_project):
    owner = _user(db_session, 96901)
    _member(db_session, test_project, owner, ProjectRole.ANALYST.value)
    site = models.Site(project_id=test_project.id, name="plt8-site")
    db_session.add(site)
    db_session.commit()
    assert client.patch(f"/api/v1/projects/{test_project.id}/sites/{site.id}",
                        json={"owner_id": owner.id}).status_code == 200
    assert client.delete(f"/api/v1/projects/{test_project.id}/members/{owner.id}").status_code == 200
    db_session.refresh(site)
    assert site.owner_id is None                                          # was the removed member
