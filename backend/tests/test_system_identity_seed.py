"""Regression: first boot must produce exactly one deployment identity.

`system_identity` is documented as "one row per instance", but only
`instance_id` is unique — the primary key is a plain autoincrement, so nothing
stopped two of the four production Uvicorn workers from both observing an empty
table and inserting *different* UUIDs. Reads then used an unordered `.first()`,
so different workers could answer with different identities.

That defeats the only thing the table exists for: an agent verifying that the
instance it is talking to is the one that issued its instructions. A check that
can disagree with itself is worse than no check.

v2.298.0 serializes the seed on a Postgres advisory lock and re-reads under it,
mirroring `seed_default_admin`.
"""
import threading

import pytest
from sqlalchemy import text

from app.db.models_auth import SystemIdentity
from app.startup import seed_system_identity


def _is_postgres(db) -> bool:
    return db.bind.dialect.name == "postgresql"


def test_seed_is_idempotent(db_session):
    seed_system_identity()
    seed_system_identity()
    rows = db_session.query(SystemIdentity).all()
    assert len(rows) == 1


def test_concurrent_workers_agree_on_one_identity(db_session):
    """The actual race: four workers booting against an empty table.

    Threads stand in for the four Uvicorn workers, but the lock is held by the
    *connection*, so each thread must have its own. The conftest deliberately
    rebinds ``SessionLocal`` to the single savepoint-wrapped test connection so
    middleware writes land in the test transaction — which would make every
    thread share one connection and test nothing. So this test restores a real
    sessionmaker bound to the test ENGINE for its duration, and cleans up the
    rows it commits for real afterwards.
    """
    if not _is_postgres(db_session):
        pytest.skip("advisory locks are a Postgres mechanism; sqlite runs single-process")

    from sqlalchemy.orm import sessionmaker
    import app.db.session as session_module
    from tests.conftest import engine

    real_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    patched = session_module.SessionLocal
    session_module.SessionLocal = real_session_local

    def _wipe():
        with real_session_local() as s:
            s.query(SystemIdentity).delete()
            s.commit()

    barrier = threading.Barrier(4)
    errors = []

    def boot():
        try:
            barrier.wait(timeout=10)  # maximise the overlap
            seed_system_identity()
        except Exception as exc:  # pragma: no cover - surfaced via assert below
            errors.append(exc)

    try:
        _wipe()
        threads = [threading.Thread(target=boot) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == [], f"seeding raised under concurrency: {errors}"
        with real_session_local() as s:
            rows = s.query(SystemIdentity).all()
            ids = [r.instance_id for r in rows]
        assert len(rows) == 1, (
            f"{len(rows)} identities created concurrently: {ids} — "
            "every worker must agree on one"
        )
    finally:
        _wipe()
        session_module.SessionLocal = patched


def test_readers_are_deterministic_when_a_deployment_already_raced(db_session):
    """A deployment that raced BEFORE the fix still has two rows. The readers
    order by id so at least every worker names the same one, rather than the
    answer depending on whatever the planner returned."""
    db_session.query(SystemIdentity).delete()
    db_session.commit()
    db_session.add_all([
        SystemIdentity(instance_id="aaaa-first"),
        SystemIdentity(instance_id="bbbb-second"),
    ])
    db_session.commit()

    ordered = db_session.query(SystemIdentity).order_by(SystemIdentity.id).first()
    assert ordered.instance_id == "aaaa-first"

    # The seed must not add a third when rows already exist.
    seed_system_identity()
    assert db_session.query(SystemIdentity).count() == 2


def test_seed_takes_the_advisory_lock_on_postgres(db_session):
    """Structural: the lock is what makes the re-read safe, so a refactor that
    drops it must fail here rather than silently reopening the race."""
    if not _is_postgres(db_session):
        pytest.skip("Postgres-only mechanism")
    import inspect

    source = inspect.getsource(seed_system_identity)
    assert "pg_advisory_xact_lock" in source
    # The re-read has to happen AFTER the lock, or the lock buys nothing.
    assert source.index("pg_advisory_xact_lock") < source.index("query(SystemIdentity)")
