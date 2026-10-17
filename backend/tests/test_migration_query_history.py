"""Migration a7d3c9e1f5b2 — one query history for the Hosts and Query pages.

A populated walk on a scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_hostname_changed.py``; the suite's own schema is
``create_all()`` from the models).

Seeded at ``f6a2c8e4b0d3``: two people's Hosts histories in one project — one
query run three times with another between (the old page collapsed only
CONSECUTIVE repeats, so it is three rows), one run once.

Asserted:

* the upgrade renames the table, its sequence and indexes; every row becomes
  ``kind`` 'hosts', not an agent's; a repeated query is ONE row (its newest
  id) with ``run_count`` = the rows it had, ``created_at`` the first run and
  ``last_run_at`` the latest; other people's rows are untouched;
* the unique index refuses a second row of one query, and the sequence still
  numbers new rows;
* the downgrade deletes Query-page and agent rows, keeps the Hosts rows (merged),
  and gives back ``host_query_history`` with ``created_at`` = the last run;
* up again works on what the downgrade left.

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
BEFORE = "f6a2c8e4b0d3"
REVISION = "a7d3c9e1f5b2"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


def _alembic(url, *args: str) -> None:
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
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_a7d3"[:63])
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
INSERT INTO projects (id, name, slug, status) VALUES (1, 'P', 'p', 'active');
INSERT INTO users (id, username, hashed_password, role, is_active) VALUES
    (1, 'alice', 'x', 'member', true), (2, 'bob', 'x', 'member', true);
INSERT INTO host_query_history (id, user_id, project_id, q, result_count, created_at) VALUES
    (1, 1, 1, 'port:445', 3, '2026-10-01T10:00:00Z'),
    (2, 1, 1, 'os:linux', 9, '2026-10-01T11:00:00Z'),
    (3, 1, 1, 'port:445', 4, '2026-10-01T12:00:00Z'),
    (4, 1, 1, 'port:445', 5, '2026-10-01T13:00:00Z'),
    (5, 2, 1, 'port:445', 1, '2026-10-01T09:00:00Z');
SELECT setval('host_query_history_id_seq', 5);
"""


def _tables(conn) -> set:
    return {r[0] for r in conn.execute(text(
        "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() AND tablename LIKE '%query_history'"))}


def _indexes(conn, table) -> set:
    return {r[0] for r in conn.execute(text(
        "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() AND tablename = :t"), {"t": table})}


def test_the_hosts_history_becomes_the_one_history_and_comes_back(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text(SEED))

    # ---- upgrade -------------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert _tables(conn) == {"query_history"}
        assert _indexes(conn, "query_history") == {
            "query_history_pkey", "ix_query_history_id", "ix_query_history_user_id", "ix_query_history_project_id",
            "ix_query_history_user_project_kind_last", "uq_query_history_entry",
        }
        rows = conn.execute(text(
            "SELECT id, user_id, q, kind, by_agent, run_count, result_count, error, "
            "to_char(created_at AT TIME ZONE 'UTC', 'HH24:MI'), to_char(last_run_at AT TIME ZONE 'UTC', 'HH24:MI') "
            "FROM query_history ORDER BY id")).all()
        assert [tuple(r) for r in rows] == [
            (2, 1, "os:linux", "hosts", False, 1, 9, None, "11:00", "11:00"),
            # Three rows of one query: its newest row, counted, first and last run.
            (4, 1, "port:445", "hosts", False, 3, 5, None, "10:00", "13:00"),
            # Another person's is their own.
            (5, 2, "port:445", "hosts", False, 1, 1, None, "09:00", "09:00"),
        ]

    with eng.begin() as conn:
        # The sequence still numbers new rows; a Query-page and an agent row.
        conn.execute(text("INSERT INTO query_history (user_id, project_id, kind, q, by_agent) VALUES "
                          "(1, 1, 'query', 'hosts | head 5', false), (1, 1, 'query', 'hosts | head 5', true)"))
    with eng.connect() as conn:
        assert conn.execute(text("SELECT max(id) FROM query_history")).scalar() == 7
        with pytest.raises(IntegrityError):
            conn.execute(text("INSERT INTO query_history (user_id, project_id, kind, q) VALUES (1, 1, 'hosts', 'port:445')"))
        conn.rollback()

    # ---- downgrade -----------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert _tables(conn) == {"host_query_history"}
        assert "ix_host_query_history_user_project_created" in _indexes(conn, "host_query_history")
        rows = conn.execute(text(
            "SELECT id, user_id, q, result_count, to_char(created_at AT TIME ZONE 'UTC', 'HH24:MI') "
            "FROM host_query_history ORDER BY id")).all()
        # The Query-page and agent rows are gone; the merged Hosts row stays one,
        # dated by its last run (what the old page sorted by).
        assert [tuple(r) for r in rows] == [
            (2, 1, "os:linux", 9, "11:00"), (4, 1, "port:445", 5, "13:00"), (5, 2, "port:445", 1, "09:00"),
        ]

    # ---- and up again --------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*), sum(run_count) FROM query_history")).one() == (3, 3)
