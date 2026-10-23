"""Migration e8c4a2f6d1b9 (a host's review marks outlive the account that made
them), walked on a scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_agent_feedback_gaps.py``).

Seeded at ``f2b8d4a6c1e3``: two people's review marks on one host.

Asserted:

* up: ``host_follows.user_id`` is nullable and its key SET NULL — deleting a
  reviewer keeps their row with no reviewer, its review intact; two deleted
  reviewers of one host keep two rows (``uq_host_follow_user`` treats NULLs as
  distinct), while a living person still holds one row per host;
* down: the rows with no reviewer are DELETED (the old NOT NULL column cannot
  hold them), every other row kept, the column NOT NULL and the key CASCADE
  again;
* up again works on what the downgrade left.
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
BEFORE = "f2b8d4a6c1e3"
HEAD = "e8c4a2f6d1b9"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


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
    url = base.set(database=f"{base.database}_e8c4"[:63])
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
    (1, 'alice', 'x', 'member', true), (2, 'bob', 'x', 'member', true), (3, 'carol', 'x', 'member', true);
INSERT INTO hosts_v2 (id, project_id, ip_address, state) VALUES (1, 1, '10.0.0.1', 'up');
INSERT INTO host_follows (id, host_id, user_id, status, reviewed_at, review_summary, assigned_at, assigned_by_id) VALUES
    (1, 1, 1, 'REVIEWED', now(), 'Checked.', now(), 2),
    (2, 1, 2, 'IN_REVIEW', NULL, NULL, NULL, NULL),
    (3, 1, 3, 'REVIEWED', now(), NULL, NULL, NULL);
"""


def _guards(conn):
    """(trigger present, check present) — the "an assignment needs a person" rule."""
    trigger = conn.execute(text(
        "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_host_follows_assignment_needs_a_person'")).scalar()
    check = conn.execute(text(
        "SELECT count(*) FROM pg_constraint WHERE conname = 'ck_host_follows_assignment_needs_a_person'")).scalar()
    return bool(trigger), bool(check)


def _column(conn):
    nullable = conn.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'host_follows' AND column_name = 'user_id'")).scalar()
    rule = conn.execute(text(
        "SELECT confdeltype FROM pg_constraint WHERE conname = 'host_follows_user_id_fkey'")).scalar()
    return nullable, rule


def test_review_marks_outlive_their_reviewer_up_and_down(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        for statement in filter(str.strip, SEED.split(";")):
            conn.execute(text(statement))
        assert _column(conn) == ("NO", "c")

    # ---- up ------------------------------------------------------------------
    _alembic(url, "upgrade", HEAD)
    with eng.begin() as conn:
        assert _column(conn) == ("YES", "n")
        assert _guards(conn) == (True, True)
        conn.execute(text("DELETE FROM users WHERE id IN (1, 3)"))
        rows = conn.execute(text(
            "SELECT id, user_id, status, reviewed_at IS NOT NULL, review_summary, assigned_at, assigned_by_id "
            "FROM host_follows ORDER BY id")).all()
        # Kept, and the assignment the deleted person held ended with them —
        # done by the database in the DELETE itself (the SET NULL fires the trigger).
        assert [tuple(r) for r in rows] == [
            (1, None, "REVIEWED", True, "Checked.", None, None),
            (2, 2, "IN_REVIEW", False, None, None, None),
            (3, None, "REVIEWED", True, None, None, None),
        ]
    # A living person still holds one row per host.
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO host_follows (host_id, user_id, status) VALUES (1, 2, 'REVIEWED')"))

    # ---- down ----------------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.begin() as conn:
        assert [tuple(r) for r in conn.execute(text("SELECT id, user_id FROM host_follows ORDER BY id"))] == [(2, 2)]
        assert _column(conn) == ("NO", "c")
        assert _guards(conn) == (False, False)

    # ---- up again --------------------------------------------------------------
    _alembic(url, "upgrade", HEAD)
    with eng.begin() as conn:
        assert _column(conn) == ("YES", "n")
        assert _guards(conn) == (True, True)
        conn.execute(text("DELETE FROM users WHERE id = 2"))
        assert [tuple(r) for r in conn.execute(text("SELECT id, user_id FROM host_follows"))] == [(2, None)]
