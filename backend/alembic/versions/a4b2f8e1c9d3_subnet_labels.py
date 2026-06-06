"""subnet labels + assignment (v2.86.0)

Parallel to the v2.71.0 host-tags migration: adds a project-scoped
``subnet_labels`` definition table and a ``subnet_label_assignments``
many-to-many join so the Hosts inventory page can filter by labels
attached to the host's containing subnets.

Revision ID: a4b2f8e1c9d3
Revises: e7a5b9f2c814
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa


revision = "a4b2f8e1c9d3"
down_revision = "e7a5b9f2c814"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subnet_labels",
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
        sa.UniqueConstraint("project_id", "name", name="uq_subnet_label_name"),
    )
    op.create_index("ix_subnet_labels_project_id", "subnet_labels", ["project_id"])

    op.create_table(
        "subnet_label_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subnet_id",
            sa.Integer(),
            sa.ForeignKey("subnets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "label_id",
            sa.Integer(),
            sa.ForeignKey("subnet_labels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("subnet_id", "label_id", name="uq_subnet_label_assignment"),
    )
    op.create_index("ix_subnet_label_assignments_subnet_id", "subnet_label_assignments", ["subnet_id"])
    op.create_index("ix_subnet_label_assignments_label_id", "subnet_label_assignments", ["label_id"])


def downgrade() -> None:
    op.drop_index("ix_subnet_label_assignments_label_id", table_name="subnet_label_assignments")
    op.drop_index("ix_subnet_label_assignments_subnet_id", table_name="subnet_label_assignments")
    op.drop_table("subnet_label_assignments")
    op.drop_index("ix_subnet_labels_project_id", table_name="subnet_labels")
    op.drop_table("subnet_labels")
