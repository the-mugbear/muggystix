"""tool_registry — one source of truth for the tools BlueStick knows about

Replaces two lists that could not see each other: 61 curated entries hardcoded
in the frontend reference page, and 11 tools in the backend recon catalog (the
only one that could gate anything). They had already drifted — `testssl` was
agent-usable with no human entry.

Seeding is done by the app on boot from app/data/tool_registry_seed.json rather
than in this migration, so the curated prose stays reviewable as data and a
re-seed doesn't require a new revision.

Revision ID: b8d3f61c470a
Revises: a7e4c2b91d38
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = "b8d3f61c470a"
down_revision = "a7e4c2b91d38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_registry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("ports", sa.String(length=64), nullable=True),
        sa.Column("install", sa.String(length=255), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("kali", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="reference"),
        sa.Column("phases", sa.JSON(), nullable=True),
        sa.Column("intrusive", sa.Boolean(), nullable=True),
        sa.Column("requires_privileges", sa.Boolean(), nullable=True),
        sa.Column("output_format", sa.String(length=16), nullable=True),
        sa.Column("ingestible", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("suggested_rationale", sa.Text(), nullable=True),
        sa.Column("suggested_by_agent_id", sa.Integer(), nullable=True),
        sa.Column("suggested_in_project_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", name="uq_tool_registry_name"),
    )
    op.create_index("ix_tool_registry_name", "tool_registry", ["name"])
    op.create_index("ix_tool_registry_category", "tool_registry", ["category"])
    op.create_index("ix_tool_registry_status", "tool_registry", ["status"])
    op.create_index("idx_tool_registry_status_name", "tool_registry", ["status", "name"])


def downgrade() -> None:
    op.drop_index("idx_tool_registry_status_name", table_name="tool_registry")
    op.drop_index("ix_tool_registry_status", table_name="tool_registry")
    op.drop_index("ix_tool_registry_category", table_name="tool_registry")
    op.drop_index("ix_tool_registry_name", table_name="tool_registry")
    op.drop_table("tool_registry")
