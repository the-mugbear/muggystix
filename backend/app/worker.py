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
import time

from app import worker_loop
# Register EVERY ORM model before the first query.  The migration used to run
# in this process (Alembic's env.py imports the registry), which hid that the
# worker's own imports do not reach every model module; it runs in its own step
# now, so the worker says it itself — as the report worker always has.
from app.db import model_registry  # noqa: F401
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


def main() -> None:
    worker_loop.install_signal_handlers(logger)

    # The schema is brought to head by the migration step the container runs
    # first (`python -m app.db.migrate`, docker-compose.yml's worker command),
    # in its own process. Nothing here runs Alembic, so its fileConfig no
    # longer disables this process's loggers (the old _restore_app_logging).

    service = IngestionService()
    from app.core.config import settings
    if settings.INGESTION_JOB_TIMEOUT_STILL_SET:
        logger.warning(
            "INGESTION_JOB_TIMEOUT is set (%ss) and INGESTION_STALL_TIMEOUT is not: it is read as the stall "
            "window — how long an import may go WITHOUT PROGRESS (an import that progresses is no longer "
            "stopped for taking long). Rename it to INGESTION_STALL_TIMEOUT in .env.",
            settings.INGESTION_STALL_TIMEOUT,
        )

    def _reap() -> None:
        reaped = service.reap_orphaned_jobs()
        if reaped:
            logger.info("Orphan reaper cleaned up %d stuck job(s)", reaped)
        # v2.352.0 — staged uploads nobody started expire after a day.
        try:
            from app.db.session import SessionLocal
            from app.services.staged_import_service import expire_staged_jobs
            from app.services.staged_import_service import expire_retained_files, sweep_orphan_upload_dirs
            with SessionLocal() as db:
                expired = expire_staged_jobs(db)
                # v2.354.0 — finished jobs' files past the retention window.
                removed = expire_retained_files(db)
                # v2.513.2 — upload folders no job names (a process that died
                # between the file and its row; review 2026-10-16, A4).
                orphans = sweep_orphan_upload_dirs(db)
            if expired:
                logger.info("Expired %d staged upload(s) never started", expired)
            if removed:
                logger.info("Removed %d retained upload file(s) past the retention window", removed)
            if orphans:
                logger.info("Removed %d upload folder(s) no import job names", orphans)
        except Exception:
            # WARNING, not DEBUG (the app logger is at INFO): a sweep that
            # keeps failing is how the uploads disk fills.
            logger.warning("Upload expiry / retention sweep failed", exc_info=True)

    def _check_backlog() -> None:
        # B2-3 — proactive WARNING when the queue is steadily backing up
        # (distinct from the reaper, which only sees wedged/failed jobs). One
        # worker process, so no leader election; per-callback exceptions are
        # isolated by run_listen_loop.
        from app.db.session import SessionLocal
        from app.services import operational_alerts
        from app.services.queue_metrics_service import warn_if_disk_low, warn_if_ingestion_backlog_high
        from app.services.resource_metrics import STARTED_AT, instance_name, worker_reports

        with SessionLocal() as db:
            # B-OPS-3 — what was a log line nobody tails is also in the
            # global admins' bell, once a day per kind.
            backlog = warn_if_ingestion_backlog_high(db)
            if backlog:
                operational_alerts.alert_ingestion_backlog(db, backlog)
            disk = warn_if_disk_low()
            if disk:
                operational_alerts.alert_disk_low(db, disk)
            operational_alerts.alert_silent_workers(
                db, instance_name(), worker_reports(), running_for=time.monotonic() - STARTED_AT,
            )
            db.commit()

    def _sweep_webhooks() -> None:
        # v2.233.0 — retry the durable webhook outbox. Rides this worker's tick
        # rather than a fourth container: the work is a handful of bounded HTTP
        # POSTs, and this process already owns the periodic-maintenance slot.
        # Batch-capped in the sweeper so dead receivers can't monopolise a tick.
        from app.db.session import SessionLocal
        from app.services.webhook_dispatcher import (
            prune_delivery_history, sweep_pending_deliveries,
        )

        with SessionLocal() as db:
            attempted = sweep_pending_deliveries(db)
            if attempted:
                logger.info("Retried %d pending webhook deliver(ies)", attempted)
            prune_delivery_history(db)

    last_alert_sweep = [0.0]

    def _remediation_alerts() -> None:
        # v2.461.0 — tell admins about findings entering the due-soon window
        # or passing their deadline.  A deadline is a day, so every quarter
        # of an hour is plenty; a no-op when the installation has the feature
        # off.  Each deadline alerts once (the row remembers), so a second
        # worker or a restart repeats nothing.
        if time.monotonic() - last_alert_sweep[0] < 900:
            return
        last_alert_sweep[0] = time.monotonic()
        from app.db.session import SessionLocal
        from app.services import remediation_alerts

        with SessionLocal() as db:
            written = remediation_alerts.sweep(db)
            # v2.462.0 — today's count per project, replaced on every sweep:
            # the history a derived deadline does not have.
            from app.services import remediation_service
            remediation_service.snapshot(db)
            db.commit()
        if written:
            logger.info("Raised %d remediation deadline notification(s)", written)

    # Before the first claim: a job this worker (or a peer that is gone) was
    # running when it died is known at once — nobody holds its attempt's
    # liveness lock — and is re-queued, so its partial scan is removed by the
    # very next claim instead of after the reaper's time window, with other
    # imports in between.  A job a live peer is running keeps its lock.
    try:
        _reap()
    except Exception:
        logger.exception("Orphan reap at start-up failed; the periodic reaper will retry")

    worker_loop.run_listen_loop(
        channel="ingestion_jobs",
        poll_one=service.poll_and_run_one,
        heartbeat_path=HEARTBEAT_PATH,
        logger=logger,
        poll_interval=POLL_INTERVAL,
        periodic=[_reap, _check_backlog, _sweep_webhooks, _remediation_alerts],
    )
    logger.info("Ingestion worker stopped")


if __name__ == "__main__":
    main()
