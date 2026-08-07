"""webhook_deliveries: durable outbox for outbound webhook POSTs

Webhook delivery was fire-and-forget on a process-local queue: a redeploy,
crash, or transient receiver outage lost the event with only a log line.
Each intended POST is now persisted here, attempted immediately, and retried
with exponential backoff by a periodic sweeper until max_attempts.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4a1c73b8d95"
down_revision: Union[str, None] = "d9b4e71c2a86"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("webhook_config_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=True,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["webhook_config_id"], ["webhook_configs.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_deliveries_id", "webhook_deliveries", ["id"])
    op.create_index(
        "ix_webhook_deliveries_webhook_config_id",
        "webhook_deliveries", ["webhook_config_id"],
    )
    op.create_index(
        "ix_webhook_deliveries_project_id", "webhook_deliveries", ["project_id"],
    )
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])
    op.create_index(
        "ix_webhook_deliveries_next_attempt_at",
        "webhook_deliveries", ["next_attempt_at"],
    )
    # The sweeper's only query: due pending rows, oldest first.
    op.create_index(
        "idx_webhook_delivery_due", "webhook_deliveries",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_webhook_delivery_due", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_next_attempt_at", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_project_id", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_webhook_config_id", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
