"""netexec_results.writable_share (v2.412.0)

A share table row with WRITE permission (NetExec --shares, SMBMap) as a typed
column, so `has:writable_share` filters on it instead of the shares JSON
(column-vs-blob policy).  Backfilled from the stored shares.

Revision ID: c2f7a9d4e6b1
Revises: b8e3f0a5c2d7
Create Date: 2026-09-25
"""
import sqlalchemy as sa
from alembic import op


revision = "c2f7a9d4e6b1"
down_revision = "b8e3f0a5c2d7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("netexec_results", sa.Column("writable_share", sa.Boolean(), nullable=True))
    # Only the list shape carries permissions (the --shares table, SMBMap);
    # a spider_plus listing (an object) says nothing about them.
    op.execute(
        """
        UPDATE netexec_results SET writable_share = EXISTS (
            SELECT 1 FROM json_array_elements(shares) AS s
            WHERE upper(coalesce(s->>'permissions', '')) LIKE '%WRITE%'
        )
        WHERE shares IS NOT NULL AND json_typeof(shares) = 'array'
        """
    )


def downgrade():
    op.drop_column("netexec_results", "writable_share")
