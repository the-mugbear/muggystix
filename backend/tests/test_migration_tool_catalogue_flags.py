"""Migration e3f9c1a5b7d4 — the tool catalogue's intrusive / privileges / output.

Seeding is additive and never overwrites, so the revision is what gives an
existing installation's rows the values the seed now ships.  A column is
written only where it is NULL or still holds what the product shipped it
with; its downgrade puts back only what it wrote.

Up / down / up on a scratch database BUILT BY THE CHAIN, seeded at the
revision before it with:

* ``curl`` stating nothing (filled; an interactive client keeps no output format);
* ``httpx`` as shipped (``requires_privileges`` true, ``json``): corrected;
* ``nikto`` with an administrator's own ``intrusive`` and format (kept both
  ways) beside a ``requires_privileges`` still as shipped (corrected);
* ``ourtool``, not a shipped tool (untouched).

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
BEFORE = "d2e8b0f4a6c3"
REVISION = "e3f9c1a5b7d4"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)

SEED = """
INSERT INTO tool_registry (name, description, category, status, ingestible, intrusive, requires_privileges, output_format)
VALUES ('curl', 'HTTP client.', 'Web Analysis', 'reference', false, NULL, NULL, NULL),
       ('httpx', 'HTTP toolkit.', 'Web Analysis', 'reference', true, false, true, 'json'),
       ('nikto', 'Web scanner.', 'Web Analysis', 'reference', true, false, true, 'csv'),
       ('ourtool', 'In-house.', 'Other', 'reference', false, NULL, NULL, NULL);
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
    url = base.set(database=f"{base.database}_e3f9"[:63])
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
            name: (intrusive, privileged, fmt)
            for name, intrusive, privileged, fmt in conn.execute(text(
                "SELECT name, intrusive, requires_privileges, output_format FROM tool_registry"
            ))
        }


def test_the_revision_writes_what_the_seed_ships():
    """One table: the revision's frozen copy is the seed's, for every tool."""
    rev = _revision_module()
    seed = load_seed()
    assert [(t["name"], t["intrusive"], t["requires_privileges"], t["output_format"]) for t in seed] == list(rev._FLAGS)
    # Every shipped tool says both flags; only a tool that writes no file has no format.
    assert all(t["intrusive"] is not None and t["requires_privileges"] is not None for t in seed)
    assert all(len(t["output_format"]) <= 16 for t in seed if t["output_format"])
    assert set(rev._SHIPPED) <= {t["name"] for t in seed}


def test_up_down_up(scratch):
    url, eng = scratch

    _alembic(url, "upgrade", REVISION)
    upgraded = {
        "curl": (False, False, None),
        "httpx": (False, False, "jsonl"),      # the blanket "needs privileges" is corrected
        # An administrator's "not intrusive" and "csv" are kept (shipped: true,
        # json); "needs privileges" was still as shipped, so it is corrected.
        "nikto": (False, False, "csv"),
        "ourtool": (None, None, None),
    }
    assert _registry(eng) == upgraded

    _alembic(url, "downgrade", BEFORE)
    assert _registry(eng) == {
        "curl": (None, None, None),
        "httpx": (False, True, "json"),        # back to what was shipped
        "nikto": (False, True, "csv"),
        "ourtool": (None, None, None),
    }

    _alembic(url, "upgrade", REVISION)
    assert _registry(eng) == upgraded
