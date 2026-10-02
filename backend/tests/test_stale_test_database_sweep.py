"""The suite's own leftovers: ``conftest._sweep_stale_test_databases``.

A hard-killed run never drops its ``<app-db>_test_<host>_<pid>`` database, and
nothing else did (41 of them on the development server by 2026-10-01).  The
sweep at session start removes them — and this pins how little else it may
touch: only the suite's own name shape, only databases older than a day, only
ones nobody is connected to.

The test works under a made-up "application database" name of its own, so the
prefix it sweeps (``sweepcheck<pid>_test_``) can match nothing but the
databases created here — never another run's live test database.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from tests import conftest
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(not USING_POSTGRES, reason="needs the PostgreSQL test server")


def test_only_old_idle_databases_of_the_suites_own_shape_are_dropped(monkeypatch):
    fake_app = f"sweepcheck{os.getpid()}"
    fake_url = make_url(settings.DATABASE_URL).set(database=fake_app)
    stale = f"{fake_app}_test_abc123def456_15"       # the suite's shape
    busy = f"{fake_app}_test_fedcba654321_16"        # the suite's shape, someone connected
    named_by_a_person = f"{fake_app}_test_mine"      # no <host>_<pid>
    plain = f"{fake_app}_test"                       # the old shared name
    everything = [stale, busy, named_by_a_person, plain, fake_app]

    admin = create_engine(fake_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    busy_engine = None
    try:
        with admin.connect() as conn:
            superuser = conn.execute(
                text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            ).scalar()
            for name in everything:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
        busy_engine = create_engine(fake_url.set(database=busy))
        held = busy_engine.connect()

        def existing():
            with admin.connect() as conn:
                return set(conn.execute(
                    text("SELECT datname FROM pg_database WHERE left(datname, :n) = :p"),
                    {"n": len(fake_app), "p": fake_app},
                ).scalars())

        # Created a moment ago: far younger than a day, so nothing goes.
        assert conftest._sweep_stale_test_databases(fake_url) == []
        assert existing() == set(everything)

        if not superuser:
            pytest.skip("the test role cannot read file times; the sweep is a no-op for it")

        # With the age limit out of the way, exactly one qualifies.
        monkeypatch.setattr(conftest, "_STALE_TEST_DB_AGE", "0 seconds")
        assert conftest._sweep_stale_test_databases(fake_url) == [stale]
        assert existing() == set(everything) - {stale}
        held.close()
    finally:
        if busy_engine is not None:
            busy_engine.dispose()
        with admin.connect() as conn:
            for name in everything:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
