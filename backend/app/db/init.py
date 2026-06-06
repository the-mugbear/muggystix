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
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from app.db.session import engine

logger = logging.getLogger(__name__)

_done = False

# Stable 64-bit key for the schema-migration advisory lock.  Arbitrary fixed
# constant — every process just has to agree on the same value.
_MIGRATION_LOCK_KEY = 738582901


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
    if "alembic_version" in tables:
        command.upgrade(cfg, "head")
    elif "users" in tables:
        logger.info("Existing pre-Alembic schema detected — adopting it (stamp head)")
        command.stamp(cfg, "head")
    else:
        logger.info("Empty database — building schema from Alembic head")
        command.upgrade(cfg, "head")


def initialize_database() -> None:
    """Bring the database schema to Alembic head, retrying while the DB boots.

    Safe to call multiple times (the ``_done`` guard makes every call after
    the first a no-op) and safe to call concurrently from the API and worker
    processes: on PostgreSQL a session-level advisory lock serializes the
    upgrade, so whichever process loses the race simply runs it as a no-op.

    ``_done`` is set only after a fully successful sync, so a failed attempt
    (retries exhausted, mid-migration crash) leaves it ``False`` and a later
    call in the same process can try again.
    """
    global _done
    if _done:
        return

    max_attempts = int(os.getenv("DB_INIT_MAX_RETRIES", "10"))
    backoff_seconds = float(os.getenv("DB_INIT_RETRY_DELAY", "3"))
    last_exc: OperationalError | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            if engine.dialect.name == "postgresql":
                # Serialize schema upgrades across processes.  The loser of
                # the race blocks on pg_advisory_lock, then runs the sync as
                # a no-op once the winner has finished and released it.
                with engine.connect() as conn:
                    conn.execute(
                        text("SELECT pg_advisory_lock(:k)"),
                        {"k": _MIGRATION_LOCK_KEY},
                    )
                    try:
                        _sync_schema_with_alembic()
                    finally:
                        conn.execute(
                            text("SELECT pg_advisory_unlock(:k)"),
                            {"k": _MIGRATION_LOCK_KEY},
                        )
                        conn.commit()
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
