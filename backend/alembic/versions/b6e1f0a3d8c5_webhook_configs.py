"""outbound webhook configs (v2.73.0)

Per-project outbound webhooks (Slack-incoming-webhook compatible) that
fire on mention / status-change / assignment events.

Revision ID: b6e1f0a3d8c5
Revises: e3d9b1c7a4f2
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa


revision = "b6e1f0a3d8c5"
down_revision = "e3d9b1c7a4f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_webhook_configs_project_id", "webhook_configs", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_webhook_configs_project_id", table_name="webhook_configs")
    op.drop_table("webhook_configs")
