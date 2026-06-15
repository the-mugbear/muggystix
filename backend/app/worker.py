"""Ingestion worker — polls PostgreSQL for queued jobs and parses them.

Run as:  ``python -m app.worker``

The worker uses ``SELECT … FOR UPDATE SKIP LOCKED`` to claim one job at a
time, ensuring serial parsing without deadlocks on the host deduplication
service's row-level locks.  It listens on the ``ingestion_jobs`` PG
notification channel so it wakes up immediately when a new upload arrives
instead of waiting for the next poll interval.

The LISTEN/poll/reconnect/heartbeat loop lives in ``app.worker_loop`` and is
shared with the report worker (``app.report_worker``); this module only wires
in the ingestion-specific pieces (the service, channel, and reaper).
"""

from __future__ import annotations

import logging
import os
import sys

from app import worker_loop
from app.services.ingestion_service import IngestionService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("app.worker")

# How long to wait (seconds) between polls when no PG notification arrives.
POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", "5"))

# Liveness file the container healthcheck reads — see worker_loop.write_heartbeat.
# Container-local /tmp (the healthcheck runs in-container; /tmp is writable by the
# non-root user). Keep in sync with the docker-compose worker healthcheck.
HEARTBEAT_PATH = os.getenv("WORKER_HEARTBEAT_PATH", "/tmp/bluestick_worker_heartbeat")


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
    worker_loop.install_signal_handlers(logger)

    # Ensure DB schema is up-to-date (same migrations the API runs on boot).
    from app.db.init import initialize_database
    initialize_database()

    # initialize_database() ran Alembic, which disabled our loggers
    # AND reset the root logger to WARNING.  Restore both before the
    # worker loop starts so its output is actually visible.  See
    # _restore_app_logging for the full story.
    _restore_app_logging()

    service = IngestionService()

    def _reap() -> None:
        reaped = service.reap_orphaned_jobs()
        if reaped:
            logger.info("Orphan reaper cleaned up %d stuck job(s)", reaped)

    def _check_backlog() -> None:
        # B2-3 — proactive WARNING when the queue is steadily backing up
        # (distinct from the reaper, which only sees wedged/failed jobs). One
        # worker process, so no leader election; per-callback exceptions are
        # isolated by run_listen_loop.
        from app.db.session import SessionLocal
        from app.services.queue_metrics_service import warn_if_ingestion_backlog_high

        with SessionLocal() as db:
            warn_if_ingestion_backlog_high(db)

    worker_loop.run_listen_loop(
        channel="ingestion_jobs",
        poll_one=service.poll_and_run_one,
        heartbeat_path=HEARTBEAT_PATH,
        logger=logger,
        poll_interval=POLL_INTERVAL,
        periodic=[_reap, _check_backlog],
    )
    logger.info("Ingestion worker stopped")


if __name__ == "__main__":
    main()
