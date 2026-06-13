"""Ingestion worker — polls PostgreSQL for queued jobs and parses them.

Run as:  ``python -m app.worker``

The worker uses ``SELECT … FOR UPDATE SKIP LOCKED`` to claim one job at a
time, ensuring serial parsing without deadlocks on the host deduplication
service's row-level locks.  It listens on the ``ingestion_jobs`` PG
notification channel so it wakes up immediately when a new upload arrives
instead of waiting for the next poll interval.
"""

from __future__ import annotations

import logging
import os
import select
import signal
import sys
import time

from app.db.session import engine, SessionLocal
from app.services.ingestion_service import IngestionService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("app.worker")

# How long to wait (seconds) between polls when no PG notification arrives.
POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", "5"))

# Liveness file the container healthcheck reads.  The previous healthcheck only
# grep'd /proc for the process, so a worker whose loop had wedged or crashed but
# whose process was still alive read "healthy" (the failure mode behind the
# 24h-silent-worker incident).  The main loop rewrites this file every cycle, so
# a stalled loop lets it go stale and the healthcheck flips unhealthy.
# Container-local (not the uploads volume): the healthcheck runs inside the
# container, /tmp is always writable by the worker's non-root user, and the
# file needn't persist across restarts.  Keep in sync with the docker-compose
# worker healthcheck (same WORKER_HEARTBEAT_PATH default).
HEARTBEAT_PATH = os.getenv("WORKER_HEARTBEAT_PATH", "/tmp/bluestick_worker_heartbeat")

_shutdown = False


def _write_heartbeat() -> None:
    """Rewrite the liveness file (mtime is what the healthcheck checks).

    Called from the MAIN loop, not a daemon thread — a thread would keep
    ticking even if the loop wedged, defeating the point.  Written between
    jobs and after each job; a single job that legitimately runs longer than
    the healthcheck timeout will briefly read stale, but an unhealthy worker
    is only *surfaced*, never restarted (Docker restarts on process exit), so
    that window is visible, not disruptive — mirrors the API /health design."""
    try:
        with open(HEARTBEAT_PATH, "w") as fh:
            fh.write(str(time.time()))
    except OSError as exc:
        logger.warning("Could not write worker heartbeat to %s: %s", HEARTBEAT_PATH, exc)


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown
    logger.info("Received signal %s — shutting down after current job", signum)
    _shutdown = True


# Reconnect backoff settings.  Exponential backoff capped so we don't
# stall ingestion for minutes if Postgres has a brief blip.
_RECONNECT_INITIAL_DELAY = 1.0
_RECONNECT_MAX_DELAY = 30.0


def _open_listen_connection():
    """Open a fresh raw DB connection in LISTEN mode and return it.

    Isolation level 0 = autocommit, which is required for LISTEN to
    work without an explicit transaction.
    """
    raw_conn = engine.raw_connection()
    raw_conn.set_isolation_level(0)
    cur = raw_conn.cursor()
    cur.execute("LISTEN ingestion_jobs")
    return raw_conn


def _listen_loop(service: IngestionService) -> None:
    """Main worker loop: LISTEN for notifications, poll on timeout.

    Wrapped in an outer reconnect loop so a Postgres restart, network
    blip, or stale connection doesn't permanently kill the worker.
    On any DB-related exception inside the inner loop we close the
    bad connection, log, sleep with exponential backoff, and try
    again.  Cooperative shutdown via _shutdown is honored at every
    sleep checkpoint so SIGTERM still drains in <1s.
    """
    backoff = _RECONNECT_INITIAL_DELAY
    while not _shutdown:
        raw_conn = None
        try:
            raw_conn = _open_listen_connection()
            logger.info(
                "Ingestion worker connected — polling every %.0fs, listening for notifications",
                POLL_INTERVAL,
            )
            # Reset backoff on every successful connect so transient
            # blips don't permanently inflate the recovery delay.
            backoff = _RECONNECT_INITIAL_DELAY

            reap_tick = 0
            while not _shutdown:
                # Liveness: the loop is cycling.  Refreshed again after each job
                # so a backlog drain keeps the heartbeat fresh between jobs.
                _write_heartbeat()
                # Process any queued jobs first (there may be several).
                while not _shutdown:
                    try:
                        did_work = service.poll_and_run_one()
                    except Exception:
                        logger.exception("Unexpected error in poll_and_run_one")
                        did_work = False
                    _write_heartbeat()
                    if not did_work:
                        break

                # Every ~12 idle ticks (~1 minute at the default 5s
                # poll), sweep for orphaned 'processing' jobs that
                # outlived their parser.  Cheap — indexed lookup with
                # a time cutoff.  Runs on the same worker that would
                # process new jobs so we don't need a second daemon.
                reap_tick = (reap_tick + 1) % 12
                if reap_tick == 0:
                    try:
                        reaped = service.reap_orphaned_jobs()
                        if reaped:
                            logger.info("Orphan reaper cleaned up %d stuck job(s)", reaped)
                    except Exception:
                        logger.exception("Unexpected error in orphan reaper")

                if _shutdown:
                    break

                # Wait for a PG notification or timeout.  select.select
                # raises OSError if the socket is dead, which we let
                # bubble out to the reconnect handler below.
                if select.select([raw_conn], [], [], POLL_INTERVAL) == ([], [], []):
                    # Timeout — no notification; loop will poll again.
                    pass
                else:
                    # Drain all pending notifications.  raw_conn.poll()
                    # raises psycopg2.OperationalError if the
                    # connection has been closed by the server.
                    raw_conn.poll()
                    while raw_conn.notifies:
                        raw_conn.notifies.pop(0)

        except Exception as exc:
            # Catches:
            #   - psycopg2.OperationalError (server closed conn)
            #   - select.error / OSError (socket died)
            #   - SQLAlchemy DBAPIError surfaced from raw connection
            # Anything that means "reconnect or die" lands here.
            logger.error(
                "Ingestion worker DB connection lost (%s: %s) — reconnecting in %.1fs",
                type(exc).__name__, exc, backoff,
            )
        finally:
            if raw_conn is not None:
                try:
                    raw_conn.close()
                except Exception:
                    pass

        if _shutdown:
            break

        # Sleep in 1s slices so SIGTERM still drains promptly.
        slept = 0.0
        while slept < backoff and not _shutdown:
            time.sleep(min(1.0, backoff - slept))
            slept += 1.0

        backoff = min(backoff * 2.0, _RECONNECT_MAX_DELAY)


def _restore_app_logging() -> None:
    """Undo the logging damage done by Alembic's fileConfig().

    v2.45.3 — ``initialize_database()`` runs Alembic migrations, and
    Alembic's ``fileConfig(config_file_name)`` does TWO things that
    silently blackhole the worker's logs (confirmed empirically — a
    fresh worker process showed ``app.worker disabled=True`` and the
    root logger flipped to a stderr handler at WARNING level after
    ``initialize_database()``):

      1. ``disable_existing_loggers=True`` (the fileConfig default)
         marks every logger that already existed — ``app.worker``
         created at this module's import, plus any ``app.services.*``
         imported before ``main()`` ran — as ``disabled``.  Disabled
         loggers drop records BEFORE the handler check.

      2. fileConfig REPLACES the root logger's config from alembic.ini,
         which sets root to WARNING.  Even after we clear ``disabled``,
         ``app.worker`` is level NOTSET and inherits that WARNING
         threshold — so every ``logger.info(...)`` in the worker loop
         is filtered out below level.

    Both together meant the worker ran for 24h emitting nothing — not
    its "connected" line, not the traceback behind its exit-255 (the
    incident that surfaced this bug).

    Fix BOTH: clear ``disabled`` on ``app`` + every ``app.*`` logger,
    AND pin the ``app`` logger to INFO so the whole ``app.*`` subtree
    has an INFO effective level regardless of what alembic.ini did to
    root.  Records still propagate to root's handler (alembic's stderr
    StreamHandler — docker captures stderr fine), so no handler needs
    to be re-added.  Mirrors the FastAPI-side fix in ``main.py``'s
    startup_event; the worker never runs that path so it needs its own.
    """
    reenabled = 0
    for name, lg in logging.Logger.manager.loggerDict.items():
        if isinstance(lg, logging.Logger) and (
            name == "app" or name.startswith("app.")
        ):
            if getattr(lg, "disabled", False):
                lg.disabled = False
                reenabled += 1
    app_logger = logging.getLogger("app")
    app_logger.disabled = False
    # Pin the subtree's effective level to INFO — overrides the
    # WARNING that fileConfig left on root.
    app_logger.setLevel(logging.INFO)
    logger.info(
        "Restored app.* logging after Alembic fileConfig "
        "(re-enabled %d logger(s), pinned app subtree to INFO) — "
        "worker output will now reach docker logs",
        reenabled,
    )


def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Write an initial heartbeat before migrations so the file exists from the
    # start (the healthcheck's start_period covers the migration window); if
    # migrations themselves hang, the heartbeat goes stale and that shows.
    _write_heartbeat()

    # Ensure DB schema is up-to-date (same migrations the API runs on boot).
    from app.db.init import initialize_database
    initialize_database()

    # initialize_database() ran Alembic, which disabled our loggers
    # AND reset the root logger to WARNING.  Restore both before the
    # worker loop starts so its output is actually visible.  See
    # _restore_app_logging for the full story.
    _restore_app_logging()

    service = IngestionService()
    _listen_loop(service)
    logger.info("Ingestion worker stopped")


if __name__ == "__main__":
    main()
