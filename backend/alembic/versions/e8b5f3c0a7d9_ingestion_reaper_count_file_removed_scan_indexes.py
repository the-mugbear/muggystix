"""ingestion: the reaper's own counter, when a job's file was removed, scan_id indexes

* ``ingestion_jobs.reap_count`` — how many times the orphan reaper re-queued
  the job since an operator last queued it.  ``INGESTION_MAX_RETRIES`` is
  measured against this; ``retry_count`` keeps counting failures and operator
  retries for the pages.  Starts at 0 for every row.
* ``ingestion_jobs.file_removed_at`` — when the uploaded file was removed.
  Nothing is back-filled: NULL means "not known to be gone", and the
  retention sweep stamps old rows as it finds their file missing.
* Indexes on the ``scan_id`` columns that deleting a scan (and the cleanup of
  a partial scan) filters on and that had none: ``scripts_v2.scan_id``,
  ``host_scripts_v2.scan_id``, ``ingestion_jobs.scan_id``,
  ``ingestion_jobs.in_progress_scan_id``.  Built inside the upgrade
  transaction.

No data change.

Revision ID: e8b5f3c0a7d9
Revises: d7a4e2b9f6c8
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

revision = "e8b5f3c0a7d9"
down_revision = "d7a4e2b9f6c8"
branch_labels = None
depends_on = None

_SCAN_INDEXES = (
    ("ix_scripts_v2_scan_id", "scripts_v2", "scan_id"),
    ("ix_host_scripts_v2_scan_id", "host_scripts_v2", "scan_id"),
    ("ix_ingestion_jobs_scan_id", "ingestion_jobs", "scan_id"),
    ("ix_ingestion_jobs_in_progress_scan_id", "ingestion_jobs", "in_progress_scan_id"),
)


def upgrade():
    op.add_column(
        "ingestion_jobs",
        sa.Column("reap_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("file_removed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, table, column in _SCAN_INDEXES:
        op.create_index(name, table, [column], unique=False)


def downgrade():
    for name, table, _column in reversed(_SCAN_INDEXES):
        op.drop_index(name, table_name=table)
    op.drop_column("ingestion_jobs", "file_removed_at")
    op.drop_column("ingestion_jobs", "reap_count")
