"""job-queue claim indexes — partial indexes for the worker poll/reap paths

Both workers poll ``WHERE status='queued' ORDER BY created_at LIMIT 1`` and the
reapers scan ``WHERE status='processing'`` by ``last_heartbeat``.  Without a
matching index every poll scans + sorts the full (retained) job history, and the
report_jobs composite index leads with ``project_id`` so it can't serve the
project-agnostic poll.  Partial indexes keyed exactly on the claim/reap
predicates stay tiny (only live rows) and make both O(log n).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3a9d1f80b27"
down_revision: Union[str, None] = "b1d7e9f4a2c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_ingestion_jobs_queued_created", "ingestion_jobs", ["created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_ingestion_jobs_processing_heartbeat", "ingestion_jobs", ["last_heartbeat"],
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "ix_report_jobs_queued_created", "report_jobs", ["created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_report_jobs_processing_heartbeat", "report_jobs", ["last_heartbeat"],
        postgresql_where=sa.text("status = 'processing'"),
    )


def downgrade() -> None:
    op.drop_index("ix_report_jobs_processing_heartbeat", table_name="report_jobs")
    op.drop_index("ix_report_jobs_queued_created", table_name="report_jobs")
    op.drop_index("ix_ingestion_jobs_processing_heartbeat", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_queued_created", table_name="ingestion_jobs")
