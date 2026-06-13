"""report_jobs — async report generation queue

The heavy export formats (PDF, JSON, markdown-bundle, agent-package) build the
whole document in memory, so they now run on a dedicated background worker
instead of the API request thread.  This table is that worker's queue — it
mirrors ``ingestion_jobs``' lifecycle (queued → processing → completed/failed)
and dead-letter columns (retry_count / last_error / last_heartbeat) so the
report worker reuses the same claim + reaper + heartbeat mechanics.  The
generated artifact is written to disk (``result_path``) and deleted after
``expires_at`` by the worker's cleanup pass.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1d7e9f4a2c8"
down_revision: Union[str, None] = "a9f3c2e1b740"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("report_type", sa.String(length=20), nullable=False, server_default="comprehensive"),
        sa.Column("filters", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_path", sa.String(), nullable=True),
        sa.Column("result_filename", sa.String(), nullable=True),
        sa.Column("media_type", sa.String(), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE",
            name="fk_report_jobs_project_id",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"], ["users.id"], ondelete="SET NULL",
            name="fk_report_jobs_requested_by_id",
        ),
    )
    op.create_index("ix_report_jobs_project_id", "report_jobs", ["project_id"])
    op.create_index("ix_report_jobs_expires_at", "report_jobs", ["expires_at"])
    op.create_index(
        "idx_report_jobs_project_status", "report_jobs",
        ["project_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_report_jobs_project_status", table_name="report_jobs")
    op.drop_index("ix_report_jobs_expires_at", table_name="report_jobs")
    op.drop_index("ix_report_jobs_project_id", table_name="report_jobs")
    op.drop_table("report_jobs")
