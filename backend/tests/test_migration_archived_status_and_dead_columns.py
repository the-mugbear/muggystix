"""Migration c2a7e5f1d9b3 — a project is archived by its status alone, and five
columns nothing needs are dropped.

A populated walk on a scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_one_scope_per_project.py``; the test schema is
``create_all()`` from the models and has none of the dropped columns).

Seeded at ``e1b4c7d0a2f5``: projects whose ``is_archived`` flag and status
agree, and the two ways they can disagree (plus a NULL flag); a user with
``is_verified``; a report job with ``truncated``; catalogue tools with
``phases``; sessions with and without a ``legacy_assist_session_id``, stored
under every ``workflow`` value; a host review stored as the retired
``WATCHING``.

Asserted, in order:

* the upgrade drops the five columns and the index, and every ROW stays;
* where flag and status disagreed the FLAG decided: a closed project is
  ``archived``, an open one labelled archived is ``completed``; rows that
  agreed are unchanged;
* ``agent_sessions.workflow`` keeps every stored value, and the ``WATCHING``
  review row is still there — neither is this revision's to change — and
  ``host_follows.status`` has no database default (the retired default was
  the model's);
* the downgrade restores each column with its old definition:
  ``is_archived`` from the status, ``truncated`` false, the other three
  empty; a status the upgrade changed is not changed back;
* and the walk repeats.

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
BEFORE = "e1b4c7d0a2f5"
REVISION = "c2a7e5f1d9b3"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)

#: table -> the column this revision drops from it.
DROPPED = {
    "projects": "is_archived",
    "agent_sessions": "legacy_assist_session_id",
    "tool_registry": "phases",
    "report_jobs": "truncated",
    "users": "is_verified",
}


def _run_alembic(url, *args: str) -> subprocess.CompletedProcess:
    """Run alembic against the scratch database, in its own process: env.py
    reads the URL from the settings, which this process has already loaded."""
    env = {
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
    )


def _alembic(url, *args: str) -> None:
    done = _run_alembic(url, *args)
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"


@pytest.fixture(scope="module")
def scratch():
    """A database migrated by the chain to the revision before this one."""
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_c2a7"[:63])
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


SEED = """
INSERT INTO projects (id, name, slug, status, is_archived) VALUES
    (1, 'Open',            'open',     'active',    false),
    (2, 'Closed',          'closed',   'archived',  true),
    (3, 'Flag only',       'flag',     'active',    true),
    (4, 'Label only',      'label',    'archived',  false),
    (5, 'Label, no flag',  'nullflag', 'archived',  NULL),
    (6, 'Done',            'done',     'completed', NULL);
INSERT INTO users (id, username, hashed_password, role, is_active, is_verified) VALUES
    (1, 'verified', 'x', 'member', true, true), (2, 'unverified', 'x', 'member', true, false);
INSERT INTO report_jobs (id, project_id, format, report_type, status, truncated) VALUES
    (1, 1, 'json', 'inventory', 'completed', false);
INSERT INTO tool_registry (id, name, description, category, phases) VALUES
    (9001, 'c2a7-with-phases', 'd', 'c', '["discovery", "web"]'),
    (9002, 'c2a7-no-phases', 'd', 'c', '[]');
INSERT INTO agent_sessions (id, workflow, project_id, status, legacy_assist_session_id) VALUES
    (72, 'project',         1, 'ended',  52),
    (73, 'project',         1, 'active', NULL),
    (31, 'assist',          1, 'ended',  31),
    (12, 'execution',       1, 'ended',  NULL),
    (11, 'plan_generation', 1, 'ended',  NULL),
    (10, 'recon',           1, 'ended',  NULL);
INSERT INTO hosts_v2 (id, ip_address, project_id) VALUES (1, '10.0.0.1', 1);
INSERT INTO host_follows (id, host_id, user_id, status) VALUES (1, 1, 1, 'WATCHING');
"""

#: What ``projects.status`` reads after the upgrade — and after the downgrade.
STATUS_AFTER = {
    1: "active", 2: "archived", 3: "archived", 4: "completed", 5: "completed", 6: "completed",
}
WORKFLOWS = {
    72: "project", 73: "project", 31: "assist",
    12: "execution", 11: "plan_generation", 10: "recon",
}
COUNTS = {
    "projects": 6, "users": 2, "report_jobs": 1, "agent_sessions": 6, "host_follows": 1,
}


def _columns(conn, table: str) -> set:
    return {
        r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
        ), {"t": table})
    }


def _revision(conn) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _rows_kept(conn) -> None:
    for table, expected in COUNTS.items():
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == expected, table
    assert conn.execute(text(
        "SELECT count(*) FROM tool_registry WHERE name LIKE 'c2a7-%'"
    )).scalar() == 2
    # Not this revision's to change: the stored kinds and the retired review state.
    assert dict(conn.execute(text("SELECT id, workflow FROM agent_sessions")).all()) == WORKFLOWS
    assert conn.execute(text("SELECT status::text FROM host_follows WHERE id = 1")).scalar() == "WATCHING"
    assert conn.execute(text(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name = 'host_follows' AND column_name = 'status'"
    )).scalar() is None


def _index_exists(conn) -> bool:
    return bool(conn.execute(text(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_agent_session_legacy_assist'"
    )).scalar())


def _assert_upgraded(conn) -> None:
    assert _revision(conn) == REVISION
    for table, column in DROPPED.items():
        assert column not in _columns(conn, table), (table, column)
    assert not _index_exists(conn)
    _rows_kept(conn)
    assert dict(conn.execute(text("SELECT id, status FROM projects")).all()) == STATUS_AFTER


def test_the_flag_decides_the_status_then_the_columns_go_and_come_back(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text(SEED))

    # ---- upgrade -------------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        _assert_upgraded(conn)

    # A review row with no status is refused by the column itself.
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO host_follows (host_id, user_id) VALUES (1, 2)"))

    # ---- downgrade -----------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert _revision(conn) == BEFORE
        for table, column in DROPPED.items():
            assert column in _columns(conn, table), (table, column)
        assert _index_exists(conn)
        _rows_kept(conn)
        # The flag is the status's; a status the upgrade changed stays changed.
        assert dict(conn.execute(text("SELECT id, status FROM projects")).all()) == STATUS_AFTER
        assert dict(conn.execute(text("SELECT id, is_archived FROM projects")).all()) == {
            1: False, 2: True, 3: True, 4: False, 5: False, 6: False,
        }
        # ``truncated`` as it was defined, holding what every row held.
        assert conn.execute(text("SELECT truncated FROM report_jobs WHERE id = 1")).scalar() is False
        assert conn.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'report_jobs' AND column_name = 'truncated'"
        )).scalar() == "NO"
        # The other three come back empty: their values are not recoverable.
        assert conn.execute(text(
            "SELECT count(*) FROM agent_sessions WHERE legacy_assist_session_id IS NOT NULL"
        )).scalar() == 0
        assert conn.execute(text(
            "SELECT count(*) FROM tool_registry WHERE phases IS NOT NULL"
        )).scalar() == 0
        assert conn.execute(text(
            "SELECT count(*) FROM users WHERE is_verified IS NOT NULL"
        )).scalar() == 0
    # The restored index is the unique one the old schema had.
    with eng.begin() as conn:
        conn.execute(text("UPDATE agent_sessions SET legacy_assist_session_id = 52 WHERE id = 72"))
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(text("UPDATE agent_sessions SET legacy_assist_session_id = 52 WHERE id = 73"))
    # A disagreement made on the old schema again.
    with eng.begin() as conn:
        conn.execute(text("UPDATE projects SET is_archived = true WHERE id = 1"))

    # ---- and up again ---------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert _revision(conn) == REVISION
        for table, column in DROPPED.items():
            assert column not in _columns(conn, table), (table, column)
        _rows_kept(conn)
        assert dict(conn.execute(text("SELECT id, status FROM projects")).all()) == {
            **STATUS_AFTER, 1: "archived",
        }
