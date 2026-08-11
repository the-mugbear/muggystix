"""Three small "the system claims something it doesn't do" fixes (v2.240.4).

  * A3 — a pre-Alembic database was adopted with ``stamp head``, marking it as
    containing every migration in the chain when its schema equals only the
    BASELINE. The site booted fine and broke later, at the first query
    touching anything added since.
  * Assist sessions could be ended by any project analyst, not just their
    owner — revoking a colleague's key mid-conversation.
  * ``api_keys.allowed_ips`` advertised an IP whitelist that nothing enforced.
"""

import pytest

from app.db.init import (
    _BASELINE_REVISION,
    _BASELINE_REQUIRED_TABLES,
    _POST_BASELINE_TABLES,
    _plan_for_tables,
)


# ---------------------------------------------------------------------------
# A3 — adoption of a pre-Alembic database
# ---------------------------------------------------------------------------

def test_baseline_revision_constant_is_really_the_root_of_the_chain():
    """Pins the constant the adoption path stamps.

    If someone squashes migrations or renames the baseline, stamping a stale
    revision id would fail loudly — but silently stamping the WRONG one is the
    failure mode that hurts, so assert this is the revision with no parent.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    script = ScriptDirectory.from_config(cfg)

    roots = [r.revision for r in script.walk_revisions() if r.down_revision is None]
    assert roots == [_BASELINE_REVISION], (
        f"the chain's root is {roots}, but the adoption path stamps "
        f"{_BASELINE_REVISION}"
    )


def test_an_alembic_managed_database_just_upgrades():
    assert _plan_for_tables({"alembic_version", "users", "hosts_v2"}) == "upgrade"


def test_an_empty_database_is_built_from_scratch():
    assert _plan_for_tables(set()) == "upgrade"


def test_a_pre_alembic_database_is_adopted_at_the_baseline_not_at_head():
    """The A3 bug.

    Adopting means stamping the baseline and then migrating forward. Stamping
    head would claim the post-baseline migrations had already run.
    """
    assert _plan_for_tables(set(_BASELINE_REQUIRED_TABLES)) == "adopt"


def test_a_partially_migrated_database_fails_closed():
    """No version table, but post-baseline tables present.

    That is not a pre-Alembic install — it's a partial restore or a schema
    whose version table was dropped. Adopting it at the baseline would re-run
    migrations against objects that already exist, so refuse.
    """
    tables = set(_BASELINE_REQUIRED_TABLES) | {sorted(_POST_BASELINE_TABLES)[0]}
    assert _plan_for_tables(tables) == "fail"


def test_an_unrecognisable_database_is_not_adopted():
    """Something else entirely — don't stamp a schema we don't understand."""
    assert _plan_for_tables({"wordpress_posts", "wp_users"}) == "upgrade"


# ---------------------------------------------------------------------------
# allowed_ips — the security control that wasn't
# ---------------------------------------------------------------------------

def test_api_keys_no_longer_advertise_an_unenforced_ip_whitelist():
    from app.db.models_auth import APIKey

    assert "allowed_ips" not in APIKey.__table__.c, (
        "a column named allowed_ips implies key access is IP-pinned; if it "
        "comes back it needs an enforcement point in the auth dependency"
    )


# ---------------------------------------------------------------------------
# Assist session ownership
# ---------------------------------------------------------------------------

def _make_user(db, username, uid, role="member"):
    from app.db.models_auth import User, UserRole

    # Explicit id: the shared `test_user` fixture inserts id=1 directly, which
    # leaves the sequence at 1, so an id-less insert collides on users_pkey.
    user = User(
        id=uid,
        username=username, email=f"{username}@example.com",
        hashed_password="x", is_active=True,
        role=UserRole.ADMIN if role == "global_admin" else UserRole.MEMBER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _member(db, user, project, role):
    from app.db.models_project import ProjectMembership

    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
    db.commit()


def _start_session(client, project_id):
    r = client.post(f"/api/v1/projects/{project_id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return r.json()["assist_session_id"]


def test_an_operator_can_end_their_own_session(client, test_project):
    sid = _start_session(client, test_project.id)
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/sessions/{sid}/end")
    assert r.status_code == 204, r.text


def test_a_peer_analyst_cannot_end_someone_elses_session(
    client, db_session, test_project, test_user,
):
    """The defect: any project analyst could revoke a colleague's key.

    Ending a session hands the colleague's still-running agent 401s
    mid-conversation, and peers gain nothing from being able to do it — an
    assist agent's writes already carry its operator's name.
    """
    sid = _start_session(client, test_project.id)

    peer = _make_user(db_session, "peer-analyst", uid=9001)
    _member(db_session, peer, test_project, "analyst")

    from app.api.v1.endpoints.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: peer
    try:
        r = client.post(
            f"/api/v1/projects/{test_project.id}/assist/sessions/{sid}/end"
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 403, r.text
    assert "another operator" in r.json()["detail"]


def test_a_project_admin_can_end_an_abandoned_session(
    client, db_session, test_project,
):
    """Cleanup has to remain possible — a laptop closed mid-session shouldn't
    leave a live key nobody can revoke."""
    sid = _start_session(client, test_project.id)

    admin = _make_user(db_session, "project-admin", uid=9002)
    _member(db_session, admin, test_project, "admin")

    from app.api.v1.endpoints.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: admin
    try:
        r = client.post(
            f"/api/v1/projects/{test_project.id}/assist/sessions/{sid}/end"
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 204, r.text
