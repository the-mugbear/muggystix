"""Migration e1b4c7d0a2f5 — a project has exactly one scope, and it has no name.

A populated walk on a scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_integrations_application_wide.py``; the test schema is
``create_all()`` from the models and has never had ``scopes.name``, a scope
with no project, or a second scope of one project).

Seeded at ``d9a3e6b0f4c2``: project 1 with two scopes (the shape a seed could
leave), each with subnets and domains, a note on the second scope and agent
calls naming each; project 2 with the one scope the application makes.

Asserted, in order:

* a scope with no project REFUSES the upgrade and changes nothing;
* a CIDR, and then a domain, held by two scopes of one project refuses it,
  naming the entry, and changes nothing — nothing is merged by guess;
* the upgrade then moves every subnet, domain and note into the project's
  first scope, deletes the emptied one, leaves the one-scope project alone,
  drops ``scopes.name`` and ``agent_api_calls.scope_id`` (the calls stay), and
  a second scope of a project is refused by the database from then on;
* the downgrade brings both columns and the old nullability back, without
  splitting the scope again; and the walk repeats.

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
BEFORE = "d9a3e6b0f4c2"
REVISION = "e1b4c7d0a2f5"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


def _run_alembic(url, *args: str) -> subprocess.CompletedProcess:
    """Run alembic against the scratch database, in its own process: env.py
    reads the URL from the settings, which this process has already loaded."""
    env = {
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True,
    )


def _alembic(url, *args: str) -> None:
    done = _run_alembic(url, *args)
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"


@pytest.fixture(scope="module")
def scratch():
    """A database migrated by the chain to the revision before this one."""
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_e1b4"[:63])
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
INSERT INTO projects (id, name, slug, status) VALUES
    (1, 'Two', 'two', 'active'), (2, 'One', 'one', 'active');
INSERT INTO scopes (id, name, project_id) VALUES
    (10, '__default__', 1), (11, 'Eval scope', 1), (20, '__default__', 2);
INSERT INTO subnets (id, scope_id, cidr) VALUES
    (100, 10, '10.1.0.0/24'), (101, 10, '10.1.1.0/24'),
    (110, 11, '10.77.1.0/24'), (111, 11, '10.77.2.0/24'),
    (200, 20, '10.1.0.0/24');
INSERT INTO scope_domains (id, scope_id, domain, include_subdomains) VALUES
    (300, 10, 'example.test', true),
    (310, 11, 'eval.test', false),
    (320, 20, 'example.test', false);
INSERT INTO annotations (id, body, scope_id) VALUES (400, 'about the second scope', 11);
INSERT INTO agent_api_calls (id, method, path, status_code, duration_ms, scope_id, error_class) VALUES
    (500, 'GET', '/api/v1/agent/scopes/10/subnets', 200, 3, 10, 'seeded'),
    (501, 'GET', '/api/v1/agent/scopes/11/subnets', 200, 3, 11, 'seeded');
"""


def _columns(conn, table: str) -> set:
    return {
        r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
        ), {"t": table})
    }


def _revision(conn) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _scope_of(conn, table: str) -> dict:
    return dict(conn.execute(text(f"SELECT id, scope_id FROM {table}")).all())


def _assert_untouched(conn) -> None:
    """A refused upgrade changed nothing: one transaction, rolled back."""
    assert _revision(conn) == BEFORE
    assert "name" in _columns(conn, "scopes")
    assert "scope_id" in _columns(conn, "agent_api_calls")
    assert conn.execute(text("SELECT count(*) FROM scopes WHERE project_id = 1")).scalar() == 2
    assert _scope_of(conn, "subnets")[110] == 11


def _refused(url) -> str:
    done = _run_alembic(url, "upgrade", REVISION)
    assert done.returncode != 0, "the upgrade should have been refused"
    return done.stdout + done.stderr


def _assert_upgraded(conn) -> None:
    assert _revision(conn) == REVISION
    # One scope per project: the lowest id of each.
    assert dict(conn.execute(text("SELECT project_id, id FROM scopes")).all()) == {1: 10, 2: 20}
    # Every entry of the second scope is in the first; the other project's are its own.
    assert _scope_of(conn, "subnets") == {100: 10, 101: 10, 110: 10, 111: 10, 200: 20}
    assert _scope_of(conn, "scope_domains") == {300: 10, 310: 10, 320: 20}
    assert _scope_of(conn, "annotations") == {400: 10}
    assert conn.execute(text(
        "SELECT include_subdomains FROM scope_domains WHERE id = 300"
    )).scalar() is True
    # The name and the call log's scope column are gone; the calls are not.
    assert "name" not in _columns(conn, "scopes")
    assert "scope_id" not in _columns(conn, "agent_api_calls")
    assert conn.execute(text("SELECT count(*) FROM agent_api_calls")).scalar() == 2
    assert conn.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'scopes' AND column_name = 'project_id'"
    )).scalar() == "NO"
    assert conn.execute(text(
        "SELECT contype::text FROM pg_constraint WHERE conname = 'uq_scopes_project'"
    )).scalar() == "u"


def test_the_upgrade_refuses_what_it_cannot_decide_then_leaves_one_scope_per_project(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text(SEED))

    # ---- a scope nobody owns ------------------------------------------------
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO scopes (id, name, project_id) VALUES (30, 'orphan', NULL)"))
    message = _refused(url)
    assert "[30] belong to no project" in message and "Nothing was changed" in message
    with eng.connect() as conn:
        _assert_untouched(conn)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM scopes WHERE id = 30"))

    # ---- one CIDR in two scopes of one project ------------------------------
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO subnets (id, scope_id, cidr) VALUES (112, 11, '10.1.1.0/24')"))
    message = _refused(url)
    assert "project 1: subnet 10.1.1.0/24" in message
    # The same CIDR in ANOTHER project's scope (10.1.0.0/24) is no clash.
    assert "10.1.0.0/24" not in message
    with eng.connect() as conn:
        _assert_untouched(conn)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM subnets WHERE id = 112"))

    # ---- one domain in two scopes of one project ----------------------------
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO scope_domains (id, scope_id, domain, include_subdomains) "
            "VALUES (311, 11, 'example.test', false)"
        ))
    message = _refused(url)
    assert "project 1: domain example.test" in message
    with eng.connect() as conn:
        _assert_untouched(conn)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM scope_domains WHERE id = 311"))

    # ---- upgrade -------------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        _assert_upgraded(conn)

    # A second scope of a project is now impossible, and so is one with no project.
    for statement in (
        "INSERT INTO scopes (project_id) VALUES (1)",
        "INSERT INTO scopes (project_id) VALUES (NULL)",
    ):
        with pytest.raises(IntegrityError):
            with eng.begin() as conn:
                conn.execute(text(statement))

    # ---- downgrade -----------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert _revision(conn) == BEFORE
        # The name is back, the same on every row; the scope is not split again.
        assert dict(conn.execute(text("SELECT id, name FROM scopes")).all()) == {
            10: "__default__", 20: "__default__",
        }
        assert _scope_of(conn, "subnets") == {100: 10, 101: 10, 110: 10, 111: 10, 200: 20}
        assert conn.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'scopes' AND column_name = 'name'"
        )).scalar() == "NO"
        assert conn.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'uq_scopes_project'"
        )).scalar() == 0
        # The call log's column is back, empty, with the baseline's SET NULL.
        assert conn.execute(text(
            "SELECT count(*) FROM agent_api_calls WHERE scope_id IS NOT NULL"
        )).scalar() == 0
        assert conn.execute(text(
            "SELECT confdeltype::text FROM pg_constraint "
            "WHERE conrelid = 'agent_api_calls'::regclass AND contype = 'f' "
            "AND pg_get_constraintdef(oid) LIKE '%(scope_id)%'"
        )).scalar() == "n"
    # The old schema allows what the new one refuses.
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO scopes (id, name, project_id) VALUES (12, 'again', 1)"))
        conn.execute(text("INSERT INTO subnets (id, scope_id, cidr) VALUES (120, 12, '10.88.0.0/24')"))

    # ---- and up again ---------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert dict(conn.execute(text("SELECT project_id, id FROM scopes")).all()) == {1: 10, 2: 20}
        assert _scope_of(conn, "subnets")[120] == 10
        assert "name" not in _columns(conn, "scopes")
