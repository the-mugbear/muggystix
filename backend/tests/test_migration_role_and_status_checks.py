"""Revision 47e5e79b90a9 on a scratch database BUILT BY THE CHAIN (the pattern
of ``test_migration_r7_schema.py``).

* The upgrade REFUSES while a row holds a role or a status outside its
  vocabulary, naming the table and count, and changes nothing;
* with the rows corrected it adds the three CHECKs, which then refuse such a
  value and accept every known one;
* down drops them (the value is storable again), and up adds them back.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine
from tests.test_migration_r7_schema import _alembic, _refused, _revision

BEFORE = "c8e2a4f6b1d9"
CHECKS = "47e5e79b90a9"
NAMES = {"ck_users_role", "ck_project_memberships_role", "ck_projects_status"}

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


@pytest.fixture(scope="module")
def scratch():
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_rolechk"[:63])
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
INSERT INTO users (id, username, hashed_password, role, is_active) VALUES
    (1, 'admin', 'x', 'admin', true), (2, 'legacy', 'x', 'analyst', true), (3, 'm', 'x', 'member', true);
INSERT INTO projects (id, name, slug, status) VALUES (1, 'P', 'p', 'active'), (2, 'Q', 'q', 'in_progress');
INSERT INTO project_memberships (id, project_id, user_id, role) VALUES (1, 1, 1, 'admin'), (2, 1, 3, 'owner');
"""


def _checks(conn) -> set:
    return {r[0] for r in conn.execute(text(
        "SELECT conname FROM pg_constraint WHERE contype = 'c' AND conname = ANY(:n)"
    ), {"n": list(NAMES)})}


def _refuses(eng, statement: str) -> bool:
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(text(statement))
        except IntegrityError:
            return True
        finally:
            trans.rollback()
    return False


BAD = (
    "UPDATE users SET role = 'viewer' WHERE id = 3",
    "UPDATE project_memberships SET role = 'member' WHERE id = 1",
    "UPDATE projects SET status = 'draft' WHERE id = 1",
)


def test_the_walk(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text(SEED))

    _refused(url, CHECKS, "users.role: 1 row(s)", "project_memberships.role: 1 row(s)",
             "projects.status: 1 row(s)")
    with eng.connect() as conn:
        assert _revision(conn) == BEFORE
        assert _checks(conn) == set()
        assert conn.execute(text("SELECT role FROM users WHERE id = 2")).scalar() == "analyst"

    with eng.begin() as conn:
        conn.execute(text("UPDATE users SET role = 'member' WHERE id = 2"))
        conn.execute(text("UPDATE projects SET status = 'active' WHERE id = 2"))
        conn.execute(text("UPDATE project_memberships SET role = 'viewer' WHERE id = 2"))
    _alembic(url, "upgrade", CHECKS)
    with eng.connect() as conn:
        assert _checks(conn) == NAMES
    for statement in BAD:
        assert _refuses(eng, statement), statement
    for good in ("UPDATE users SET role = 'admin' WHERE id = 3",
                 "UPDATE project_memberships SET role = 'auditor' WHERE id = 2",
                 "UPDATE projects SET status = 'archived' WHERE id = 2"):
        assert not _refuses(eng, good), good

    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert _checks(conn) == set()
    assert not _refuses(eng, BAD[0])

    _alembic(url, "upgrade", CHECKS)
    with eng.connect() as conn:
        assert _checks(conn) == NAMES
