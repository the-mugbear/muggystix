"""Saved and shared Query-page queries (v2.509.0)

The owner, 2026-10-16: the teams know pipe query languages, so the Query page saves a
pipeline under a name, for oneself or shared with the project.

**Upgrade**: CREATES the empty table ``saved_queries`` (project CASCADE,
creator SET NULL, one name per person per project).  Catalogue only, no row
written.

**Downgrade**: DROPS the table — every saved query is lost.

Revision ID: f6a2c8e4b0d3
Revises: e5c1a7b3d9f2
"""
import sqlalchemy as sa
from alembic import op

revision = "f6a2c8e4b0d3"
down_revision = "e5c1a7b3d9f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("shared", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "created_by_id", "name", name="uq_saved_query_name"),
    )
    op.create_index(op.f("ix_saved_queries_id"), "saved_queries", ["id"], unique=False)
    op.create_index(op.f("ix_saved_queries_project_id"), "saved_queries", ["project_id"], unique=False)
    op.create_index(op.f("ix_saved_queries_created_by_id"), "saved_queries", ["created_by_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_saved_queries_created_by_id"), table_name="saved_queries")
    op.drop_index(op.f("ix_saved_queries_project_id"), table_name="saved_queries")
    op.drop_index(op.f("ix_saved_queries_id"), table_name="saved_queries")
    op.drop_table("saved_queries")
