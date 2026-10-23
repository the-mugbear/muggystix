"""Review 2026-10-23, accounts and roles (H-DB1, H-DB2, H-DB3, R-DB1, R-DB3 and
the auth nitpicks).

* Creating an account is behind the password-change / 2FA gate.
* An admin 2FA reset ends the user's BROWSER sessions too, not only their
  agents'.
* The users list pages, in a stated order, and says how many there are.
* The project routes ask the one access rule of ``app.api.deps`` (old == new
  over every role).
* An account change and its audit row are one transaction.
* A NUL character in a new password is refused like a weak one, not a 500.
* A token's session must be its own user's.
* Two administrators cannot leave the installation with none.
* First boot: only an ACTIVE admin counts; a taken username is said, loudly.
* The first-boot credential file goes only with its own account's change.
"""
from __future__ import annotations

import secrets
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import startup
from app.api import deps
from app.api.deps import get_current_user
from app.api.v1.endpoints import auth as auth_endpoints
from app.api.v1.endpoints import users as users_endpoints
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_session,
    get_password_hash,
    validate_password_strength,
)
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.main import app
from app.startup import admin_marker_path, seed_default_admin
from tests.conftest import TEST_USER_PASSWORD
from tests.two_connections import two_sessions  # noqa: F401  (fixture)

STRONG = "A-Strong-Passw0rd!"
HASH = "$2b$12$abcdefghijklmnopqrstuv"   # never verified in these tests


def _user(db, uid, *, role=UserRole.MEMBER, username=None, active=True, **extra):
    user = User(
        id=uid, username=username or f"user-{uid}", email=f"user-{uid}@example.com",
        hashed_password=HASH, role=role, is_active=active, **extra,
    )
    db.add(user)
    db.commit()
    return user


def _token(db, user) -> str:
    """A real token for ``user``, with its session row (checked by the real
    ``get_current_user``)."""
    jti = secrets.token_urlsafe(16)
    create_session(db, user, jti)
    return create_access_token({"sub": str(user.id), "username": user.username}, jti=jti)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def real_auth(client):
    """The ``client`` with the REAL ``get_current_user`` (tokens are checked)."""
    app.dependency_overrides.pop(get_current_user)
    return client


# ---------------------------------------------------------------------------
# H-DB1 — register is behind the gate
# ---------------------------------------------------------------------------

def _register(client, token, username="newcomer"):
    return client.post("/api/v1/auth/register", headers=_bearer(token),
                       json={"username": username, "password": STRONG})


def test_an_admin_on_a_transit_password_cannot_create_accounts(real_auth, db_session):
    admin = _user(db_session, 92001, role=UserRole.ADMIN, must_change_password=True)
    resp = _register(real_auth, _token(db_session, admin))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "password_change_required"
    assert db_session.query(User).filter(User.username == "newcomer").first() is None


def test_an_admin_owing_2fa_enrolment_cannot_create_accounts(real_auth, db_session, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_2FA", True)
    admin = _user(db_session, 92002, role=UserRole.ADMIN)
    resp = _register(real_auth, _token(db_session, admin))
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "two_factor_setup_required"


def test_a_ready_admin_still_creates_accounts(real_auth, db_session):
    if db_session.bind.dialect.name == "postgresql":
        # The fixtures insert explicit ids (``test_user`` is id 1); the new
        # account takes the sequence's next, which must be past them.
        db_session.execute(text(
            "SELECT setval(pg_get_serial_sequence('users', 'id'), "
            "GREATEST((SELECT max(id) FROM users), 200000))"
        ))
    admin = _user(db_session, 92003, role=UserRole.ADMIN)
    resp = _register(real_auth, _token(db_session, admin))
    assert resp.status_code == 200, resp.text
    assert resp.json()["username"] == "newcomer"


# ---------------------------------------------------------------------------
# H-DB2 — an admin 2FA reset ends the user's browser sessions
# ---------------------------------------------------------------------------

def test_a_two_factor_reset_ends_the_users_browser_sessions(real_auth, db_session):
    admin = _user(db_session, 92011, role=UserRole.ADMIN)
    target = _user(db_session, 92012, totp_enabled=True)
    earlier = _token(db_session, target)
    assert real_auth.get("/api/v1/auth/profile", headers=_bearer(earlier)).status_code == 200

    resp = real_auth.post(f"/api/v1/users/{target.id}/reset-2fa", headers=_bearer(_token(db_session, admin)))
    assert resp.status_code == 200, resp.text

    refused = real_auth.get("/api/v1/auth/profile", headers=_bearer(earlier))
    assert refused.status_code == 401
    assert refused.json()["detail"] == "Session expired or revoked"
    # The token cannot start an agent session either.
    project = Project(name="p-2fa", slug="p-2fa")
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=project.id, user_id=target.id, role="analyst"))
    db_session.commit()
    start = real_auth.post(f"/api/v1/projects/{project.id}/assist/start", json={}, headers=_bearer(earlier))
    assert start.status_code == 401


# ---------------------------------------------------------------------------
# H-DB3 — the users list pages
# ---------------------------------------------------------------------------

def test_the_users_list_pages_over_every_account_once_in_username_order(client, db_session):
    db_session.add_all([
        User(id=93000 + i, username=f"acct-{i:03d}", email=f"acct-{i}@example.com",
             hashed_password=HASH, role=UserRole.MEMBER, is_active=True)
        for i in range(205)
    ])
    db_session.commit()
    everyone = db_session.query(User).count()
    assert everyone > 200

    seen = []
    offset = 0
    while True:
        page = client.get("/api/v1/users/", params={"limit": 100, "offset": offset}).json()
        assert page["total"] == everyone
        assert page["limit"] == 100 and page["offset"] == offset
        assert page["returned"] == len(page["items"])
        seen += [row["username"] for row in page["items"]]
        if not page["has_more"]:
            break
        offset += page["returned"]
    assert len(seen) == everyone
    assert len(set(seen)) == everyone
    assert seen == sorted(seen)

    # The default page is the first 100, and asking for more than 200 is refused.
    default = client.get("/api/v1/users/").json()
    assert default["returned"] == 100 and default["has_more"] is True
    assert client.get("/api/v1/users/", params={"limit": 201}).status_code == 422


# ---------------------------------------------------------------------------
# R-DB1 — the project routes ask the one rule (old == new)
# ---------------------------------------------------------------------------

def _old_can_manage(role, is_global_admin):
    """``projects._can_manage_project`` as it was."""
    return is_global_admin or role == "admin"


def _old_can_ingest(role, is_global_admin):
    """``projects._can_ingest_into_project`` as it was."""
    return is_global_admin or role in ("admin", "analyst")


def _old_is_member(role, is_global_admin):
    """``get_project`` / ``list_members``' own membership check, as it was."""
    return is_global_admin or role is not None


@pytest.mark.parametrize("role", [None, "viewer", "auditor", "analyst", "admin"])
@pytest.mark.parametrize("is_global_admin", [False, True])
def test_the_project_routes_answer_as_their_old_checks_did(real_auth, db_session, role, is_global_admin):
    project = Project(name="p-access", slug="p-access", status="archived")
    db_session.add(project)
    db_session.flush()
    caller = _user(db_session, 94001, role=UserRole.ADMIN if is_global_admin else UserRole.MEMBER)
    if role:
        db_session.add(ProjectMembership(project_id=project.id, user_id=caller.id, role=role))
    db_session.commit()
    headers = _bearer(_token(db_session, caller))
    base = f"/api/v1/projects/{project.id}"

    def ok(resp):
        assert resp.status_code in (200, 403), resp.text
        return resp.status_code == 200

    assert ok(real_auth.get(base, headers=headers)) == _old_is_member(role, is_global_admin)
    assert ok(real_auth.get(f"{base}/members", headers=headers)) == _old_is_member(role, is_global_admin)
    # The role is asked as before; one who passes is then told the project is
    # archived, which takes no ingest settings (review 2026-10-23b, PLT-8).
    ingest = real_auth.patch(f"{base}/ingest-settings", headers=headers, json={"skip_informational_findings": True})
    assert ingest.status_code == (410 if _old_can_ingest(role, is_global_admin) else 403), ingest.text
    # An archived project stays reachable to the people who manage it.
    assert ok(real_auth.put(base, headers=headers, json={"description": "d"})) == _old_can_manage(role, is_global_admin)
    if ok(real_auth.get(base, headers=headers)):
        expected = "admin" if is_global_admin else role
        assert real_auth.get(base, headers=headers).json()["my_role"] == expected


def test_the_access_helpers_are_one_rule(db_session):
    project = Project(name="p-rule", slug="p-rule")
    db_session.add(project)
    db_session.flush()
    member = _user(db_session, 94011)
    db_session.add(ProjectMembership(project_id=project.id, user_id=member.id, role="auditor"))
    db_session.commit()
    assert deps.membership_role(db_session, project.id, member) == "auditor"
    assert deps.has_project_role(db_session, project.id, member, ProjectRole.AUDITOR)
    assert not deps.has_project_role(db_session, project.id, member, ProjectRole.ANALYST)
    assert not deps.is_project_admin(db_session, project.id, member)
    outsider = _user(db_session, 94012)
    assert deps.membership_role(db_session, project.id, outsider) is None
    assert not deps.has_project_role(db_session, project.id, outsider, ProjectRole.VIEWER)


# ---------------------------------------------------------------------------
# R-DB3 — the change and its audit row are one transaction
# ---------------------------------------------------------------------------

def _audit_fails(monkeypatch, module):
    def refuse(*args, **kwargs):
        raise RuntimeError("the audit row could not be written")
    monkeypatch.setattr(module, "log_audit_event", refuse)


def test_an_account_change_whose_audit_row_fails_is_not_kept(client, db_session, monkeypatch):
    target = _user(db_session, 95001, full_name="Before")
    _audit_fails(monkeypatch, users_endpoints)
    with pytest.raises(RuntimeError):
        client.put(f"/api/v1/users/{target.id}", json={"full_name": "After"})
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(User, target.id).full_name == "Before"


def test_a_deletion_whose_audit_row_fails_is_not_kept(client, db_session, monkeypatch):
    target = _user(db_session, 95002)
    _audit_fails(monkeypatch, users_endpoints)
    with pytest.raises(RuntimeError):
        client.delete(f"/api/v1/users/{target.id}")
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(User, target.id) is not None


def test_a_password_reset_whose_audit_row_fails_is_not_kept(client, db_session, monkeypatch):
    target = _user(db_session, 95003)
    _audit_fails(monkeypatch, users_endpoints)
    with pytest.raises(RuntimeError):
        client.post(f"/api/v1/users/{target.id}/reset-password", json={"new_password": STRONG})
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(User, target.id).hashed_password == HASH


def test_a_two_factor_reset_whose_audit_row_fails_is_not_kept(client, db_session, monkeypatch):
    target = _user(db_session, 95004, totp_enabled=True)
    _audit_fails(monkeypatch, users_endpoints)
    with pytest.raises(RuntimeError):
        client.post(f"/api/v1/users/{target.id}/reset-2fa")
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(User, target.id).totp_enabled is True


def test_an_account_whose_audit_row_fails_is_not_created(client, db_session, monkeypatch):
    _audit_fails(monkeypatch, auth_endpoints)
    with pytest.raises(RuntimeError):
        client.post("/api/v1/auth/register", json={"username": "half-made", "password": STRONG})
    db_session.rollback()
    assert db_session.query(User).filter(User.username == "half-made").first() is None


# ---------------------------------------------------------------------------
# A NUL in a new password
# ---------------------------------------------------------------------------

def test_a_nul_in_a_password_is_one_of_the_strength_rules():
    result = validate_password_strength("A-Strong-Passw0rd!\x00")
    assert not result["valid"]
    assert "Password must not contain a NUL character" in result["errors"]


@pytest.mark.parametrize("call", ["change", "register", "reset"])
def test_a_nul_in_a_new_password_is_refused_not_a_500(client, db_session, call):
    nul = "A-Strong-Passw0rd!\x00x"
    if call == "change":
        resp = client.post("/api/v1/auth/change-password",
                           json={"current_password": TEST_USER_PASSWORD, "new_password": nul})
    elif call == "register":
        resp = client.post("/api/v1/auth/register", json={"username": "nul-user", "password": nul})
    else:
        target = _user(db_session, 96001)
        resp = client.post(f"/api/v1/users/{target.id}/reset-password", json={"new_password": nul})
    assert resp.status_code == 400, resp.text
    assert "NUL" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# A token's session is its own user's
# ---------------------------------------------------------------------------

def test_a_token_naming_another_users_session_is_refused(real_auth, db_session):
    owner = _user(db_session, 97001)
    other = _user(db_session, 97002)
    jti = secrets.token_urlsafe(16)
    create_session(db_session, owner, jti)
    borrowed = create_access_token({"sub": str(other.id), "username": other.username}, jti=jti)
    resp = real_auth.get("/api/v1/auth/profile", headers=_bearer(borrowed))
    assert resp.status_code == 401
    own = create_access_token({"sub": str(owner.id), "username": owner.username}, jti=jti)
    assert real_auth.get("/api/v1/auth/profile", headers=_bearer(own)).status_code == 200


# ---------------------------------------------------------------------------
# Never zero active administrators
# ---------------------------------------------------------------------------

def test_an_actor_who_is_no_longer_an_active_admin_is_refused(db_session):
    former = _user(db_session, 98001, role=UserRole.MEMBER)
    admin = _user(db_session, 98002, role=UserRole.ADMIN)
    with pytest.raises(HTTPException) as refused:
        users_endpoints.keep_an_active_admin(db_session, actor=former, target=admin)
    assert refused.value.status_code == 403


def test_two_admins_demoting_each_other_leave_one(two_sessions):
    """A demotes B while B demotes A: B's request waits on the admin rows'
    lock, then finds its own actor demoted and is refused."""
    _p1, a = two_sessions.project()
    _p2, b = two_sessions.project()

    def promote(db):
        for uid in (a.id, b.id):
            db.get(User, uid).role = UserRole.ADMIN.value
    two_sessions.commit(promote)

    # A's request: under the lock, B is demoted — not yet committed.
    users_endpoints.keep_an_active_admin(two_sessions.a, actor=SimpleNamespace(id=a.id),
                                         target=SimpleNamespace(id=b.id))
    two_sessions.a.get(User, b.id).role = UserRole.MEMBER.value
    two_sessions.a.flush()

    outcome = {}

    def b_demotes_a():
        try:
            users_endpoints.keep_an_active_admin(two_sessions.b, actor=SimpleNamespace(id=b.id),
                                                 target=SimpleNamespace(id=a.id))
            outcome["result"] = "allowed"
        except HTTPException as exc:
            outcome["result"] = exc.status_code
        finally:
            two_sessions.b.rollback()

    racer = threading.Thread(target=b_demotes_a, daemon=True)
    racer.start()
    time.sleep(0.5)
    assert racer.is_alive(), "B's request must wait for A's lock on the admin rows"
    two_sessions.a.commit()
    racer.join(10)
    assert outcome["result"] == 403

    check = two_sessions.fresh()
    try:
        assert check.get(User, a.id).role == UserRole.ADMIN.value
    finally:
        check.close()


def test_the_last_active_admin_cannot_be_deactivated_by_a_former_admin(client, db_session, test_user):
    """Through the route: ``require_role`` read the caller as an admin, but
    the row says otherwise by the time the change is decided."""
    other = _user(db_session, 98011, role=UserRole.ADMIN)
    test_user.role = UserRole.MEMBER.value   # demoted after the request was authenticated
    db_session.commit()
    test_user.role = UserRole.ADMIN.value    # the stale view the override hands the route
    resp = client.put(f"/api/v1/users/{other.id}", json={"is_active": False})
    assert resp.status_code == 403, resp.text
    db_session.expire_all()
    assert db_session.get(User, other.id).is_active is True


# ---------------------------------------------------------------------------
# First boot
# ---------------------------------------------------------------------------

@pytest.fixture
def uploads_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "operator-chosen-secret")
    monkeypatch.delenv("DEFAULT_ADMIN_USERNAME", raising=False)
    return tmp_path


def test_a_deactivated_admin_is_not_an_admin_at_first_boot(db_session, uploads_dir):
    _user(db_session, 99001, role=UserRole.ADMIN, username="old-admin", active=False)
    seed_default_admin()
    created = db_session.query(User).filter(User.username == "admin").first()
    assert created is not None and created.role == UserRole.ADMIN.value and created.is_active


def test_a_taken_default_username_is_said_and_nothing_is_created(db_session, uploads_dir, monkeypatch):
    holder = _user(db_session, 99002, username="admin")
    said = []
    monkeypatch.setattr(startup.logger, "error", lambda msg, *args: said.append(msg % args))
    seed_default_admin()
    assert len(said) == 1
    assert "NO ACTIVE ADMINISTRATOR, AND NONE WAS CREATED" in said[0]
    assert "DEFAULT_ADMIN_USERNAME" in said[0]
    db_session.expire_all()
    kept = db_session.get(User, holder.id)
    assert kept.role == UserRole.MEMBER.value and kept.hashed_password == HASH
    assert db_session.query(User).filter(User.role == UserRole.ADMIN).count() == 0


def test_the_first_boot_file_goes_only_with_its_own_accounts_change(client, test_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    marker = tmp_path / "initial-admin-password.txt"
    assert admin_marker_path() == str(marker)
    marker.write_text("username: admin\npassword: still-the-first-one\n")

    def change(new):
        resp = client.post("/api/v1/auth/change-password",
                           json={"current_password": TEST_USER_PASSWORD, "new_password": new})
        assert resp.status_code == 200, resp.text

    change("Changed-Passw0rd!one")           # test-admin is not the file's account
    assert marker.exists()

    test_user.hashed_password = get_password_hash(TEST_USER_PASSWORD)
    marker.write_text(f"username: {test_user.username}\npassword: still-the-first-one\n")
    change("Changed-Passw0rd!two")
    assert not marker.exists()


# ---------------------------------------------------------------------------
# The role and status vocabularies are the database's
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("build", [
    lambda: User(id=99101, username="bad-role", email="bad@example.com", hashed_password=HASH, role="analyst"),
    lambda: Project(name="bad-status", slug="bad-status", status="in_progress"),
])
def test_the_database_refuses_a_value_outside_the_vocabulary(db_session, build):
    if db_session.bind.dialect.name != "postgresql":
        pytest.skip("the chain-built schema is Postgres")
    db_session.add(build())
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_database_refuses_an_unknown_project_role(db_session, test_project):
    if db_session.bind.dialect.name != "postgresql":
        pytest.skip("the chain-built schema is Postgres")
    member = _user(db_session, 99102)
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=member.id, role="owner"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_users_list_answer_is_the_one_envelope():
    from app.services.paging import PAGE_KEYS
    assert tuple(users_endpoints.UserListPage.model_fields) == PAGE_KEYS

