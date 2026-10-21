"""Migrations b8e4d2f6a0c3 (a finding's edit history) and c4f9e2a7b1d5 (the
agent feedback gaps), walked on a scratch database BUILT BY THE CHAIN (the
pattern of ``test_migration_query_history.py``).

Seeded at ``a7d3c9e1f5b2``: a finding with a status line, two evidence
records (one with a multi-byte raw output, one with none) and a proposal.

Asserted:

* up: the history's three new columns are empty on the old row; every raw
  output's ``raw_output_sha256`` is what the write path computes now (Python's
  SHA-256 of the UTF-8), none for a record with no output; a proposal may name
  the one that replaced it; an ``info`` record may have no host and any other
  may not (``ck_evidence_host_or_info``);
* down: the host-less record and the edit lines are deleted (the old columns
  cannot hold them), everything else is kept, ``host_id`` is NOT NULL again;
* up again works on what the downgrade left.
"""
from __future__ import annotations

import hashlib
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
BEFORE = "a7d3c9e1f5b2"
HEAD = "c4f9e2a7b1d5"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)

RAW = "é€😀 banner\nline two"


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
    url = base.set(database=f"{base.database}_c4f9"[:63])
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
INSERT INTO users (id, username, hashed_password, role, is_active) VALUES (1, 'alice', 'x', 'member', true);
INSERT INTO hosts_v2 (id, project_id, ip_address, state) VALUES (1, 1, '10.0.0.1', 'up');
INSERT INTO findings (id, project_id, title, severity, status, source)
    VALUES (1, 1, 'Weak TLS', 'medium', 'open', 'manual');
INSERT INTO finding_status_history (id, finding_id, from_status, to_status, changed_by_id)
    VALUES (1, 1, NULL, 'open', 1);
INSERT INTO evidence_records (id, project_id, host_id, tool, outcome, summary, raw_output, created_at)
    VALUES (1, 1, 1, 'curl', 'info', 'Banner', :raw, now()),
           (2, 1, 1, 'nmap', 'no_finding', 'Closed', NULL, now());
INSERT INTO agent_proposals (id, project_id, kind, status, source, finding_id, field, payload, created_at)
    VALUES (1, 1, 'finding_text', 'pending', 'agent', 1, 'impact', '{"value": "i"}', now()),
           (2, 1, 'finding_text', 'pending', 'agent', 1, 'impact', '{"value": "j"}', now());
"""


def test_the_edit_history_and_the_agent_gaps_go_up_and_down(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        for statement in filter(str.strip, SEED.split(";")):
            conn.execute(text(statement), {"raw": RAW} if ":raw" in statement else {})

    # ---- up ------------------------------------------------------------------
    _alembic(url, "upgrade", HEAD)
    with eng.begin() as conn:
        assert tuple(conn.execute(text(
            "SELECT field, before_value, after_value FROM finding_status_history WHERE id = 1")).one()) == (
            None, None, None)
        hashes = dict(conn.execute(text("SELECT id, raw_output_sha256 FROM evidence_records")).all())
        assert hashes == {1: hashlib.sha256(RAW.encode("utf-8")).hexdigest(), 2: None}

        conn.execute(text("UPDATE agent_proposals SET status = 'superseded', replaced_by_id = 2 WHERE id = 1"))
        conn.execute(text(
            "INSERT INTO evidence_records (id, project_id, host_id, tool, outcome, summary, created_at) "
            "VALUES (3, 1, NULL, 'dig', 'info', 'MX off-site', now())"))
        conn.execute(text(
            "INSERT INTO finding_status_history (id, finding_id, from_status, to_status, changed_by_id, "
            "summary, field, before_value, after_value) VALUES (2, 1, 'open', 'open', 1, 'Renamed', 'title', "
            "'Weak TLS', 'TLS 1.0')"))
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO evidence_records (project_id, host_id, tool, outcome, summary, created_at) "
                "VALUES (1, NULL, 'dig', 'no_finding', 'x', now())"))

    # ---- down ----------------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert [r[0] for r in conn.execute(text("SELECT id FROM evidence_records ORDER BY id"))] == [1, 2]
        assert [r[0] for r in conn.execute(text("SELECT id FROM finding_status_history ORDER BY id"))] == [1]
        assert [r[0] for r in conn.execute(text("SELECT id FROM agent_proposals ORDER BY id"))] == [1, 2]
        nullable = conn.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'evidence_records' AND column_name = 'host_id'")).scalar()
        assert nullable == "NO"

    # ---- up again --------------------------------------------------------------
    _alembic(url, "upgrade", HEAD)
    with eng.connect() as conn:
        assert conn.execute(text("SELECT raw_output_sha256 FROM evidence_records WHERE id = 1")).scalar() == (
            hashlib.sha256(RAW.encode("utf-8")).hexdigest())
