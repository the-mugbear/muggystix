"""Migration c8f2d5a9e3b1 — ownership comes off ``integration_credentials``.

A populated up / down / up walk on a scratch database BUILT BY THE CHAIN (the
pattern of ``test_migration_one_session_id.py``; the test schema is
``create_all()`` from the models and has never had the old columns).

Seeded at ``a6d3b1e8c5f7``: one user's all-project row, the same user's row
limited to a project, and ANOTHER user's row under the same name — the
duplicate the owner decided to keep ("an admin deletes duplicates; nothing is
merged by guess").

Asserted: the upgrade keeps every row and every secret, drops the ownership
columns and the unique name; deleting the account that configured a row then
keeps the row; the downgrade gives every row an owner again (the first active
global administrator for a row whose creator is gone), returns ``project_id``
as NULL and the constraint as the baseline had it — and succeeds with two rows
of ONE owner under ONE name, without renaming either, because a unique
constraint treats their NULL ``project_id`` as distinct; and the walk repeats.

Postgres only: the scratch database is created on the server the suite uses.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BEFORE = "a6d3b1e8c5f7"
REVISION = "c8f2d5a9e3b1"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


def _alembic(url, *args: str) -> None:
    """Run alembic against the scratch database, in its own process: env.py
    reads the URL from the settings, which this process has already loaded."""
    env = {
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "BLUESTICK_SKIP_DB_INIT": "1",
    }
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
    )
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"


@pytest.fixture(scope="module")
def scratch():
    """A database migrated by the chain to the revision before this one."""
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_c8f2"[:63])
    _drop_database(url)
    _ensure_database(url)
    try:
        _alembic(url, "upgrade", BEFORE)
        eng = create_engine(url)
        try:
            yield url, eng
        finally:
            eng.dispose()
    finally:
        _drop_database(url)


# User 1 is an INACTIVE admin with the lowest id, user 2 a member, user 3 the
# first ACTIVE admin, user 4 the member who configured rows and is deleted
# later: the downgrade must pick 3, not the lowest id and not any admin.
SEED = """
INSERT INTO projects (id, name, slug, status) VALUES (1, 'P', 'p', 'active');
INSERT INTO users (id, username, hashed_password, role, is_active) VALUES
    (1, 'old-admin', 'x', 'admin', false),
    (2, 'member',    'x', 'member', true),
    (3, 'admin',     'x', 'admin', true),
    (4, 'leaver',    'x', 'member', true);

INSERT INTO integration_credentials
    (id, user_id, project_id, name, integration_type, base_url,
     secret_encrypted, secret2_encrypted, extra_config, is_active)
VALUES
    (10, 3, NULL, 'Nessus',  'nessus',      'https://192.0.2.10:8834', 'enc-a', 'enc-b', '{"max_hosts_per_scan": 256}', true),
    (11, 3, 1,    'Project', 'generic_api', NULL,                      'enc-c', NULL,    NULL, true),
    (12, 4, NULL, 'Nessus',  'nessus',      'https://192.0.2.20:8834', 'enc-d', 'enc-e', NULL, false),
    (13, 4, 1,    'Burp',    'burp',        NULL,                      'enc-f', NULL,    NULL, true);
SELECT setval('integration_credentials_id_seq', 13);
"""

#: id → what must survive every step untouched.
KEPT = {
    10: ("Nessus", "nessus", "https://192.0.2.10:8834", "enc-a", "enc-b", '{"max_hosts_per_scan": 256}', True),
    11: ("Project", "generic_api", None, "enc-c", None, None, True),
    12: ("Nessus", "nessus", "https://192.0.2.20:8834", "enc-d", "enc-e", None, False),
    13: ("Burp", "burp", None, "enc-f", None, None, True),
}


def _columns(conn) -> set:
    return {
        r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'integration_credentials'"
        ))
    }


def _constraints(conn) -> dict:
    """``{constraint name: (type, ON DELETE action)}`` on the table."""
    return {
        name: (kind, action)
        for name, kind, action in conn.execute(text(
            "SELECT conname, contype::text, confdeltype::text FROM pg_constraint "
            "WHERE conrelid = 'integration_credentials'::regclass"
        ))
    }


def _indexes(conn) -> set:
    return {
        r[0] for r in conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'integration_credentials'"
        ))
    }


def _kept(conn) -> dict:
    return {
        row[0]: tuple(row[1:])
        for row in conn.execute(text(
            "SELECT id, name, integration_type, base_url, secret_encrypted, "
            "secret2_encrypted, extra_config, is_active FROM integration_credentials"
        ))
    }


def _assert_upgraded(conn) -> None:
    columns = _columns(conn)
    assert "created_by_id" in columns
    assert not {"user_id", "project_id"} & columns
    constraints = _constraints(conn)
    assert "uq_integration_user_project_name" not in constraints
    # ``n`` = SET NULL: deleting the account keeps the integration.
    assert constraints["integration_credentials_created_by_id_fkey"] == ("f", "n")
    assert {name for name, (kind, _) in constraints.items() if kind == "f"} == {
        "integration_credentials_created_by_id_fkey",
    }
    indexes = _indexes(conn)
    assert "ix_integration_credentials_created_by_id" in indexes
    assert not {"ix_integration_credentials_user_id", "ix_integration_credentials_project_id"} & indexes
    assert conn.execute(text(
        "SELECT is_nullable FROM information_schema.columns WHERE "
        "table_name = 'integration_credentials' AND column_name = 'created_by_id'"
    )).scalar() == "YES"


def test_up_down_up_keeps_every_integration_and_gives_each_an_owner_again(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text(SEED))

    # ---- upgrade -----------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        _assert_upgraded(conn)
        # Every row kept — the duplicate name and the project-limited ones too.
        assert _kept(conn) == KEPT
        assert dict(conn.execute(text(
            "SELECT id, created_by_id FROM integration_credentials"
        )).all()) == {10: 3, 11: 3, 12: 4, 13: 4}

    with eng.begin() as conn:
        # Deleting the account that configured two of them keeps both.
        conn.execute(text("DELETE FROM users WHERE id = 4"))
        # A row added after the upgrade, by nobody, under a name already used
        # twice: legal now, and it must not stop the downgrade.
        conn.execute(text(
            "INSERT INTO integration_credentials (id, created_by_id, name, integration_type, is_active) "
            "VALUES (14, NULL, 'Nessus', 'nessus', true)"
        ))
    with eng.connect() as conn:
        assert dict(conn.execute(text(
            "SELECT id, created_by_id FROM integration_credentials"
        )).all()) == {10: 3, 11: 3, 12: None, 13: None, 14: None}

    # ---- downgrade ---------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        columns = _columns(conn)
        assert {"user_id", "project_id"} <= columns and "created_by_id" not in columns
        # Orphans went to the first ACTIVE global admin (3) — not to the
        # inactive admin with the lowest id (1), nor to a member (2).
        owners = dict(conn.execute(text("SELECT id, user_id FROM integration_credentials")).all())
        assert owners == {10: 3, 11: 3, 12: 3, 13: 3, 14: 3}
        # NULL = every project in the old model: what an application-wide row is.
        assert conn.execute(text(
            "SELECT count(*) FROM integration_credentials WHERE project_id IS NOT NULL"
        )).scalar() == 0
        # Three rows of ONE owner under ONE name, and no name was changed.
        assert {**KEPT, 14: ("Nessus", "nessus", None, None, None, None, True)} == _kept(conn)
        constraints = _constraints(conn)
        assert constraints["uq_integration_user_project_name"][0] == "u"
        # ``c`` = CASCADE, as the baseline had both.
        assert constraints["integration_credentials_user_id_fkey"] == ("f", "c")
        assert constraints["integration_credentials_project_id_fkey"] == ("f", "c")
        assert conn.execute(text(
            "SELECT is_nullable FROM information_schema.columns WHERE "
            "table_name = 'integration_credentials' AND column_name = 'user_id'"
        )).scalar() == "NO"
        assert {
            "ix_integration_credentials_user_id", "ix_integration_credentials_project_id",
        } <= _indexes(conn)

    # The restored constraint is the old model's: it still refuses a second
    # row of one user under one name IN ONE PROJECT.
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO integration_credentials (id, user_id, project_id, name, integration_type, is_active) "
            "VALUES (15, 2, 1, 'Scoped', 'burp', true)"
        ))
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO integration_credentials (id, user_id, project_id, name, integration_type, is_active) "
                "VALUES (16, 2, 1, 'Scoped', 'burp', true)"
            ))

    # ---- and up again ------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        _assert_upgraded(conn)
        assert set(_kept(conn)) == {10, 11, 12, 13, 14, 15}
        assert dict(conn.execute(text(
            "SELECT id, created_by_id FROM integration_credentials"
        )).all()) == {10: 3, 11: 3, 12: 3, 13: 3, 14: 3, 15: 2}


def test_the_downgrade_refuses_when_there_is_nobody_to_own_an_orphan(scratch):
    """Rows whose creator is gone and no user at all: the downgrade stops with
    a message and changes nothing (one transaction), instead of failing on
    NOT NULL or deleting a scanner."""
    url, eng = scratch
    # The first test leaves the database at REVISION.
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM users"))
    with eng.connect() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM integration_credentials WHERE created_by_id IS NOT NULL"
        )).scalar() == 0
        before = _kept(conn)

    env = {
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "BLUESTICK_SKIP_DB_INIT": "1",
    }
    done = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", BEFORE],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
    )
    assert done.returncode != 0
    assert "no user to give them to" in done.stdout + done.stderr
    with eng.connect() as conn:
        _assert_upgraded(conn)
        assert _kept(conn) == before

    # With the integrations removed there is nothing to re-home, and it runs.
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM integration_credentials"))
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert "user_id" in _columns(conn)
