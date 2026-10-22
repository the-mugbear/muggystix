"""Revisions e3b7a1c9d5f0, f4c8b2d0e6a1, a5d9c3e1f7b2 and b6e0d4f2a8c3 on a
scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_one_scope_per_project.py``: the suite's own schema is built
from the models and can never hold the rows these revisions refuse).

Asserted, in order:

* ``e3b7a1c9d5f0`` REFUSES while any of the five tables holds a row with no
  project, naming the table and ids, and changes nothing; with the rows
  gone it makes ``project_id`` NOT NULL on all five;
* ``f4c8b2d0e6a1`` refuses while a key has no agent or no session, naming
  the key, and changes nothing; then drops ``user_id`` / ``scopes`` and makes
  both bindings NOT NULL (the keys stay);
* ``a5d9c3e1f7b2`` drops the 100 redundant indexes;
* ``b6e0d4f2a8c3`` refuses while two vulnerabilities rows share an identity,
  naming them, and changes nothing; then the unique index refuses a repeat
  (NULL port and plugin id included) and keeps rows that differ;
* the downgrade to the start brings every column, nullability and index
  back without losing a row; and the walk repeats.
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
BEFORE = "d5a1f3c7e9b2"
NOT_NULL = "e3b7a1c9d5f0"
KEYS = "f4c8b2d0e6a1"
INDEXES = "a5d9c3e1f7b2"
IDENTITY = "b6e0d4f2a8c3"
FIVE = ("hosts_v2", "scans", "ingestion_jobs", "parse_errors", "dns_records")

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


def _run_alembic(url, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "BLUESTICK_SKIP_DB_INIT": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
    )


def _alembic(url, *args: str) -> None:
    done = _run_alembic(url, *args)
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"


def _refused(url, target: str, *needles: str) -> None:
    done = _run_alembic(url, "upgrade", target)
    assert done.returncode != 0, "the upgrade should have been refused"
    for needle in needles:
        assert needle in done.stderr, (needle, done.stderr[-2000:])
    assert "Nothing was changed" in done.stderr


@pytest.fixture(scope="module")
def scratch():
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_r7"[:63])
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
INSERT INTO users (id, username, hashed_password, role) VALUES (1, 'op', 'x', 'member');
INSERT INTO projects (id, name, slug, status) VALUES (1, 'P', 'p', 'active');
INSERT INTO scans (id, project_id, filename) VALUES (10, 1, 'a.xml'), (11, NULL, 'orphan.xml');
INSERT INTO hosts_v2 (id, project_id, ip_address) VALUES (20, 1, '10.0.0.1'), (21, NULL, '10.0.0.2');
INSERT INTO ingestion_jobs (id, project_id, status, storage_path, original_filename, filename) VALUES
    (30, NULL, 'completed', '/x', 'o.xml', 'o.xml');
INSERT INTO parse_errors (id, project_id, error_type, filename, error_message) VALUES
    (40, NULL, 'parsing_error', 'o.xml', 'bad');
INSERT INTO dns_records (id, project_id, domain, record_type, value) VALUES
    (50, NULL, 'a.test', 'A', '10.0.0.2');
INSERT INTO agents (id, name, project_id, owner_id, is_active) VALUES (60, 'agent', 1, 1, true);
INSERT INTO agent_sessions (id, workflow, project_id) VALUES (70, 'assist', 1);
INSERT INTO api_keys (id, agent_id, agent_session_id, user_id, name, key_hash, key_prefix, scopes) VALUES
    (80, 60, 70, NULL, 'bound', 'h80', 'nm_agent_80', NULL),
    (81, 60, NULL, NULL, 'unbound', 'h81', 'nm_agent_81', NULL),
    (82, NULL, NULL, 1, 'operator', 'h82', 'nm_operator', '["read"]');
INSERT INTO vulnerabilities (id, host_id, source, severity, title, plugin_id) VALUES
    (90, 20, 'NESSUS', 'HIGH', 'Old SSH', '1'),
    (91, 20, 'NESSUS', 'HIGH', 'Old SSH', '1'),
    (92, 20, 'MANUAL', 'LOW', 'Synthetic low issue', NULL);
"""


def _revision(conn) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _nullable(conn, table: str, column: str):
    return conn.execute(text(
        "SELECT is_nullable FROM information_schema.columns WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).scalar()


def _indexes(conn) -> set:
    return {r[0] for r in conn.execute(text(
        "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
    ))}


def test_the_walk(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text(SEED))
        indexes_before = _indexes(conn)

    # e3b7a1c9d5f0: rows with no project stop it, named, nothing changed.
    _refused(url, NOT_NULL, "hosts_v2 ids [21]", "scans ids [11]", "ingestion_jobs ids [30]",
             "parse_errors ids [40]", "dns_records ids [50]")
    with eng.connect() as conn:
        assert _revision(conn) == BEFORE
        assert conn.execute(text("SELECT project_id FROM hosts_v2 WHERE id = 21")).scalar() is None
    with eng.begin() as conn:
        for table in ("dns_records", "parse_errors", "ingestion_jobs"):
            conn.execute(text(f"DELETE FROM {table} WHERE project_id IS NULL"))
        conn.execute(text("UPDATE hosts_v2 SET project_id = 1 WHERE id = 21"))
        conn.execute(text("UPDATE scans SET project_id = 1 WHERE id = 11"))
    _alembic(url, "upgrade", NOT_NULL)
    with eng.connect() as conn:
        assert {_nullable(conn, t, "project_id") for t in FIVE} == {"NO"}

    # f4c8b2d0e6a1: an unbound key stops it, named.
    _refused(url, KEYS, "2 key(s)", "[81, 82]")
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM api_keys WHERE id IN (81, 82)"))
    _alembic(url, "upgrade", KEYS)
    with eng.connect() as conn:
        assert _nullable(conn, "api_keys", "user_id") is None
        assert _nullable(conn, "api_keys", "scopes") is None
        assert _nullable(conn, "api_keys", "agent_session_id") == "NO"
        assert conn.execute(text("SELECT name FROM api_keys")).scalars().all() == ["bound"]

    # a5d9c3e1f7b2: the redundant indexes go.
    _alembic(url, "upgrade", INDEXES)
    with eng.connect() as conn:
        after = _indexes(conn)
    for gone in ("ix_hosts_v2_id", "ix_hosts_v2_ip_address", "ix_hosts_v2_project_id",
                 "ix_vulnerabilities_id", "ix_vulnerabilities_host_id", "idx_port_scan_history_scan"):
        assert gone in indexes_before and gone not in after, gone
    assert len(indexes_before - after) == 100
    assert {"idx_host_ip_address", "uq_project_ip", "idx_vulnerability_host_severity"} <= after

    # b6e0d4f2a8c3: duplicates stop it, named; nothing merged.
    _refused(url, IDENTITY, "1 identity is", "ids [90, 91]")
    with eng.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM vulnerabilities")).scalar() == 3
        conn.execute(text("DELETE FROM vulnerabilities WHERE id = 91"))
    _alembic(url, "upgrade", IDENTITY)
    with eng.connect() as conn:
        assert "uq_vulnerability_identity" in _indexes(conn)
    for values in ("(20, 'NESSUS', 'HIGH', 'Old SSH', '1')",
                   "(20, 'MANUAL', 'LOW', 'Synthetic low issue', NULL)"):
        with pytest.raises(IntegrityError):
            with eng.begin() as conn:
                conn.execute(text(
                    f"INSERT INTO vulnerabilities (host_id, source, severity, title, plugin_id) VALUES {values}"
                ))
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO vulnerabilities (host_id, source, severity, title, plugin_id) "
            "VALUES (20, 'NESSUS', 'HIGH', 'Old SSH, renamed', '1')"
        ))

    # Down to the start: every column, nullability and index back, no row lost.
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert _revision(conn) == BEFORE
        assert {_nullable(conn, t, "project_id") for t in FIVE} == {"YES"}
        assert _nullable(conn, "api_keys", "user_id") == "YES"
        assert _nullable(conn, "api_keys", "agent_session_id") == "YES"
        assert _indexes(conn) == indexes_before
        assert conn.execute(text("SELECT count(*) FROM vulnerabilities")).scalar() == 3
        assert conn.execute(text("SELECT count(*) FROM hosts_v2")).scalar() == 2

    # ... and up again.
    _alembic(url, "upgrade", IDENTITY)
    with eng.connect() as conn:
        assert _revision(conn) == IDENTITY
