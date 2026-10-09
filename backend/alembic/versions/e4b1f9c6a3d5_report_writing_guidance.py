"""report text: the installation's writing guidance

``report_writing_guidance`` — one row per key (``general`` and each report-text
section), holding what a global admin wrote in System settings to tell a model
how to draft that section.  No row is the shipped default, so the table starts
empty and the upgrade changes nothing about how drafts are written.

The downgrade drops the table: every edited instruction is lost and drafts are
written from the shipped defaults again.

Revision ID: e4b1f9c6a3d5
Revises: d3a0e8b5f2c4
Create Date: 2026-10-09
"""
import sqlalchemy as sa
from alembic import op

revision = "e4b1f9c6a3d5"
down_revision = "d3a0e8b5f2c4"
branch_labels = None
depends_on = None

TABLE = "report_writing_guidance"


def upgrade():
    op.create_table(
        TABLE,
        sa.Column("key", sa.String(length=40), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table(TABLE)
