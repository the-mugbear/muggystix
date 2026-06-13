"""Report worker — generates async report jobs (PDF / JSON / zip bundles).

Run as:  ``python -m app.report_worker``

A dedicated background worker so heavy report rendering (WeasyPrint PDF, large
ZIP bundles) never competes with ingestion on the same process.  It claims
queued ``report_jobs`` via ``SELECT … FOR UPDATE SKIP LOCKED``, writes the
artifact to the shared report-artifacts dir, and the backend's download endpoint
streams it.  Shares the LISTEN/poll/reconnect/heartbeat loop with the ingestion
worker (``app.worker_loop``).

Unlike the ingestion worker, this one does NOT run migrations — the backend +
ingestion worker own that.  This worker only consumes the ``report_jobs`` table
once it exists; on a cold first boot the loop tolerates the not-yet-migrated DB
(it logs and retries until the table appears).
"""
from __future__ import annotations

import logging
import os
import sys

from app import worker_loop
from app.services.report_job_service import ReportJobService

# Register EVERY ORM model so SQLAlchemy can configure the full mapper graph
# (e.g. Host.project -> Project) before the first query.  Unlike the ingestion
# worker this process never runs initialize_database(), which is what loads them
# elsewhere — without this, the first ReportJob query fails to resolve related
# mappers.  Keep in sync with alembic/env.py / app/db/init.py / tests/conftest.py.
import app.db.models  # noqa: F401,E402
import app.db.models_auth  # noqa: F401,E402
import app.db.models_project  # noqa: F401,E402
import app.db.models_agent  # noqa: F401,E402
import app.db.models_findings  # noqa: F401,E402
import app.db.models_vulnerability  # noqa: F401,E402
import app.db.models_confidence  # noqa: F401,E402
import app.db.models_llm  # noqa: F401,E402
import app.db.models_integrations  # noqa: F401,E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("app.report_worker")

POLL_INTERVAL = float(os.getenv("REPORT_WORKER_POLL_INTERVAL", "5"))

# Liveness file the container healthcheck reads — see worker_loop.write_heartbeat.
HEARTBEAT_PATH = os.getenv(
    "REPORT_WORKER_HEARTBEAT_PATH", "/tmp/bluestick_report_worker_heartbeat"
)


def main() -> None:
    worker_loop.install_signal_handlers(logger)

    service = ReportJobService()

    def _reap() -> None:
        handled = service.reap_orphaned_jobs()
        if handled:
            logger.info("Report reaper handled %d stalled job(s)", handled)

    def _cleanup() -> None:
        removed = service.cleanup_expired()
        if removed:
            logger.info("Report cleanup removed %d expired artifact(s)", removed)
        # R6 backstop: reap orphaned note-attachment files (commit-failure /
        # missed deletes). Cheap — one query + a grace-windowed dir scan.
        try:
            from app.db.session import SessionLocal
            from app.services.note_attachment_service import reconcile_orphan_attachments
            with SessionLocal() as db:
                orphans = reconcile_orphan_attachments(db)
            if orphans:
                logger.info("Reaped %d orphaned attachment dir(s)", orphans)
        except Exception:
            logger.exception("Attachment reconcile failed")

    worker_loop.run_listen_loop(
        channel="report_jobs",
        poll_one=service.poll_and_run_one,
        heartbeat_path=HEARTBEAT_PATH,
        logger=logger,
        poll_interval=POLL_INTERVAL,
        periodic=[_reap, _cleanup],
    )
    logger.info("Report worker stopped")


if __name__ == "__main__":
    main()
