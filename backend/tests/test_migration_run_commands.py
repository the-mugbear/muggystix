"""Migration f5c2a0d7b4e6 — the call log forgets test-plan entries; the tool
registry carries run commands.

A populated up / down / up walk on a scratch database BUILT BY THE CHAIN (the
test schema is ``create_all()`` from the models: it never had
``referenced_entry_ids`` and always has ``run_command``, so it can show
neither direction).  Seeded at ``e4b1f9c6a3d5``:

* a call-log row that named two test-plan entries;
* registry rows as an existing deployment holds them — a tool the revision has
  a command for, one it has none for, and an agent's suggestion.

Seeding is additive and never overwrites, so the UPGRADE is the only thing that
gives an existing deployment's rows their commands.

Postgres only: the scratch database is created on the server the suite uses.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BEFORE = "e4b1f9c6a3d5"
REVISION = "f5c2a0d7b4e6"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)

SEED = """
-- No agent or project: a row the log allows only with an error class.
INSERT INTO agent_api_calls
    (id, method, path, status_code, duration_ms, error_class, referenced_host_ids, referenced_entry_ids)
VALUES (1, 'GET', '/api/v1/agent/hosts/4', 500, 3, 'RuntimeError', '[4]', '[7, 8]');

INSERT INTO tool_registry (name, description, category, status, ingestible)
VALUES ('nmap', 'Port scanner.', 'Port Scanning', 'reference', true),
       ('amap', 'Application mapper.', 'Port Scanning', 'reference', false),
       ('ligolo-ng', 'Suggested by an agent.', 'Uncategorised', 'suggested', false);
"""


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
    url = base.set(database=f"{base.database}_f5c2"[:63])
    _drop_database(url)
    _ensure_database(url)
    try:
        _alembic(url, "upgrade", BEFORE)
        eng = create_engine(url)
        try:
            with eng.begin() as conn:
                # A deployment's registry was seeded at startup; start from
                # these rows alone, whatever an earlier revision inserted.
                conn.execute(text("DELETE FROM tool_registry"))
                conn.execute(text(SEED))
            yield url, eng
        finally:
            eng.dispose()
    finally:
        _drop_database(url)


def _columns(eng, table):
    return {c["name"]: c for c in inspect(eng).get_columns(table)}


def _registry(eng):
    with eng.connect() as conn:
        return {
            name: (run, note)
            for name, run, note in conn.execute(
                text("SELECT name, run_command, run_note FROM tool_registry")
            )
        }


def _assert_upgraded(eng):
    assert "referenced_entry_ids" not in _columns(eng, "agent_api_calls")
    with eng.connect() as conn:
        # The row and what it says about hosts are untouched.
        assert conn.execute(text("SELECT referenced_host_ids FROM agent_api_calls WHERE id = 1")).scalar() == [4]
    registry = _registry(eng)
    assert registry["nmap"] == (
        "nmap -sV -sC -O -oX scan.xml <target>",
        "Upload scan.xml (-oX). Use -oG for a .gnmap instead.",
    )
    assert registry["amap"] == (None, None)
    assert registry["ligolo-ng"] == (None, None)


def test_up_down_up(scratch):
    url, eng = scratch

    _alembic(url, "upgrade", REVISION)
    _assert_upgraded(eng)

    _alembic(url, "downgrade", BEFORE)
    eng.dispose()
    entry_ids = _columns(eng, "agent_api_calls")["referenced_entry_ids"]
    assert entry_ids["nullable"] is True and "JSON" in str(entry_ids["type"]).upper()
    with eng.connect() as conn:
        # Back as it was, and empty: the discarded ids are not invented again.
        assert conn.execute(text("SELECT referenced_entry_ids FROM agent_api_calls WHERE id = 1")).scalar() is None
    assert not {"run_command", "run_note"} & set(_columns(eng, "tool_registry"))
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM tool_registry")).scalar() == 3

    _alembic(url, "upgrade", REVISION)
    eng.dispose()
    _assert_upgraded(eng)
