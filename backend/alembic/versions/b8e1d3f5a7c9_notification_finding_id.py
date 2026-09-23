"""notifications.finding_id — deep link for finding-comment notifications (v2.397.0)

A mention in, or a reply to, a finding's discussion needs to open that
finding at the comment, the way a host-note notification opens
/hosts/<host_id>#note-<id>.  A plain nullable integer like ``host_id``: a
soft polymorphic reference, not a foreign key.

Revision ID: b8e1d3f5a7c9
Revises: a1c3e5f7b9d2
Create Date: 2026-09-23
"""
from alembic import op
import sqlalchemy as sa


revision = "b8e1d3f5a7c9"
down_revision = "a1c3e5f7b9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("finding_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_notifications_finding_id"), "notifications", ["finding_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_finding_id"), table_name="notifications")
    op.drop_column("notifications", "finding_id")
