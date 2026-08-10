"""drop the dead data_source_metadata table

Revision ID: c3f9a2e7d810
Revises: d4a1c8f73b69
Create Date: 2026-06-10 21:30:00.000000

The 2026-06-10 schema review found ``data_source_metadata`` (the
``DataSourceMetadata`` model) has ZERO reads or writes anywhere in
``app/`` — no service, endpoint, or parser ever touches it.  It also
duplicated columns already on ``scans`` (scan_type, command_line,
timing).  It survived only because the cascade-FK sweep
(``f1a9c7e3b528``) still listed it as a scan child.

This drops the dead table.  Per the precedent set by
``d6e7f8090a1b_drop_dead_risk_scoring_tables`` (which also removed a
never-populated subsystem), the downgrade is a documented no-op: this
removes a dead feature, not a reversible schema tweak, and recreating
an empty unused table on rollback has no value.

Phase 0b of the schema-review remediation.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3f9a2e7d810'
down_revision: Union[str, None] = 'd4a1c8f73b69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_source_metadata CASCADE")


def downgrade() -> None:
    # Restores the table as the baseline defined it.
    #
    # This was a deliberate no-op ("it drops a dead, never-used table"), which
    # conflated "nobody uses this table" with "rollback doesn't need it". A
    # downgrade's job is to return the schema to what it was AT that revision,
    # and an earlier revision's upgrade indexes this table — so the no-op made
    # the whole chain non-round-trippable: walking back to the baseline and
    # forward again died on
    #     UndefinedTable: relation "data_source_metadata" does not exist
    # Found by wiring the round-trip check into CI (review B3).
    op.create_table(
        "data_source_metadata",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("scan_type", sa.String(), nullable=False),
        sa.Column("tool_version", sa.String(), nullable=True),
        sa.Column("command_line", sa.Text(), nullable=True),
        sa.Column("total_hosts_scanned", sa.Integer(), nullable=True),
        sa.Column("successful_responses", sa.Integer(), nullable=True),
        sa.Column("failed_responses", sa.Integer(), nullable=True),
        sa.Column("timeout_count", sa.Integer(), nullable=True),
        sa.Column("scan_duration_seconds", sa.Float(), nullable=True),
        sa.Column("scan_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scan_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_metadata", sa.JSON(), nullable=True),
        sa.Column("overall_quality_score", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_data_source_metadata_id"), "data_source_metadata", ["id"], unique=False,
    )
