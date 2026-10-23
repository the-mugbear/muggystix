"""Migration b3d9f1a7c5e2 — run text for testssl, gvm-cli and gvm-tools.

Seeding is additive and never overwrites, so the revision is what gives an
existing installation's rows the text the seed now ships.  It writes only a
column that is still NULL, and its downgrade clears only what it wrote.

Up / down / up on a scratch database BUILT BY THE CHAIN, seeded at the
revision before it with:

* ``testssl`` and ``gvm-tools`` as shipped (no run text);
* ``gvm-cli`` with a note somebody already put there (kept both ways);
* ``nmap`` with its own command (untouched).

Postgres only: the scratch database is created on the server the suite uses.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.services.tool_registry_service import load_seed
from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BEFORE = "e8c4a2f6d1b9"
REVISION = "b3d9f1a7c5e2"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)

KEPT_NOTE = "Export from the GSA as XML, then upload it (our own wording)."

SEED = f"""
INSERT INTO tool_registry (name, description, category, status, ingestible, run_command, run_note)
VALUES ('testssl', 'TLS scanner.', 'Web Analysis', 'reference', true, NULL, NULL),
       ('gvm-tools', 'GMP clients.', 'Vulnerability Scanning', 'reference', true, NULL, NULL),
       ('gvm-cli', 'GMP client.', 'Vulnerability Scanning', 'reference', true, NULL, '{KEPT_NOTE}'),
       ('nmap', 'Port scanner.', 'Port Scanning', 'reference', true,
        'nmap -sV -sC -O -oX scan.xml <target>', 'Upload scan.xml (-oX). Use -oG for a .gnmap instead.');
"""


def _revision_module():
    path = next((BACKEND_ROOT / "alembic" / "versions").glob(f"{REVISION}_*.py"))
    spec = importlib.util.spec_from_file_location(f"rev_{REVISION}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic(url, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
    )
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"


@pytest.fixture(scope="module")
def scratch():
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_b3d9"[:63])
    _drop_database(url)
    _ensure_database(url)
    try:
        _alembic(url, "upgrade", BEFORE)
        eng = create_engine(url)
        try:
            with eng.begin() as conn:
                conn.execute(text("DELETE FROM tool_registry"))
                conn.execute(text(SEED))
            yield url, eng
        finally:
            eng.dispose()
    finally:
        _drop_database(url)


def _registry(eng):
    with eng.connect() as conn:
        return {
            name: (run, note)
            for name, run, note in conn.execute(text("SELECT name, run_command, run_note FROM tool_registry"))
        }


NMAP = ("nmap -sV -sC -O -oX scan.xml <target>", "Upload scan.xml (-oX). Use -oG for a .gnmap instead.")


def test_the_revision_writes_what_the_seed_ships():
    """One text: the revision's frozen copy is the seed's."""
    rev = _revision_module()
    seed = {row["name"]: row for row in load_seed()}
    assert seed["testssl"]["run_command"] == rev.TESTSSL_RUN_COMMAND
    assert seed["gvm-cli"]["run_note"] == rev.GVM_RUN_NOTE
    assert seed["gvm-tools"]["run_note"] == rev.GVM_RUN_NOTE
    assert "run_command" not in seed["gvm-cli"] and "run_command" not in seed["gvm-tools"]


def test_up_down_up(scratch):
    url, eng = scratch
    rev = _revision_module()

    _alembic(url, "upgrade", REVISION)
    upgraded = {
        "testssl": (rev.TESTSSL_RUN_COMMAND, None),
        "gvm-tools": (None, rev.GVM_RUN_NOTE),
        "gvm-cli": (None, KEPT_NOTE),     # somebody's text is never overwritten
        "nmap": NMAP,
    }
    assert _registry(eng) == upgraded

    _alembic(url, "downgrade", BEFORE)
    assert _registry(eng) == {
        "testssl": (None, None),
        "gvm-tools": (None, None),
        "gvm-cli": (None, KEPT_NOTE),     # not what the upgrade wrote: kept
        "nmap": NMAP,
    }

    _alembic(url, "upgrade", REVISION)
    assert _registry(eng) == upgraded
