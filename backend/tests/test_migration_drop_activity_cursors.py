"""Migration 7b83a791a783 — the Activity "seen" cursor is gone.

Review 2026-10-23, R-FE-P1 (owner: remove it all).  The table had one writer
(``POST …/hosts/notes/mark-seen``, Collaboration on mount) and one reader
(``GET …/hosts/notes/unread-count``) that nothing called.

* the routes answer 404 (both doors are gone, no alias);
* the model is gone from the metadata;
* up / down / up on a scratch database BUILT BY THE CHAIN: the downgrade
  recreates the table exactly as the revision before left it (columns,
  constraints, indexes, foreign-key actions — read from the catalog), and the
  upgrade drops it again.

The migration half is Postgres only: the scratch database is created on the
server the suite uses.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db.session import Base
from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BEFORE = "c8e2a4f6b1d9"
REVISION = "7b83a791a783"


def test_the_routes_are_gone(client, test_project):
    pid = test_project.id
    assert client.get(f"/api/v1/projects/{pid}/hosts/notes/unread-count").status_code in (404, 405)
    assert client.post(f"/api/v1/projects/{pid}/hosts/notes/mark-seen").status_code in (404, 405)
    paths = client.app.openapi()["paths"]
    assert not any(p.endswith("/notes/unread-count") or p.endswith("/notes/mark-seen") for p in paths)


def test_the_model_is_gone():
    import app.db.model_registry  # noqa: F401 — every model module
    assert "activity_cursors" not in Base.metadata.tables


def _alembic(url, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
    )
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"


_SHAPE = {
    "columns": """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns WHERE table_name = 'activity_cursors'
        ORDER BY column_name""",
    "constraints": """
        SELECT conname, contype, pg_get_constraintdef(oid)
        FROM pg_constraint WHERE conrelid = 'activity_cursors'::regclass
        ORDER BY conname""",
    "indexes": """
        SELECT indexname, indexdef FROM pg_indexes
        WHERE tablename = 'activity_cursors' ORDER BY indexname""",
}


def _shape(eng) -> dict:
    with eng.connect() as conn:
        return {k: [tuple(r) for r in conn.execute(text(q))] for k, q in _SHAPE.items()}


def _exists(eng) -> bool:
    with eng.connect() as conn:
        return conn.execute(text("SELECT to_regclass('activity_cursors') IS NOT NULL")).scalar()


@pytest.mark.skipif(not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on")
def test_up_down_up_on_a_chain_built_database():
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_7b83"[:63])
    _drop_database(url)
    _ensure_database(url)
    eng = create_engine(url)
    try:
        _alembic(url, "upgrade", BEFORE)
        before = _shape(eng)
        assert before["columns"], "the revision before should hold the table"
        assert len(before["constraints"]) == 4  # pkey, two FKs, the unique pair
        assert [i for i, _ in before["indexes"]] == [
            "activity_cursors_pkey", "ix_activity_cursors_project_id", "uq_activity_cursor_user_project",
        ]

        _alembic(url, "upgrade", REVISION)
        assert not _exists(eng)

        _alembic(url, "downgrade", BEFORE)
        assert _shape(eng) == before

        _alembic(url, "upgrade", REVISION)
        assert not _exists(eng)
    finally:
        eng.dispose()
        _drop_database(url)
