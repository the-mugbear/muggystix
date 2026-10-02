"""Boot migrations with several processes starting at once (review 2026-10-01 B1).

Production starts four uvicorn workers, the ingestion worker and the report
worker together, and each calls ``initialize_database()``.  One takes the
migration lock and migrates; the others wait.  Two things went wrong:

* A waiter sat inside ``SELECT pg_advisory_lock(...)`` — an ACTIVE statement,
  which holds a snapshot.  ``CREATE INDEX CONCURRENTLY`` waits for every older
  snapshot to end, and the waiters were waiting for the lock the migrating
  process releases only when it is done.  Postgres sees no cycle (one side is
  an advisory lock, the other a snapshot wait), so nothing ever broke it.
* ``c3f6b9d2e5a7`` built its indexes concurrently in an autocommit block,
  which also committed every earlier revision mid-chain: the hang left the
  schema past what the previous build can run.

These tests start five real processes against a scratch database BUILT BY THE
CHAIN and require all of them to finish, the database to be at head and no
index to be left invalid.  Postgres only.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.conftest import USING_POSTGRES, _drop_database, _ensure_database, engine as suite_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BEFORE_INDEXES = "b2e5a8c1d4f6"
INDEX_REVISION = "c3f6b9d2e5a7"
BEFORE_REFUSAL = "a1d4f7b9c2e3"
PROCESSES = 5
#: Generous: five processes from an empty database take ~10 s here.  The
#: defect this guards against never finishes at all.
BOOT_BOUND_SECONDS = 90

READ_PATH_INDEXES = {
    "ix_vulnerabilities_issue_key", "ix_host_tests_agent_session_id", "ix_host_tests_name_id",
    "ix_agent_proposals_finding_host_id", "ix_agent_proposals_result_finding_id",
    "ix_evidence_records_finding_host_id", "idx_port_scan_history_scan_state",
    "ix_trgm_port_service_name", "ix_trgm_port_service_product", "ix_trgm_port_product_version",
}

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES, reason="starts real processes against real migrations; needs Postgres",
)

_BOOT = "from app.db.init import initialize_database; initialize_database()"


def _env(url, **extra: str) -> dict:
    env = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False), **extra}
    return env


def _alembic(url, *args: str) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT, env=_env(url, BLUESTICK_SKIP_DB_INIT="1"), capture_output=True, text=True,
    )
    assert done.returncode == 0, f"alembic {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"


def _start_boot(url) -> subprocess.Popen:
    """One process doing what ``app.main`` / ``app.worker`` do at import."""
    env = _env(url, DB_INIT_MAX_RETRIES="2", DB_INIT_RETRY_DELAY="0.2")
    env.pop("BLUESTICK_SKIP_DB_INIT", None)  # the suite sets it; a deployment never does
    return subprocess.Popen(
        [sys.executable, "-c", _BOOT], cwd=BACKEND_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _finish(procs, bound: float) -> list:
    """Wait for every process; returns ``[(returncode | None, output)]`` —
    ``None`` for one that had to be killed at the bound."""
    deadline = time.monotonic() + bound
    results = [None] * len(procs)

    def drain(i, proc):
        try:
            out, _ = proc.communicate(timeout=max(0.1, deadline - time.monotonic()))
            results[i] = (proc.returncode, out)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
            results[i] = (None, out)

    threads = [threading.Thread(target=drain, args=(i, p)) for i, p in enumerate(procs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


@pytest.fixture
def scratch(request):
    """An empty scratch database on the suite's server; dropped afterwards
    (which also evicts anything a failing test left hanging in it)."""
    base = make_url(suite_engine.url.render_as_string(hide_password=False))
    url = base.set(database=f"{base.database}_boot"[:63])
    _drop_database(url)
    _ensure_database(url)
    eng = create_engine(url)
    try:
        yield url, eng
    finally:
        eng.dispose()
        _drop_database(url)


def _revision(eng) -> str:
    with eng.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _head(url) -> str:
    done = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=BACKEND_ROOT, env=_env(url, BLUESTICK_SKIP_DB_INIT="1"), capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    heads = [line.split()[0] for line in done.stdout.splitlines() if line.strip()]
    assert len(heads) == 1, f"more than one head: {heads}"
    return heads[0]


def _invalid_indexes(eng) -> list:
    with eng.connect() as conn:
        return [r[0] for r in conn.execute(text(
            "SELECT indexrelid::regclass::text FROM pg_index WHERE NOT indisvalid"
        ))]


def _assert_all_booted(results, eng, url, elapsed: float) -> None:
    hung = [i for i, (rc, _out) in enumerate(results) if rc is None]
    assert not hung, (
        f"{len(hung)} of {len(results)} processes were still running after "
        f"{BOOT_BOUND_SECONDS}s — the boot migration hung.\n" + results[hung[0]][1][-2000:]
    )
    failed = [(rc, out) for rc, out in results if rc != 0]
    assert not failed, f"a process failed to boot (rc={failed[0][0]}):\n{failed[0][1][-3000:]}"
    assert _revision(eng) == _head(url)
    assert _invalid_indexes(eng) == []
    print(f"\n{len(results)} processes booted in {elapsed:.1f}s")


@pytest.mark.parametrize("start", [BEFORE_INDEXES, None], ids=["upgrade-path", "empty-database"])
def test_five_processes_starting_together_all_reach_head(scratch, start):
    """``upgrade-path`` is what a production upgrade across ``c3f6b9d2e5a7``
    does; ``empty-database`` walks the OLDER concurrent-index revisions, which
    hung the same way."""
    url, eng = scratch
    if start:
        _alembic(url, "upgrade", start)
    began = time.monotonic()
    procs = [_start_boot(url) for _ in range(PROCESSES)]
    results = _finish(procs, BOOT_BOUND_SECONDS)
    _assert_all_booted(results, eng, url, time.monotonic() - began)
    with eng.connect() as conn:
        present = {r[0] for r in conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        ))}
    assert READ_PATH_INDEXES <= present


def test_a_process_waiting_for_the_migration_lock_holds_no_snapshot(scratch):
    """The waiter's connection must be idle between tries: a statement that
    blocks in ``pg_advisory_lock`` holds a snapshot for as long as it waits,
    and that is what a concurrent index build waits for."""
    import app.db.init as dbinit

    url, eng = scratch
    _alembic(url, "upgrade", "head")
    holder = eng.connect().execution_options(isolation_level="AUTOCOMMIT")
    proc = None
    try:
        assert holder.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": dbinit._MIGRATION_LOCK_KEY},
        ).scalar() is True
        proc = _start_boot(url)
        samples = []
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as watch:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                samples.append(watch.execute(text(
                    "SELECT state, backend_xmin IS NOT NULL, wait_event_type, query "
                    "FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() AND pid <> :holder AND backend_type = 'client backend'"
                ), {"holder": holder.execute(text("SELECT pg_backend_pid()")).scalar()}).all())
                time.sleep(0.2)
        assert proc.poll() is None, "the waiter did not wait for the lock:\n" + (proc.stdout.read() or "")
        waiting = [rows for rows in samples if rows]
        assert waiting, "the waiter never connected"
        blocked = [r for rows in waiting for r in rows if r[2] == "Lock"]
        assert not blocked, f"a waiter is blocked inside a statement (it holds a snapshot): {blocked[0]}"
        # Seen idle, with no snapshot, in (nearly) every sample: a poll is a
        # sub-millisecond statement, the wait between polls is the rest.
        idle = [rows for rows in waiting if all(r[0] == "idle" and not r[1] for r in rows)]
        assert len(idle) >= len(waiting) - 2, f"waiter not idle between tries: {waiting[-1]}"
    finally:
        holder.execute(text("SELECT pg_advisory_unlock_all()"))
        holder.close()
        if proc is not None:
            (rc, out), = _finish([proc], 30)
    assert rc == 0, f"the waiter did not proceed once the lock was free (rc={rc}):\n{out[-2000:]}"


def test_the_migrating_process_holds_no_snapshot_on_its_lock_connection(scratch):
    """The connection that HOLDS the advisory lock is open for the whole
    upgrade.  Left "idle in transaction" it could itself hold back a concurrent
    index build running on alembic's connection."""
    import app.db.init as dbinit

    url, eng = scratch
    lock_engine = create_engine(url)
    try:
        with dbinit._migration_lock(lock_engine):
            with eng.connect() as conn:
                rows = conn.execute(text(
                    "SELECT a.state, a.backend_xmin IS NOT NULL, a.backend_xid IS NOT NULL "
                    "FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
                    "WHERE l.locktype = 'advisory' AND l.granted AND a.datname = current_database()"
                )).all()
            assert rows == [("idle", False, False)], rows
        with eng.connect() as conn:
            assert conn.execute(text(
                "SELECT count(*) FROM pg_locks l JOIN pg_database d ON d.oid = l.database "
                "WHERE l.locktype = 'advisory' AND d.datname = current_database()"
            )).scalar() == 0, "the migration lock was not released"
    finally:
        lock_engine.dispose()


def test_a_waiter_gives_up_at_its_bound_and_names_the_holder(scratch, monkeypatch):
    import app.db.init as dbinit

    url, eng = scratch
    monkeypatch.setenv("DB_MIGRATION_LOCK_TIMEOUT", "1")
    lock_engine = create_engine(url)
    holder = eng.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": dbinit._MIGRATION_LOCK_KEY})
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar()
        began = time.monotonic()
        with pytest.raises(dbinit.MigrationLockTimeout) as caught:
            with dbinit._migration_lock(lock_engine):
                pytest.fail("entered the body without the lock")
        assert 1 <= time.monotonic() - began < 10
        assert f"backend pid {holder_pid}" in str(caught.value)
        # Not an OperationalError: the "database not ready" retry loop must
        # not swallow it and start the wait again.
        from sqlalchemy.exc import OperationalError
        assert not isinstance(caught.value, OperationalError)
    finally:
        holder.close()
        lock_engine.dispose()


def _interrupted_concurrent_build(eng, name: str, table: str, column: str) -> None:
    """Leave ``name`` the way an interrupted ``CREATE INDEX CONCURRENTLY``
    does: present and INVALID.  An open snapshot makes the build wait, and a
    statement timeout cancels it there — the production failure in small."""
    snapshot = eng.connect().execution_options(isolation_level="REPEATABLE READ")
    try:
        snapshot.execute(text("SELECT 1")).scalar()  # takes the snapshot
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("SET statement_timeout = 1500"))
            with pytest.raises(Exception, match="statement timeout"):
                conn.execute(text(f"CREATE INDEX CONCURRENTLY {name} ON {table} ({column})"))
    finally:
        snapshot.close()


def test_an_invalid_index_left_by_an_interrupted_build_is_rebuilt(scratch):
    """``IF NOT EXISTS`` alone skips an INVALID index for ever while alembic
    stamps the revision done — the planner never uses it."""
    url, eng = scratch
    _alembic(url, "upgrade", BEFORE_INDEXES)
    _interrupted_concurrent_build(eng, "ix_vulnerabilities_issue_key", "vulnerabilities", "issue_key")
    # A trigram name too, left as a plain b-tree: rebuilt as what it should be.
    _interrupted_concurrent_build(eng, "ix_trgm_port_service_name", "ports_v2", "service_name")
    assert sorted(_invalid_indexes(eng)) == ["ix_trgm_port_service_name", "ix_vulnerabilities_issue_key"]

    _alembic(url, "upgrade", INDEX_REVISION)
    assert _invalid_indexes(eng) == []
    with eng.connect() as conn:
        defs = dict(conn.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY(:n)"
        ), {"n": sorted(READ_PATH_INDEXES)}).all())
    assert set(defs) == READ_PATH_INDEXES
    assert "gin_trgm_ops" in defs["ix_trgm_port_service_name"]

    # Where it already ran (every valid index is there): running it again
    # changes nothing and does not fail.
    _alembic(url, "stamp", BEFORE_INDEXES)
    _alembic(url, "upgrade", INDEX_REVISION)
    assert _invalid_indexes(eng) == []
    with eng.connect() as conn:
        assert dict(conn.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY(:n)"
        ), {"n": sorted(READ_PATH_INDEXES)}).all()) == defs

    # And the downgrade is real.
    _alembic(url, "downgrade", BEFORE_INDEXES)
    with eng.connect() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY(:n)"
        ), {"n": sorted(READ_PATH_INDEXES)}).scalar() == 0


def test_an_upgrade_that_refuses_leaves_the_database_where_it_started_and_says_so(scratch):
    """``b2e5a8c1d4f6`` refuses to run while an issue has two scanner
    findings.  The whole upgrade is one transaction, so the database stays at
    the revision the previous build runs — and the boot message must say that,
    not "restore the backup"."""
    url, eng = scratch
    _alembic(url, "upgrade", BEFORE_REFUSAL)
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO projects (id, name, slug, status) VALUES (1, 'P', 'p', 'active')"))
        conn.execute(text(
            "INSERT INTO findings (project_id, title, severity, status, source, dedup_key) VALUES "
            "(1, 'one', 'high', 'open', 'scanner', 'nessus:1'), (1, 'two', 'high', 'open', 'scanner', 'nessus:1')"
        ))
    (rc, out), = _finish([_start_boot(url)], BOOT_BOUND_SECONDS)
    assert rc not in (0, None), out[-2000:]
    assert _revision(eng) == BEFORE_REFUSAL
    assert "NOTHING WAS CHANGED" in out and BEFORE_REFUSAL in out, out[-3000:]
    assert "more than one scanner finding" in out
    assert "restoring the pre-deploy DB backup" not in out
    assert "No database restore is needed" in out
    with eng.connect() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_finding_scanner_issue'"
        )).scalar() == 0
