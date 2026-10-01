"""Migration b8e2a5c7d1f3 — ``assist_sessions`` folded into ``agent_sessions``.

A populated up / down / up walk on a scratch database BUILT BY THE CHAIN (never
the test schema, which is ``create_all()`` from the models and has no
``assist_sessions`` table to migrate).  It seeds the three shapes a pointer row
can have at ``a7d1f4b6c9e2``:

* the pointer of a consolidated ``project`` session (written beside its
  ``AgentSession`` by every start since v2.337.0);
* the pointer of a legacy ``assist`` session, where the pointer row carried the
  lifecycle and the base row was a backfilled shell;
* a pointer with NO base session (``agent_session_id IS NULL``) — the schema
  allows it, so the migration must not lose its calls.

and asserts that no call and no feedback row loses its session in either
direction, that old ids still find their session, and that the walk can be
repeated.

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

from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BEFORE = "a7d1f4b6c9e2"
REVISION = "b8e2a5c7d1f3"

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
    url = base.set(database=f"{base.database}_b8e2"[:63])
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


# Ids are explicit and the two sequences deliberately disagree (pointer 11 is
# session 101), as they did in production: a migration that confused the two
# would still pass with ids that happen to match.
SEED = """
INSERT INTO projects (id, name, slug, status) VALUES (1, 'P', 'p', 'active');
INSERT INTO users (id, username, hashed_password, role) VALUES (1, 'op', 'x', 'member');
INSERT INTO agents (id, name, project_id, owner_id, is_active) VALUES (1, 'a', 1, 1, true);

-- 101: a consolidated project session.  102: a legacy assist session whose
-- base row is a shell (still 'active', nothing copied onto it).
INSERT INTO agent_sessions (id, workflow, project_id, agent_id, started_by_id, status, started_at, purpose)
VALUES (101, 'project', 1, 1, 1, 'active', '2026-09-01T10:00:00Z', 'project purpose'),
       (102, 'assist',  1, 1, 1, 'active', '2026-03-01T10:00:00Z', NULL);
SELECT setval('agent_sessions_id_seq', 200);

INSERT INTO assist_sessions
    (id, project_id, agent_id, started_by_id, status, purpose, started_at, ended_at,
     last_activity_at, generated_by_model, generated_by_tool, prompt_version, agent_session_id)
VALUES
    (11, 1, 1, 1, 'active', 'project purpose', '2026-09-01T10:00:00Z', NULL, NULL, NULL, NULL, NULL, 101),
    (12, 1, 1, 1, 'ended', 'legacy purpose', '2026-03-01T10:00:00Z', '2026-03-01T14:00:00Z',
     '2026-03-01T13:30:00Z', 'model-x', 'client-y', '1.2.0', 102),
    (13, 1, 1, 1, 'expired', 'orphan purpose', '2026-01-05T09:00:00Z', '2026-01-05T13:00:00Z',
     '2026-01-05T12:00:00Z', 'model-old', NULL, '1.0.0', NULL);
SELECT setval('assist_sessions_id_seq', 13);

INSERT INTO agent_api_calls
    (id, agent_id, project_id, method, path, status_code, duration_ms, agent_session_id, assist_session_id, error_class)
VALUES
    (1, 1, 1, 'GET', '/a', 200, 1, 101, NULL, NULL),   -- project session: session id only
    (2, 1, 1, 'GET', '/a', 200, 1, 101, NULL, NULL),
    (3, 1, 1, 'GET', '/a', 200, 1, 102, 12,   NULL),   -- legacy: both
    (4, 1, 1, 'GET', '/a', 200, 1, NULL, 12,  NULL),   -- legacy: pointer only
    (5, 1, 1, 'GET', '/a', 200, 1, NULL, 13,  NULL),   -- orphan pointer only
    (6, 1, 1, 'GET', '/a', 200, 1, NULL, 13,  NULL),
    (7, NULL, NULL, 'GET', '/a', 500, 1, NULL, NULL, 'Boom');  -- pre-auth crash: no session at all

INSERT INTO agent_feedback (id, project_id, agent_id, source, status, agent_session_id, assist_session_id)
VALUES (1, 1, 1, 'assist', 'new', 101, 11),
       (2, 1, 1, 'assist', 'new', NULL, 12),
       (3, 1, 1, 'assist', 'new', NULL, 13);
"""

#: call id / feedback id → the pointer id of the session it must belong to
#: after the upgrade (None = no session, before and after).
CALL_POINTER = {1: 11, 2: 11, 3: 12, 4: 12, 5: 13, 6: 13, 7: None}
FEEDBACK_POINTER = {1: 11, 2: 12, 3: 13}


def _columns(conn, table: str) -> set:
    return {
        r[0] for r in conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        )
    }


def _table_exists(conn, table: str) -> bool:
    return conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar() is not None


def _assert_upgraded(conn, *, pointer_ids, later_calls=None) -> dict:
    """The post-upgrade contract; returns ``{pointer id: session id}``.
    ``later_calls`` are calls written after the first upgrade, by session id."""
    assert not _table_exists(conn, "assist_sessions")
    assert "assist_session_id" not in _columns(conn, "agent_api_calls")
    assert "assist_session_id" not in _columns(conn, "agent_feedback")

    by_pointer = dict(conn.execute(text(
        "SELECT legacy_assist_session_id, id FROM agent_sessions "
        "WHERE legacy_assist_session_id IS NOT NULL"
    )).all())
    assert set(by_pointer) == set(pointer_ids)
    assert by_pointer[11] == 101 and by_pointer[12] == 102

    calls = dict(conn.execute(text("SELECT id, agent_session_id FROM agent_api_calls")).all())
    assert calls == {
        **{cid: by_pointer.get(p) for cid, p in CALL_POINTER.items()},
        **(later_calls or {}),
    }
    feedback = dict(conn.execute(text("SELECT id, agent_session_id FROM agent_feedback")).all())
    assert feedback == {fid: by_pointer[p] for fid, p in FEEDBACK_POINTER.items()}
    return by_pointer


def test_up_down_up_keeps_every_call_and_every_feedback_row_on_its_session(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text(SEED))
    with eng.connect() as conn:
        sessions_before = conn.execute(text("SELECT count(*) FROM agent_sessions")).scalar()

    # ---- upgrade -----------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        by_pointer = _assert_upgraded(conn, pointer_ids={11, 12, 13})
        # One session was created: the orphan pointer's.
        assert conn.execute(text("SELECT count(*) FROM agent_sessions")).scalar() == sessions_before + 1
        orphan = conn.execute(text(
            "SELECT workflow, project_id, agent_id, started_by_id, status, purpose, "
            "generated_by_model, prompt_version, started_at = '2026-01-05T09:00:00Z', "
            "completed_at = '2026-01-05T13:00:00Z', last_activity_at = '2026-01-05T12:00:00Z' "
            "FROM agent_sessions WHERE id = :i"
        ), {"i": by_pointer[13]}).one()
        assert tuple(orphan) == (
            "assist", 1, 1, 1, "expired", "orphan purpose", "model-old", "1.0.0", True, True, True,
        )
        # The legacy shell took what only its pointer row carried — and its end.
        legacy = conn.execute(text(
            "SELECT status, purpose, generated_by_model, generated_by_tool, prompt_version, "
            "completed_at = '2026-03-01T14:00:00Z', last_activity_at = '2026-03-01T13:30:00Z' "
            "FROM agent_sessions WHERE id = 102"
        )).one()
        assert tuple(legacy) == ("ended", "legacy purpose", "model-x", "client-y", "1.2.0", True, True)
        # The project session is what its key checks; its row is left alone.
        assert conn.execute(text(
            "SELECT status, purpose, completed_at FROM agent_sessions WHERE id = 101"
        )).one() == ("active", "project purpose", None)
        assert conn.execute(text("SELECT count(*) FROM agent_api_calls")).scalar() == len(CALL_POINTER)
        assert conn.execute(text("SELECT count(*) FROM agent_feedback")).scalar() == len(FEEDBACK_POINTER)

    # A session started AFTER the upgrade has no pointer id.
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO agent_sessions (id, workflow, project_id, agent_id, started_by_id, status) "
            "VALUES (300, 'project', 1, 1, 1, 'active')"
        ))
        conn.execute(text(
            "INSERT INTO agent_api_calls (id, agent_id, project_id, method, path, status_code, "
            "duration_ms, agent_session_id) VALUES (8, 1, 1, 'GET', '/a', 200, 1, 300)"
        ))

    # ---- downgrade ---------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert "legacy_assist_session_id" not in _columns(conn, "agent_sessions")
        pointers = dict(conn.execute(text("SELECT id, agent_session_id FROM assist_sessions")).all())
        # The old ids are back on the same sessions; the new session got a new one.
        assert {11: 101, 12: 102, 13: by_pointer[13]}.items() <= pointers.items()
        new_pointer = next(pid for pid, sid in pointers.items() if sid == 300)
        assert new_pointer > 13 and len(pointers) == 4
        # Nothing lost its session on the way down either.
        calls = dict(conn.execute(text("SELECT id, agent_session_id FROM agent_api_calls")).all())
        assert calls == {**{c: by_pointer.get(p) for c, p in CALL_POINTER.items()}, 8: 300}
        # The pointer column is refilled for legacy assist sessions only.
        stamped = dict(conn.execute(text(
            "SELECT id, assist_session_id FROM agent_api_calls WHERE assist_session_id IS NOT NULL"
        )).all())
        assert stamped == {3: 12, 4: 12, 5: 13, 6: 13}
        assert dict(conn.execute(text(
            "SELECT id, assist_session_id FROM agent_feedback WHERE assist_session_id IS NOT NULL"
        )).all()) == {2: 12, 3: 13}
        # A row written by the previous release gets the next id, not a used one.
        assert conn.execute(text("SELECT nextval('assist_sessions_id_seq')")).scalar() > new_pointer

    # ---- and up again ------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        again = _assert_upgraded(
            conn, pointer_ids={11, 12, 13, new_pointer}, later_calls={8: 300},
        )
        assert again[13] == by_pointer[13] and again[new_pointer] == 300
        # No second session for the orphan: it has had its base row since the
        # first upgrade.
        assert conn.execute(text("SELECT count(*) FROM agent_sessions")).scalar() == sessions_before + 2
        assert conn.execute(text("SELECT count(*) FROM agent_api_calls")).scalar() == len(CALL_POINTER) + 1
