"""Database initialisation — shared by the API process and the ingestion worker.

Schema management is owned by **Alembic** (see ``backend/alembic/``).  This
module's only job is to bring a database up to the current Alembic head on
process startup, retrying while the DB service boots and serializing the
upgrade across the API + worker processes.

History: the schema used to be built by ``Base.metadata.create_all`` plus a
~77-entry hand-rolled ``_MIGRATIONS`` list maintained right here.  That list
had no reversibility, no drift detection, and no history.  It was retired
once the Alembic ``baseline schema`` revision was verified to reproduce the
exact same schema (column-for-column) as the old create_all + migrations
path — the migrations were pure "catch an old DB up to the models" steps and
added nothing the models didn't already declare.

Waiting for the migration lock (review 2026-10-01 B1).  Several processes
start together (uvicorn workers, the ingestion worker, the report worker); one
migrates, the rest wait.  A waiter must hold NO snapshot while it waits: it
used to block inside ``SELECT pg_advisory_lock(...)``, an active statement,
and a ``CREATE INDEX CONCURRENTLY`` in the migrating process waits for every
older snapshot to end — while the waiters waited for the lock.  Postgres
cannot see that cycle (an advisory lock on one side, a snapshot wait on the
other), so boot hung for ever.  Waiters now POLL ``pg_try_advisory_lock`` on
an autocommit connection that is idle between tries.  See ``_migration_lock``.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from app.db.session import engine

logger = logging.getLogger(__name__)

_done = False

# Stable 64-bit key for the schema-migration advisory lock.  Arbitrary fixed
# constant — every process just has to agree on the same value.
_MIGRATION_LOCK_KEY = 738582901

# The first revision in the chain. A pre-Alembic database's schema equals THIS,
# not head — see _plan_for_tables.
_BASELINE_REVISION = "b46cd59c17f5"

# Tables the baseline creates. Their absence means whatever this database is,
# it isn't a BlueStick schema we can adopt.
_BASELINE_REQUIRED_TABLES = frozenset({"users", "hosts_v2", "scans", "ports_v2"})

# Tables introduced AFTER the baseline. If any exist while `alembic_version`
# does not, the database is not a clean pre-Alembic install — it's something
# stranger (a partial restore, a hand-edited schema, a dropped version table).
# Adopting it at the baseline would then re-run migrations against objects that
# already exist, so we refuse instead of guessing.
_POST_BASELINE_TABLES = frozenset(
    {"webhook_deliveries", "agent_sessions", "network_attributions"}
)


# How long a process waits for another one to finish migrating before it
# gives up (seconds).  Generous on purpose: a data migration on a large
# database is slow, and a waiter that exits early only gets restarted to wait
# again.  ``DB_MIGRATION_LOCK_TIMEOUT`` overrides it.
_LOCK_WAIT_SECONDS_DEFAULT = 3600.0
_LOCK_POLL_SECONDS = 0.5
_LOCK_LOG_EVERY_SECONDS = 15.0


class MigrationLockTimeout(RuntimeError):
    """Another process held the migration lock for longer than this one waits."""


def _say(level: int, msg: str) -> None:
    """Log ``msg`` so it reaches ``docker compose logs`` whatever Alembic did
    to logging.  ``command.upgrade`` runs Alembic's ``fileConfig``, which
    disables existing loggers — and any process may be the one that migrated —
    so warnings and errors also go straight to stderr."""
    if level >= logging.WARNING:
        print(f"{logging.getLevelName(level)}: {msg}", file=sys.stderr, flush=True)
    logger.disabled = False
    logger.log(level, msg)


def _lock_holder(conn) -> str:
    """Who holds the migration lock, for the waiting / timeout messages."""
    try:
        row = conn.execute(
            text(
                "SELECT a.pid, a.application_name, a.client_addr::text, "
                "       (now() - a.backend_start)::text "
                "FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
                "WHERE l.locktype = 'advisory' AND l.granted AND l.objsubid = 1 "
                "  AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
                "  AND ((l.classid::bigint << 32) | l.objid::bigint) = :k "
                "LIMIT 1"
            ),
            {"k": _MIGRATION_LOCK_KEY},
        ).first()
    except Exception:  # diagnostics only
        return "unknown"
    if row is None:
        return "nobody (it was just released)"
    return f"backend pid {row[0]} from {row[2] or 'local'}, connected {row[3]} ago"


@contextmanager
def _migration_lock(bind=None) -> Iterator[None]:
    """Hold the session-level migration advisory lock for the ``with`` body.

    Two properties matter, and ``tests/test_db_init_concurrent_boot.py`` pins
    both against real processes:

    * **A waiter holds no snapshot.**  It polls ``pg_try_advisory_lock`` —
      which returns at once — on an AUTOCOMMIT connection, so between tries
      the backend is ``idle`` with no ``backend_xmin``.  Never go back to the
      blocking ``pg_advisory_lock``: a statement that waits holds its snapshot
      for as long as it waits, and ``CREATE INDEX CONCURRENTLY`` in the
      process that IS migrating waits for exactly that.
    * **The holder holds no snapshot either.**  The lock is session-level, so
      the connection that owns it stays open, idle and outside a transaction
      for the whole upgrade (SQLAlchemy's default would leave it "idle in
      transaction").  Alembic migrates on a different connection.
    """
    bind = bind if bind is not None else engine
    wait_limit = float(os.getenv("DB_MIGRATION_LOCK_TIMEOUT", str(_LOCK_WAIT_SECONDS_DEFAULT)))
    conn = bind.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        started = time.monotonic()
        last_logged = started
        while not conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _MIGRATION_LOCK_KEY},
        ).scalar():
            now = time.monotonic()
            if now - started >= wait_limit:
                raise MigrationLockTimeout(
                    f"Waited {int(now - started)}s for another process to finish database "
                    f"migrations and it still holds the lock ({_lock_holder(conn)}). "
                    "Nothing was changed by this process. If a migration really is still "
                    "running, start this service again and it will wait again "
                    "(DB_MIGRATION_LOCK_TIMEOUT raises the wait); if the holder is stuck, "
                    "look at what it is waiting for in pg_stat_activity."
                )
            if now - last_logged >= _LOCK_LOG_EVERY_SECONDS:
                last_logged = now
                _say(
                    logging.WARNING,
                    "waiting for another process to finish database migrations "
                    f"({int(now - started)}s so far; lock held by {_lock_holder(conn)})",
                )
            time.sleep(_LOCK_POLL_SECONDS)
        waited = time.monotonic() - started
        if waited >= _LOCK_POLL_SECONDS:
            logger.info("Migration lock obtained after %.1fs", waited)
        try:
            yield
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _MIGRATION_LOCK_KEY})
            except Exception:
                # The lock dies with its session: make sure this connection is
                # closed for real rather than returned to the pool still
                # holding it.
                conn.invalidate()
                raise
    finally:
        conn.close()


def _invalid_indexes() -> list:
    """Indexes an interrupted ``CREATE INDEX CONCURRENTLY`` left INVALID.

    Postgres keeps such an index up to date on every write and never reads
    from it; ``CREATE INDEX IF NOT EXISTS`` skips it by name, so nothing in a
    later upgrade repairs it.  Best-effort: diagnostics must not stop a boot.
    """
    if engine.dialect.name != "postgresql":
        return []
    try:
        with engine.connect() as conn:
            return [r[0] for r in conn.execute(text(
                "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE NOT i.indisvalid AND n.nspname = current_schema() ORDER BY 1"
            ))]
    except Exception:
        return []


def _warn_about_invalid_indexes() -> None:
    names = _invalid_indexes()
    if names:
        _say(
            logging.WARNING,
            f"{len(names)} database index(es) are INVALID — left by an interrupted build; "
            "queries do not use them: " + ", ".join(names) + ". Rebuild each with "
            "REINDEX INDEX CONCURRENTLY <name>; (the application can stay up).",
        )


def _plan_for_tables(tables) -> str:
    """Decide how to bring this database to head. Pure, so it can be tested.

    Returns one of ``"upgrade"``, ``"adopt"``, ``"fail"``.
    """
    tables = set(tables)
    if "alembic_version" in tables:
        return "upgrade"
    if not tables:
        return "upgrade"
    if not _BASELINE_REQUIRED_TABLES <= tables:
        # Not empty, but not recognisably ours either. Let `upgrade head` run
        # and fail loudly rather than stamping something we don't understand.
        return "upgrade"
    if tables & _POST_BASELINE_TABLES:
        return "fail"
    return "adopt"


def _sync_schema_with_alembic() -> None:
    """Bring the schema to Alembic head.

    Three cases:

    * ``alembic_version`` table present — the DB is already Alembic-managed;
      ``upgrade head`` applies any revisions newer than the recorded one.
    * No ``alembic_version`` but core tables present — a pre-Alembic database
      whose schema already equals the baseline.  Adopt it with ``stamp head``
      rather than re-running the (non-idempotent) baseline migration against
      tables that already exist.
    * Empty database — ``upgrade head`` builds the schema from scratch.
    """
    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    # script_location in alembic.ini is relative to the ini file; pin it
    # absolute so this works no matter what CWD the process runs from.
    cfg.set_main_option("script_location", str(backend_root / "alembic"))

    tables = set(inspect(engine).get_table_names())
    started_at = _current_db_revision() if "alembic_version" in tables else None
    try:
        if "alembic_version" in tables:
            command.upgrade(cfg, "head")
        elif _plan_for_tables(tables) == "adopt":
            # Adopt at the BASELINE, then migrate forward.
            #
            # This used to `stamp head`, which asserts the database already
            # contains every migration in the chain. The docstring above says
            # the opposite — a pre-Alembic schema equals the BASELINE — so a
            # site upgrading directly from a pre-Alembic release was marked
            # up to date while missing every post-baseline column, table,
            # constraint and index. Nothing failed at boot; it broke later,
            # at the first query touching anything added since.
            logger.info(
                "Existing pre-Alembic schema detected — adopting it at baseline "
                "%s and migrating forward", _BASELINE_REVISION,
            )
            command.stamp(cfg, _BASELINE_REVISION)
            started_at = _BASELINE_REVISION  # the stamp changed no schema
            command.upgrade(cfg, "head")
        elif _plan_for_tables(tables) == "fail":
            raise RuntimeError(
                "Refusing to adopt this database: it has no alembic_version "
                "table, but it DOES contain post-baseline tables "
                f"({sorted(tables & _POST_BASELINE_TABLES)}). That is not a "
                "pre-Alembic install — it looks like a partial restore or a "
                "schema whose version table was dropped. Adopting it would "
                "re-run migrations against objects that already exist. "
                "Restore from a backup, or stamp the correct revision by hand."
            )
        else:
            logger.info("Empty database — building schema from Alembic head")
            command.upgrade(cfg, "head")
    except OperationalError:
        # DB not ready (connection refused / still starting) — let the caller's
        # retry loop handle it. NOT a migration defect.
        raise
    except Exception as exc:
        # The upgrade stopped, and not because the DB is unreachable. This is
        # NOT transient — every boot crash-loops here until it is resolved —
        # so surface ONE loud, actionable line instead of a buried Alembic
        # traceback, then re-raise so the process still exits non-zero. (B2-1)
        #
        # ``command.upgrade`` above ran Alembic's fileConfig, which disables
        # existing loggers (the same damage _restore_app_logging undoes in the
        # worker) — so by here our logger may be muted, and the process is
        # about to crash before anything restores it.  ``_say`` writes straight
        # to stderr as well, so the message ALWAYS reaches ``docker compose
        # logs``.
        _say(logging.CRITICAL, _failure_message(started_at, _current_db_revision(), exc))
        raise


def _failure_message(started_at: Optional[str], now_at: Optional[str], exc: BaseException) -> str:
    """What to tell the operator when the upgrade stopped.  Pure.

    ``alembic/env.py`` runs the whole upgrade in ONE transaction, so a
    revision that raises rolls back every revision before it in the same run:
    the database is left exactly where it started, which is a schema the
    previous build runs — nothing to restore.  That covers a revision that
    REFUSES on the data it finds (``b2e5a8c1d4f6`` and duplicate scanner
    findings: its own message says what to fix, on the previous build).

    The exception is a run that passed a revision which commits mid-chain (an
    ``autocommit_block`` — only the older concurrent-index revisions, the last
    of them ``b2e6d9f04a17``; ``c3f6b9d2e5a7`` no longer has one): then the
    schema did move, the previous build may not run on it, and going back
    means the backup.  Which case this is, is read from the database rather
    than guessed from the error.
    """
    if now_at == started_at:
        where = f"revision {started_at}" if started_at else "its starting state (no schema version)"
        return (
            "DATABASE MIGRATION FAILED — NOTHING WAS CHANGED: the upgrade was rolled back and "
            f"the schema is still at {where}, which the previous build runs. No database "
            "restore is needed. This is not a transient DB-readiness error, so the container "
            "will crash-loop until it is resolved: go back to the previous build "
            "(scripts/deploy.sh -> 'Roll back to previous build'; the schema did not move, "
            "so there is no database to restore), then read the underlying error — if it names data to correct, correct "
            "it on the previous build and upgrade again; otherwise it is a migration defect "
            f"to report. Underlying error: {exc}"
        )
    return (
        f"DATABASE MIGRATION FAILED — schema left PARTWAY at revision {now_at or 'unknown'} "
        f"(started at {started_at or 'an empty database'}, target: head). This is a migration "
        "defect, not a transient DB-readiness error, so the container will crash-loop until "
        "it is resolved. The previous build may not run on this schema: recover by rolling "
        "back to the previous build (scripts/deploy.sh -> 'Roll back to previous build') and "
        f"restoring the pre-deploy DB backup. Underlying error: {exc}"
    )


def _current_db_revision() -> Optional[str]:
    """Best-effort read of the DB's current Alembic revision, for diagnostics."""
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        return None


def initialize_database() -> None:
    """Bring the database schema to Alembic head, retrying while the DB boots.

    Safe to call multiple times (the ``_done`` guard makes every call after
    the first a no-op) and safe to call concurrently from the API and worker
    processes: on PostgreSQL a session-level advisory lock serializes the
    upgrade, so whichever process loses the race waits (idle, polling — see
    ``_migration_lock``) and then simply runs it as a no-op.

    ``_done`` is set only after a fully successful sync, so a failed attempt
    (retries exhausted, mid-migration crash) leaves it ``False`` and a later
    call in the same process can try again.
    """
    global _done
    if _done:
        return

    # v2.370.1 — ``app.main`` calls this at IMPORT time, so anything that
    # imports the app migrates whatever DATABASE_URL points at. The test suite
    # builds its own schema in its own ``<db>_test_<pid>`` database and never
    # needed that — but it ran with the working tree mounted, so an unmerged
    # revision was applied to the developer's real database (2026-09-20: a
    # data migration deleted rows before they had been exported). The test
    # harness sets this; nothing in a deployment should.
    if os.getenv("BLUESTICK_SKIP_DB_INIT") == "1":
        logger.warning(
            "BLUESTICK_SKIP_DB_INIT=1 — not migrating %s. Test harness only; "
            "a deployment must never set this.", engine.dialect.name,
        )
        return

    max_attempts = int(os.getenv("DB_INIT_MAX_RETRIES", "10"))
    backoff_seconds = float(os.getenv("DB_INIT_RETRY_DELAY", "3"))
    last_exc: OperationalError | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            if engine.dialect.name == "postgresql":
                # Serialize schema upgrades across processes.  A loser of the
                # race polls for the lock WITHOUT holding a snapshot (see
                # _migration_lock), then runs the sync as a no-op once the
                # winner has finished and released it.
                with _migration_lock():
                    _sync_schema_with_alembic()
                _warn_about_invalid_indexes()
            else:
                # SQLite / single-process dev — no cross-process race.
                _sync_schema_with_alembic()

            _done = True
            return
        except OperationalError as exc:
            last_exc = exc
            logger.warning(
                "Database not ready (attempt %d/%d): %s",
                attempt,
                max_attempts,
                exc,
            )
            time.sleep(backoff_seconds)

    raise RuntimeError("Database initialization failed after retries") from last_exc
