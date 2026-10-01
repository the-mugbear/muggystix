"""ingestion_jobs.in_progress_scan_id — the scan the current attempt is writing

Review 2026-10-01 R1.  ``ingestion_jobs.scan_id`` is written only when a job
completes, so the scan a parser committed incrementally was known only to the
parser object in the worker's memory.  A worker killed mid-import (OOM,
SIGKILL, host restart) left that partial scan behind with nothing pointing at
it, and the reaper's re-queue imported the file into a second scan.

The column is stamped in the transaction that first commits the scan row and
cleared at completion; a value found when the job is claimed again (or reaped
to ``failed``) names a dead attempt's scan, which is deleted first.
``ON DELETE SET NULL``: deleting the scan — the in-process cleanup, or an
operator — clears the pointer.

Existing rows get NULL: nothing is known about attempts that died before this
revision.
"""
import sqlalchemy as sa
from alembic import op

revision = "a1d4f7b9c2e3"
down_revision = "c2e6a4f8d103"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "in_progress_scan_id", sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="SET NULL"), nullable=True,
        ),
    )


def downgrade():
    # Dropping the column drops its foreign key with it.
    op.drop_column("ingestion_jobs", "in_progress_scan_id")
