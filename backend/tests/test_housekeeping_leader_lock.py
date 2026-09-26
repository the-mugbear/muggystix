"""The housekeeping leader lock is released by the connection that took it
(v2.420.0, production diagnostics 2026-09-26).

A session-level advisory lock belongs to a connection.  The loops took it,
ran work that commits — which hands a plain Session's connection back to the
pool — and unlocked on whatever connection came next: Postgres logged "you
don't own a lock of type ExclusiveLock" every hour and the lock stayed held.
"""
import pytest
from sqlalchemy import create_engine, text

from app import startup
from app.db import session as session_module

KEY = 0x42535F54455354  # "BS_TEST"


@pytest.fixture
def pooled_engine(db_session, monkeypatch):
    if db_session.bind.dialect.name != "postgresql":
        pytest.skip("advisory locks are Postgres-only")
    # A pooled engine, as production has — the bug needs connection reuse.
    engine = create_engine(db_session.bind.engine.url, pool_size=2, max_overflow=2)
    monkeypatch.setattr(session_module, "engine", engine)
    yield engine
    engine.dispose()


def _someone_else_can_lock(engine) -> bool:
    with engine.connect() as other:
        got = other.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": KEY}).scalar()
        if got:
            other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": KEY})
        other.commit()
        return bool(got)


def test_work_that_commits_keeps_the_lock_and_releases_it_after(pooled_engine):
    with startup._housekeeping_leader(KEY) as db:
        assert db is not None
        for _ in range(3):  # the purge commits after every batch
            db.execute(text("SELECT 1"))
            db.commit()
        assert not _someone_else_can_lock(pooled_engine), "the lock must be held during the pass"
    assert _someone_else_can_lock(pooled_engine), "the lock must be released after the pass"


def test_a_second_worker_skips_the_pass(pooled_engine):
    with startup._housekeeping_leader(KEY) as first:
        assert first is not None
        with startup._housekeeping_leader(KEY) as second:
            assert second is None
