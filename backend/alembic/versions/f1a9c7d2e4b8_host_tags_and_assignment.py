"""host tagging + host assignment (v2.71.0)

Adds the Phase 2 (workflow) schema:

  * ``host_tags`` — project-scoped tag definitions (name + palette colour).
  * ``host_tag_assignments`` — host<->tag many-to-many links.
  * ``host_follows.assigned_by_id`` / ``assigned_at`` — ownership /
    assignment.  A non-null ``assigned_at`` on a (host, user) follow row
    means the host is assigned to ``user_id`` by ``assigned_by_id``.

Revision ID: f1a9c7d2e4b8
Revises: e7f3c95a2b18
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa


revision = "f1a9c7d2e4b8"
down_revision = "e7f3c95a2b18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "name", name="uq_host_tag_name"),
    )
    op.create_index("ix_host_tags_project_id", "host_tags", ["project_id"])

    op.create_table(
        "host_tag_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "host_id",
            sa.Integer(),
            sa.ForeignKey("hosts_v2.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("host_tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("host_id", "tag_id", name="uq_host_tag_assignment"),
    )
    op.create_index("ix_host_tag_assignments_host_id", "host_tag_assignments", ["host_id"])
    op.create_index("ix_host_tag_assignments_tag_id", "host_tag_assignments", ["tag_id"])

    op.add_column(
        "host_follows",
        sa.Column("assigned_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column(
        "host_follows",
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_host_follow_assigned", "host_follows", ["assigned_at"])


def downgrade() -> None:
    op.drop_index("idx_host_follow_assigned", table_name="host_follows")
    op.drop_column("host_follows", "assigned_at")
    op.drop_column("host_follows", "assigned_by_id")
    op.drop_index("ix_host_tag_assignments_tag_id", table_name="host_tag_assignments")
    op.drop_index("ix_host_tag_assignments_host_id", table_name="host_tag_assignments")
    op.drop_table("host_tag_assignments")
    op.drop_index("ix_host_tags_project_id", table_name="host_tags")
    op.drop_table("host_tags")
