"""drop the dead port_attributes table

Revision ID: d1b7e4a9c602
Revises: c3f9a2e7d810
Create Date: 2026-06-10 22:10:00.000000

The 2026-06-10 schema review found ``port_attributes`` (the
``PortAttribute`` model) has ZERO writers and ZERO queries anywhere in
``app/`` — only a relationship definition on ``Port.attributes`` that
nothing ever reads.  It is the dead twin of ``host_attributes``, which
the vulnerability service does actively write.

Drops the dead table.  Downgrade is a documented no-op, matching the
``data_source_metadata`` / risk-scoring-drop precedent: this removes a
dead table, not a reversible schema tweak.

Phase 1.2 of the schema-review remediation.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1b7e4a9c602'
down_revision: Union[str, None] = 'c3f9a2e7d810'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS port_attributes CASCADE")


def downgrade() -> None:
    # Restores the table as the baseline defined it.
    #
    # This was a deliberate no-op ("it drops a dead, never-used table"), which
    # conflated "nobody uses this table" with "rollback doesn't need it". A
    # downgrade's job is to return the schema to what it was AT that revision,
    # and an earlier revision's upgrade indexes this table — so the no-op made
    # the whole chain non-round-trippable: walking back to the baseline and
    # forward again died on
    #     UndefinedTable: relation "port_attributes" does not exist
    # Found by wiring the round-trip check into CI (review B3).
    op.create_table(
        "port_attributes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("port_id", sa.Integer(), nullable=False),
        sa.Column("attribute_type", sa.String(length=50), nullable=False),
        sa.Column("value", sa.String(length=1000), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["port_id"], ["ports_v2.id"]),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_port_attributes_attribute_type"), "port_attributes",
        ["attribute_type"], unique=False,
    )
