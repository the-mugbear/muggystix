"""Migration 9c4e2b7d1a3f — ``users.is_active`` / ``api_keys.is_active`` NOT NULL.

Review 2026-10-23b, PLT-8: every reader took a NULL for false, so a row
written without the flag was silently a disabled account or a dead key.

Up / down / up on a scratch database BUILT BY THE CHAIN: a NULL becomes false
(what it already meant), the columns are NOT NULL with ``true`` as the server
default; the downgrade gives back the nullable columns with no default and
keeps every row.  Postgres only: the scratch database is created on the server
the suite uses.
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
BEFORE = "7b83a791a783"
REVISION = "9c4e2b7d1a3f"


def _alembic(url, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
    )
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"


def _columns(eng) -> list:
    with eng.connect() as conn:
        return [tuple(r) for r in conn.execute(text(
            "SELECT table_name, is_nullable, column_default FROM information_schema.columns "
            "WHERE column_name = 'is_active' AND table_name IN ('users', 'api_keys') ORDER BY table_name"
        ))]


def _flags(eng) -> dict:
    with eng.connect() as conn:
        return dict(conn.execute(text("SELECT username, is_active FROM users WHERE username LIKE 'flag-%'")).all())


@pytest.mark.skipif(not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on")
def test_up_down_up_on_a_chain_built_database():
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_9c4e"[:63])
    _drop_database(url)
    _ensure_database(url)
    eng = create_engine(url)
    try:
        _alembic(url, "upgrade", BEFORE)
        before = _columns(eng)
        assert before == [("api_keys", "YES", None), ("users", "YES", None)]
        with eng.begin() as conn:
            for name, flag in (("flag-null", None), ("flag-on", True), ("flag-off", False)):
                conn.execute(text(
                    "INSERT INTO users (username, hashed_password, role, is_active) "
                    "VALUES (:u, 'x', 'member', :a)"
                ), {"u": name, "a": flag})

        _alembic(url, "upgrade", REVISION)
        assert _columns(eng) == [("api_keys", "NO", "true"), ("users", "NO", "true")]
        assert _flags(eng) == {"flag-null": False, "flag-on": True, "flag-off": False}
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO users (username, hashed_password, role) VALUES ('flag-raw', 'x', 'member')"))
        assert _flags(eng)["flag-raw"] is True

        _alembic(url, "downgrade", BEFORE)
        assert _columns(eng) == before
        assert _flags(eng) == {"flag-null": False, "flag-on": True, "flag-off": False, "flag-raw": True}

        _alembic(url, "upgrade", REVISION)
        assert _columns(eng) == [("api_keys", "NO", "true"), ("users", "NO", "true")]
    finally:
        eng.dispose()
        _drop_database(url)
