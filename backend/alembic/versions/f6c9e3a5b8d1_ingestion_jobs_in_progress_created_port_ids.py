"""ingestion_jobs.in_progress_created_port_ids: the ports a Nessus attempt created, for cleanup (review 2026-10-01 R2)

Revision ID: f6c9e3a5b8d1
Revises: e5b8d2f4a7c9
Create Date: 2026-10-01

``delete_partial_scan`` removes what a failed import attempt created.  It
finds the attempt's ports through ``port_scan_history.port_created`` — which
the Nessus path does not write, and must not start to: that would change the
port counts on the Scans page, the dashboard and the scan diff.  So the ports
a failed, cancelled or killed Nessus import added to hosts that ALREADY
EXISTED stayed in the inventory.

This column remembers those port ids on the job row, written in the same
transaction that commits each batch and cleared when the job completes or its
leftovers are deleted.  It is read by the cleanup and by nothing else.

Nullable JSON (an array of integers), no backfill: nothing is known about
attempts that ended before this revision.
"""
import sqlalchemy as sa
from alembic import op

revision = "f6c9e3a5b8d1"
down_revision = "e5b8d2f4a7c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column("in_progress_created_port_ids", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "in_progress_created_port_ids")
