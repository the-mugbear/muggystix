"""ingestion_jobs.uninterpreted_lines (v2.418.0)

The lines a parser did not interpret, as redacted shapes with counts, so an
import on a network whose files cannot be shared still says what it missed.

Revision ID: f6d0b4a8c3e9
Revises: e5c9a3f7b2d8
Create Date: 2026-09-26
"""
import sqlalchemy as sa
from alembic import op


revision = "f6d0b4a8c3e9"
down_revision = "e5c9a3f7b2d8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ingestion_jobs", sa.Column("uninterpreted_lines", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("ingestion_jobs", "uninterpreted_lines")
