"""web_interfaces.content_length and web_paths.size become BIGINT (v2.419.0)

A response body past 2 GiB is real tool output; the 32-bit columns failed the
flush, which (before per-record savepoints) failed the whole httpx import.
Widening is lossless.

Revision ID: a1e7c5b9d2f4
Revises: f6d0b4a8c3e9
Create Date: 2026-09-26
"""
import sqlalchemy as sa
from alembic import op


revision = "a1e7c5b9d2f4"
down_revision = "f6d0b4a8c3e9"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("web_interfaces", "content_length", type_=sa.BigInteger(), existing_type=sa.Integer())
    op.alter_column("web_paths", "size", type_=sa.BigInteger(), existing_type=sa.Integer())


def downgrade():
    # Values past the 32-bit range cannot narrow: they become NULL.
    for table, column in (("web_interfaces", "content_length"), ("web_paths", "size")):
        op.execute(f"UPDATE {table} SET {column} = NULL WHERE {column} > 2147483647 OR {column} < -2147483648")
        op.alter_column(table, column, type_=sa.Integer(), existing_type=sa.BigInteger())
