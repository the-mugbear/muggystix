"""Migration d7b3e9a1c5f2 — ``host_scan_history.hostname_changed``.

A populated walk on a scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_review_2026_10_10_schema.py``; the suite's own schema is
``create_all()`` from the models and already has the column).

Seeded at ``a4f8c2e6b0d1``: two hosts, two scans, three history rows — one of
the scan that created its host, two of a scan that re-observed them.

Asserted, in order:

* before the revision the column does not exist;
* the upgrade adds it NOT NULL with a ``false`` default and builds no index;
  every row that was there reads ``false`` (earlier imports are not
  back-filled) and nothing else on those rows changed; a row written with the
  old column list (masscan's raw INSERT) reads ``false`` too;
* a row can then be marked, and NULL is refused;
* the downgrade drops the column and keeps every row and every other value;
* the upgrade again brings the column back with every row ``false`` — what a
  downgrade loses is which scans renamed which hosts.

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
BEFORE = "a4f8c2e6b0d1"
REVISION = "d7b3e9a1c5f2"

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
    url = base.set(database=f"{base.database}_d7b3"[:63])
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
INSERT INTO projects (id, name, slug, status) VALUES (1, 'Names', 'names', 'active');
INSERT INTO scans (id, project_id, filename, tool_name) VALUES (1, 1, 'first.xml', 'nmap'), (2, 1, 'dnsx.jsonl', 'dnsx');
INSERT INTO hosts_v2 (id, ip_address, project_id, hostname) VALUES
    (1, '10.1.0.1', 1, 'renamed-before-the-column.example'), (2, '10.1.0.2', 1, NULL);
INSERT INTO host_scan_history (id, host_id, scan_id, state_at_scan, hostname_at_scan, host_created) VALUES
    (10, 1, 1, 'up', 'old.example', true),
    (11, 1, 2, NULL, 'renamed-before-the-column.example', false),
    (12, 2, 2, NULL, NULL, false);
"""

ROWS = "SELECT id, host_id, scan_id, state_at_scan, hostname_at_scan, host_created FROM host_scan_history ORDER BY id"


def _revision(conn) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _column(conn):
    return conn.execute(text(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'host_scan_history' "
        "AND column_name = 'hostname_changed'"
    )).first()


def _indexes(conn) -> set:
    return {
        r[0] for r in conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() "
            "AND tablename = 'host_scan_history'"
        ))
    }


def _flags(conn) -> dict:
    return dict(conn.execute(text("SELECT id, hostname_changed FROM host_scan_history ORDER BY id")).all())


def test_the_column_arrives_false_is_dropped_and_comes_back_false(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text(SEED))
    with eng.connect() as conn:
        assert _revision(conn) == BEFORE
        assert _column(conn) is None
        rows_before = conn.execute(text(ROWS)).all()
        indexes_before = _indexes(conn)
        hosts_before = conn.execute(text("SELECT id, hostname FROM hosts_v2 ORDER BY id")).all()

    # ---- upgrade -------------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert _revision(conn) == REVISION
        data_type, nullable, default = _column(conn)
        assert (data_type, nullable, default) == ("boolean", "NO", "false")
        # Rows from before read false — the scan that did rename a host then
        # is NOT back-filled (owner decision) — and are otherwise as they were.
        assert _flags(conn) == {10: False, 11: False, 12: False}
        assert conn.execute(text(ROWS)).all() == rows_before
        assert conn.execute(text("SELECT id, hostname FROM hosts_v2 ORDER BY id")).all() == hosts_before
        assert _indexes(conn) == indexes_before      # no index built

    with eng.begin() as conn:
        # A writer that does not name the column (masscan's raw INSERT).
        conn.execute(text(
            "INSERT INTO scans (id, project_id, filename, tool_name) VALUES (3, 1, 'masscan.xml', 'masscan')"
        ))
        conn.execute(text(
            "INSERT INTO host_scan_history (id, host_id, scan_id, state_at_scan, host_created) "
            "VALUES (13, 2, 3, 'up', false)"
        ))
        # A writer that does.
        conn.execute(text("UPDATE host_scan_history SET hostname_changed = true WHERE id = 12"))
    with eng.connect() as conn:
        assert _flags(conn) == {10: False, 11: False, 12: True, 13: False}
        with pytest.raises(IntegrityError):
            conn.execute(text("UPDATE host_scan_history SET hostname_changed = NULL WHERE id = 10"))
        conn.rollback()
        rows_on_revision = conn.execute(text(ROWS)).all()

    # ---- downgrade -----------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert _revision(conn) == BEFORE
        assert _column(conn) is None
        # Every row and every other value stays; only the mark is gone.
        assert conn.execute(text(ROWS)).all() == rows_on_revision
        assert _indexes(conn) == indexes_before

    # ---- and up again --------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert _revision(conn) == REVISION
        assert _flags(conn) == {10: False, 11: False, 12: False, 13: False}
        assert conn.execute(text(ROWS)).all() == rows_on_revision
