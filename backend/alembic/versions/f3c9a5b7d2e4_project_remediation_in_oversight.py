"""projects.remediation_in_oversight: opt a project's remediation counts into Oversight

One boolean column, false for every existing project: no project's open /
closed / deferred counts appear on Oversight until a project admin turns it
on.  No data change.

Revision ID: f3c9a5b7d2e4
Revises: e2b8f4a6c1d3
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

revision = "f3c9a5b7d2e4"
down_revision = "e2b8f4a6c1d3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "projects",
        sa.Column("remediation_in_oversight", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("projects", "remediation_in_oversight")
