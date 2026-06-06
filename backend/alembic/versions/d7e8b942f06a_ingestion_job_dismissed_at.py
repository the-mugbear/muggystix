"""ingestion_jobs.dismissed_at (v2.86.2)

Add a nullable timestamp column so operators can dismiss failed
ingestion-queue rows.  Pre-fix the queue accumulated failures with no
acknowledge path; the column lets the list endpoint hide
operator-dismissed rows by default while preserving the audit trail.

Revision ID: d7e8b942f06a
Revises: c6e0fa492b15
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa


revision = "d7e8b942f06a"
down_revision = "c6e0fa492b15"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ingestion_jobs",
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("ingestion_jobs", "dismissed_at")
