"""shared per-agent rate-limit buckets

Agent rate limiting could not enforce a shared limit. It took max() of a COUNT
over `agent_api_calls` — whose rows are written by a POST-RESPONSE background
task, so the count lagged every in-flight request and read 0 if the writer was
failing — and an in-process deque that exists only inside one Uvicorn worker.
Production runs four, so a burst spread across them exceeded rate_limit_rpm
before any audit row landed, and adding workers made it worse.

This table is the shared state. Admission does one
`INSERT ... ON CONFLICT DO UPDATE ... RETURNING count`, which Postgres
serializes per row, so capacity is reserved atomically at admission instead of
inferred from an audit log afterwards.

The composite PK (agent_id, window_start) is the whole mechanism: a new window
is a new row, so there is no read-then-reset step to race on.

Revision ID: d3f8b1e64a29
Revises: c9a4e70b5d18
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa


revision = "d3f8b1e64a29"
down_revision = "c9a4e70b5d18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_rate_buckets",
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "window_start"),
    )
    # Housekeeping deletes by age; without this the sweep scans the table.
    op.create_index(
        "idx_agent_rate_bucket_window", "agent_rate_buckets", ["window_start"],
    )


def downgrade() -> None:
    op.drop_index("idx_agent_rate_bucket_window", table_name="agent_rate_buckets")
    op.drop_table("agent_rate_buckets")
