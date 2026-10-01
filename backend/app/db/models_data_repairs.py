"""The data-repair ledger (review 2026-10-01 B10).

A *data repair* is a one-off correction of rows written by older code — not a
schema change, so Alembic does not carry it, and not safe to run blindly at
boot.  Before this table the only record that one existed was a paragraph in
``scripts/README.md``: nothing told an operator upgrading across two of them
that either was owed, and nothing recorded that one had been run.

One row per repair NAME (the names live in
``app/services/data_repair_service.KNOWN_REPAIRS``): the most recent run that
settled it.  ``rows_affected`` is the repair's own counts as JSON — numbers
keyed by a rule or check id, never a host name, address or credential — so
the table is safe to include in a diagnostics bundle as it is.
"""
from sqlalchemy import Column, DateTime, Integer, JSON, String, UniqueConstraint
from sqlalchemy.sql import func

from app.db.session import Base


class DataRepairRun(Base):
    __tablename__ = "data_repairs"
    __table_args__ = (UniqueConstraint("name", name="uq_data_repairs_name"),)

    id = Column(Integer, primary_key=True)
    # The repair's stable name, e.g. "misconfig_backfill".
    name = Column(String(100), nullable=False)
    applied_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # What ran it: the script's name, or "migration" for a row seeded when the
    # ledger was created on a database with nothing to repair.
    applied_by = Column(String(100), nullable=True)
    # "apply"             — the repair ran and wrote its corrections;
    # "nothing_to_repair" — a dry run over every project found nothing;
    # "not_needed"        — the database held no data older than the fix.
    mode = Column(String(20), nullable=False)
    rows_affected = Column(JSON, nullable=True)
    # The backend version that ran it (settings.APP_VERSION).
    app_version = Column(String(32), nullable=True)
    run_count = Column(Integer, nullable=False, server_default="1")
