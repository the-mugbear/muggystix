"""Migration a4f8c2e6b0d1 — a CIDR is in a scope once, four foreign keys get
their index, the agent call log outlives its agent, a TOTP code works once.

A populated walk on a scratch database BUILT BY THE CHAIN (the pattern of
``test_migration_one_scope_per_project.py``; the suite's own schema is
``create_all()`` from the models and has never held a duplicate subnet or the
old CASCADE).

Seeded at ``c2a7e5f1d9b3``: a scope holding one CIDR three times (the first
row empty, the second with a description and a site, the third blank; their
host mappings and labels overlapping), one CIDR twice with the SAME
description, a CIDR once, and the same CIDR in another project's scope; an
account with an agent, a key, a session and two recorded calls.

Asserted, in order:

* two rows of one CIDR with DIFFERENT descriptions, and then with different
  sites, REFUSE the upgrade, naming them, and change nothing;
* the upgrade then merges each duplicate group into its lowest id — every
  host mapped once, every label once, the description and site carried over —
  leaves the other scope alone, and the database refuses a duplicate from
  then on;
* the four indexes exist and ``ix_subnets_cidr`` is gone;
* deleting the account that owned the agent keeps its calls, attributed to
  nobody, and the relaxed check still refuses a row that names no project;
* the downgrade restores the old constraint, indexes and CASCADE, deletes
  exactly the calls the old check cannot hold, and does not split a merged
  subnet; and the walk repeats.

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
BEFORE = "c2a7e5f1d9b3"
REVISION = "a4f8c2e6b0d1"

NEW_INDEXES = {
    "ix_hosts_v2_last_updated_scan_id": "hosts_v2",
    "ix_ports_v2_last_updated_scan_id": "ports_v2",
    "ix_finding_hosts_port_id": "finding_hosts",
    "ix_agent_api_calls_api_key_id": "agent_api_calls",
}
OLD_CHECK = "(agent_id IS NOT NULL) AND (project_id IS NOT NULL)"

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="walks real migrations; needs the Postgres the suite runs on",
)


def _run_alembic(url, *args: str) -> subprocess.CompletedProcess:
    """Run alembic against the scratch database, in its own process: env.py
    reads the URL from the settings, which this process has already loaded."""
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


@pytest.fixture(scope="module")
def scratch():
    """A database migrated by the chain to the revision before this one."""
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_a4f8"[:63])
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
INSERT INTO projects (id, name, slug, status) VALUES (1, 'Dups', 'dups', 'active'), (2, 'Other', 'other', 'active');
INSERT INTO users (id, username, hashed_password, role, is_active) VALUES
    (1, 'agent-owner', 'x', 'member', true), (2, 'someone-else', 'x', 'member', true);
INSERT INTO scopes (id, project_id) VALUES (10, 1), (20, 2);
INSERT INTO sites (id, project_id, name) VALUES (1, 1, 'London');
INSERT INTO hosts_v2 (id, ip_address, project_id) VALUES
    (1, '10.1.0.1', 1), (2, '10.1.0.2', 1), (3, '10.1.0.3', 1);
INSERT INTO subnets (id, scope_id, cidr, description, site, site_id) VALUES
    (100, 10, '10.1.0.0/24', NULL,   NULL,     NULL),
    (101, 10, '10.1.0.0/24', 'Core', 'London', 1),
    (102, 10, '10.1.0.0/24', '  ',   '',       NULL),
    (110, 10, '10.1.1.0/24', 'Once', NULL,     NULL),
    (120, 10, '10.1.2.0/24', 'Same', NULL,     NULL),
    (121, 10, '10.1.2.0/24', 'Same', NULL,     NULL),
    (200, 20, '10.1.0.0/24', 'Theirs', NULL,   NULL);
INSERT INTO host_subnet_mappings (host_id, subnet_id) VALUES
    (1, 100), (1, 101), (2, 101), (2, 102), (3, 102), (1, 110);
INSERT INTO subnet_labels (id, project_id, name) VALUES (1, 1, 'prod'), (2, 1, 'dmz');
INSERT INTO subnet_label_assignments (subnet_id, label_id) VALUES
    (100, 1), (101, 1), (102, 2);
INSERT INTO agents (id, name, project_id, owner_id, is_active) VALUES (1, 'their agent', 1, 1, true);
INSERT INTO agent_sessions (id, workflow, project_id, agent_id, started_by_id, status) VALUES
    (5, 'project', 1, 1, 1, 'ended');
INSERT INTO api_keys (id, name, key_hash, key_prefix, agent_id, agent_session_id) VALUES
    (1, 'session key', 'hash-1', 'nm_agent_x', 1, 5);
INSERT INTO agent_api_calls
    (id, agent_id, api_key_id, project_id, agent_session_id, method, path, status_code, duration_ms, error_class)
VALUES
    (500, 1,    1,    1,    5,    'GET', '/api/v1/agent/hosts', 200, 3, NULL),
    (501, NULL, NULL, NULL, NULL, 'GET', '/api/v1/agent/hosts', 500, 3, 'Boom');
"""


def _revision(conn) -> str:
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _subnets(conn) -> dict:
    return {
        r.id: (r.scope_id, r.cidr, r.description, r.site, r.site_id)
        for r in conn.execute(text("SELECT id, scope_id, cidr, description, site, site_id FROM subnets"))
    }


def _pairs(conn, table: str, other: str) -> list:
    return sorted(tuple(r) for r in conn.execute(text(f"SELECT subnet_id, {other} FROM {table}")))


def _indexes(conn) -> dict:
    return dict(conn.execute(text(
        "SELECT indexname, tablename FROM pg_indexes WHERE schemaname = current_schema()"
    )).all())


def _constraint(conn, name: str):
    return conn.execute(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"
    ), {"n": name}).scalar()


def _agent_fk_rule(conn) -> str:
    return conn.execute(text(
        "SELECT confdeltype::text FROM pg_constraint "
        "WHERE conrelid = 'agent_api_calls'::regclass AND contype = 'f' "
        "AND pg_get_constraintdef(oid) LIKE 'FOREIGN KEY (agent_id)%'"
    )).scalar()


def _columns(conn, table: str) -> set:
    return {
        r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
        ), {"t": table})
    }


def _refused(url) -> str:
    done = _run_alembic(url, "upgrade", REVISION)
    assert done.returncode != 0, "the upgrade should have been refused"
    return done.stdout + done.stderr


def _assert_untouched(conn, subnets_before: dict) -> None:
    """A refused upgrade changed nothing: one transaction, rolled back."""
    assert _revision(conn) == BEFORE
    assert _subnets(conn) == subnets_before
    assert _constraint(conn, "uq_subnets_scope_cidr") is None
    assert "ix_subnets_cidr" in _indexes(conn)
    assert not set(NEW_INDEXES) & set(_indexes(conn))
    assert _agent_fk_rule(conn) == "c"
    assert "totp_last_step" not in _columns(conn, "users")


MERGED = {
    # The lowest id of each group, with what a duplicate knew and it did not.
    100: (10, "10.1.0.0/24", "Core", "London", 1),
    110: (10, "10.1.1.0/24", "Once", None, None),
    120: (10, "10.1.2.0/24", "Same", None, None),
    # The same CIDR in another project's scope is not a duplicate.
    200: (20, "10.1.0.0/24", "Theirs", None, None),
}


def _assert_upgraded(conn) -> None:
    assert _revision(conn) == REVISION

    # ---- 1. one row per (scope, CIDR) ------------------------------------
    assert _subnets(conn) == MERGED
    # Every host of the three rows is mapped to the kept one, once.
    assert _pairs(conn, "host_subnet_mappings", "host_id") == [(100, 1), (100, 2), (100, 3), (110, 1)]
    # ... and every label, once.
    assert _pairs(conn, "subnet_label_assignments", "label_id") == [(100, 1), (100, 2)]
    assert _constraint(conn, "uq_subnets_scope_cidr") == "UNIQUE (scope_id, cidr)"
    indexes = _indexes(conn)
    assert "ix_subnets_cidr" not in indexes

    # ---- 2. the four indexes ----------------------------------------------
    assert {name: indexes.get(name) for name in NEW_INDEXES} == NEW_INDEXES

    # ---- 3. the call log --------------------------------------------------
    assert _agent_fk_rule(conn) == "n"
    assert _constraint(conn, "ck_agent_api_calls_attribution_or_error") == (
        "CHECK (((project_id IS NOT NULL) OR (error_class IS NOT NULL)))"
    )
    assert conn.execute(text("SELECT count(*) FROM agent_api_calls")).scalar() == 2

    # ---- 4. the last accepted step ----------------------------------------
    assert "totp_last_step" in _columns(conn, "users")
    assert conn.execute(text("SELECT count(*) FROM users WHERE totp_last_step IS NOT NULL")).scalar() == 0


def test_the_upgrade_merges_what_is_lossless_refuses_the_rest_and_comes_back(scratch):
    url, eng = scratch
    with eng.begin() as conn:
        conn.execute(text(SEED))
    with eng.connect() as conn:
        seeded = _subnets(conn)

    # ---- two rows of one CIDR that say different things --------------------
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO subnets (id, scope_id, cidr, description) VALUES "
            "(130, 10, '10.1.3.0/24', 'Printers'), (131, 10, '10.1.3.0/24', 'Cameras')"
        ))
    message = _refused(url)
    assert "scope 10: 10.1.3.0/24 (subnet ids [130, 131]; different descriptions)" in message
    assert "Nothing was changed" in message
    # The groups that CAN be merged are not named.
    assert "10.1.0.0/24" not in message and "10.1.2.0/24" not in message
    with eng.connect() as conn:
        _assert_untouched(conn, {
            **seeded,
            130: (10, "10.1.3.0/24", "Printers", None, None),
            131: (10, "10.1.3.0/24", "Cameras", None, None),
        })
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM subnets WHERE id IN (130, 131)"))

    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO subnets (id, scope_id, cidr, site) VALUES "
            "(140, 10, '10.1.4.0/24', 'East'), (141, 10, '10.1.4.0/24', 'West')"
        ))
    message = _refused(url)
    assert "scope 10: 10.1.4.0/24 (subnet ids [140, 141]; different sites)" in message
    with eng.connect() as conn:
        assert _revision(conn) == BEFORE and len(_subnets(conn)) == len(seeded) + 2
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM subnets WHERE id IN (140, 141)"))

    # ---- upgrade -------------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        _assert_upgraded(conn)

    # A second row for a CIDR of the scope is now impossible ...
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO subnets (scope_id, cidr) VALUES (10, '10.1.1.0/24')"))
    # ... and so is a call that names neither a project nor an error.
    with pytest.raises(IntegrityError):
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO agent_api_calls (method, path, status_code, duration_ms) "
                "VALUES ('GET', '/x', 200, 1)"
            ))

    # Deleting the account deletes its agent and key — and keeps the call.
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = 1"))
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM agents")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM api_keys")).scalar() == 0
        kept = conn.execute(text(
            "SELECT agent_id, api_key_id, project_id, agent_session_id, path "
            "FROM agent_api_calls WHERE id = 500"
        )).one()
        assert tuple(kept) == (None, None, 1, 5, "/api/v1/agent/hosts")
        assert tuple(conn.execute(text(
            "SELECT agent_id, started_by_id FROM agent_sessions WHERE id = 5"
        )).one()) == (None, None)

    # A call whose agent is still there, for the downgrade to keep.
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO agents (id, name, project_id, owner_id, is_active) VALUES (2, 'another', 1, 2, true)"
        ))
        conn.execute(text(
            "INSERT INTO agent_api_calls (id, agent_id, project_id, method, path, status_code, duration_ms) "
            "VALUES (502, 2, 1, 'GET', '/api/v1/agent/hosts', 200, 3)"
        ))

    # ---- downgrade -----------------------------------------------------------
    _alembic(url, "downgrade", BEFORE)
    with eng.connect() as conn:
        assert _revision(conn) == BEFORE
        assert "totp_last_step" not in _columns(conn, "users")
        indexes = _indexes(conn)
        assert not set(NEW_INDEXES) & set(indexes)
        assert indexes.get("ix_subnets_cidr") == "subnets"
        assert _constraint(conn, "uq_subnets_scope_cidr") is None
        # A merged subnet is not split again.
        assert _subnets(conn) == MERGED
        assert _agent_fk_rule(conn) == "c"
        assert OLD_CHECK in _constraint(conn, "ck_agent_api_calls_attribution_or_error")
        # The call whose agent is gone cannot be held by the old check: deleted,
        # as the old CASCADE would have.  The other two are kept.
        assert [r[0] for r in conn.execute(text("SELECT id FROM agent_api_calls ORDER BY id"))] == [501, 502]

    # The old schema allows what the new one refuses.
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO subnets (id, scope_id, cidr, description) VALUES (111, 10, '10.1.1.0/24', NULL)"
        ))
        conn.execute(text("INSERT INTO host_subnet_mappings (host_id, subnet_id) VALUES (2, 111)"))

    # ---- and up again ---------------------------------------------------------
    _alembic(url, "upgrade", REVISION)
    with eng.connect() as conn:
        assert _revision(conn) == REVISION
        assert _subnets(conn) == MERGED
        assert _pairs(conn, "host_subnet_mappings", "host_id") == [
            (100, 1), (100, 2), (100, 3), (110, 1), (110, 2),
        ]
        assert _agent_fk_rule(conn) == "n"
        assert set(NEW_INDEXES) <= set(_indexes(conn))
