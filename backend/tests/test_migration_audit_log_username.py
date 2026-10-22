"""Migration e7b3d9f1a5c2 — ``audit_logs.username`` (backlog 2026-10-22, R10).

A populated walk on a scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_hostname_changed.py``).  Seeded at the revision before: rows
whose ``details`` hold a username, hold none, hold a non-string, are a JSON
array, or are NULL.

Asserted: the upgrade adds the column and its index and fills it from each
object's string ``username`` only; the downgrade drops both and keeps every
row and its ``details``; the upgrade again fills it the same way.
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
BEFORE = "d5a1f3c7e9b2"
REVISION = "e7b3d9f1a5c2"

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
    url = base.set(database=f"{base.database}_e7b3"[:63])
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
INSERT INTO audit_logs (id, action, details, success) VALUES
  (1, 'login_failed',  '{"username": "alice"}', false),
  (2, 'login_failed',  '{"username": "bob", "reason": "lockout"}', false),
  (3, 'project_created', '{"name": "p"}', true),
  (4, 'odd',           '{"username": 7}', true),
  (5, 'odd',           '["username"]', true),
  (6, 'odd',           NULL, true)
"""

EXPECTED = {1: "alice", 2: "bob", 3: None, 4: None, 5: None, 6: None}


def _usernames(eng):
    with eng.connect() as conn:
        return dict(conn.execute(text("SELECT id, username FROM audit_logs ORDER BY id")).all())


def _columns(eng):
    with eng.connect() as conn:
        return {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_logs'"
        ))}


def _indexes(eng):
    with eng.connect() as conn:
        return {r[0] for r in conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'audit_logs'"
        ))}


def test_the_column_is_filled_from_details_and_comes_and_goes_with_the_revision(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text(SEED))
    assert "username" not in _columns(eng)

    _alembic(url, "upgrade", REVISION)
    assert _usernames(eng) == EXPECTED
    assert "ix_audit_logs_username_timestamp" in _indexes(eng)

    _alembic(url, "downgrade", BEFORE)
    assert "username" not in _columns(eng)
    assert "ix_audit_logs_username_timestamp" not in _indexes(eng)
    with eng.connect() as conn:
        kept = dict(conn.execute(text("SELECT id, details::text FROM audit_logs ORDER BY id")).all())
    assert set(kept) == set(EXPECTED) and '"alice"' in kept[1]

    _alembic(url, "upgrade", REVISION)
    assert _usernames(eng) == EXPECTED
