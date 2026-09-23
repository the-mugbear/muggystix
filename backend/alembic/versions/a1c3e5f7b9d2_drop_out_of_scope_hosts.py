"""Drop the unwritten out_of_scope_hosts table (v2.395.0)

Nothing has written ``out_of_scope_hosts`` since host deduplication landed:
out-of-scope is derived (``app/services/scope_coverage.py`` — a host with no
``host_subnet_mappings`` row), and the only readers left were the per-scan
list/count routes and an admin purge, which could only ever see zero rows.
Those routes are removed with the table (review 2026-09-23 B-Debt-3).

Downgrade recreates the empty table as the model last declared it.

Revision ID: a1c3e5f7b9d2
Revises: f4b6d8a0c2e3
Create Date: 2026-09-23
"""
from alembic import op
import sqlalchemy as sa


revision = "a1c3e5f7b9d2"
down_revision = "f4b6d8a0c2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("out_of_scope_hosts")


def downgrade() -> None:
    op.create_table(
        "out_of_scope_hosts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("ip_address", sa.String(), nullable=False),
        sa.Column("hostname", sa.String(), nullable=True),
        sa.Column("ports", sa.JSON(), nullable=True),
        sa.Column("tool_source", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name="out_of_scope_hosts_project_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"],
            name="out_of_scope_hosts_scan_id_fkey", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_out_of_scope_hosts_id", "out_of_scope_hosts", ["id"], unique=False)
    op.create_index("ix_out_of_scope_hosts_ip_address", "out_of_scope_hosts", ["ip_address"], unique=False)
    op.create_index("ix_out_of_scope_hosts_project_id", "out_of_scope_hosts", ["project_id"], unique=False)
    op.create_index("idx_out_of_scope_hosts_scan", "out_of_scope_hosts", ["scan_id"], unique=False)
