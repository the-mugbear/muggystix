"""test_execution_results.sanity_override_reason column (v2.91.0 — code review #2)

Closes finding #2 from the first code review in Option B shape:
``record_test_result`` no longer accepts a result-record write
silently when there's no passing ``HostSanityCheck`` on file — the
agent must supply ``sanity_override_reason`` instead.  The column
added here persists that reason so the audit trail shows WHICH
recorded results were captured against an unverified target.

Indexed for the "show me every result that bypassed sanity" audit
query.  Pre-existing rows get NULL on upgrade (the gap they
represent is documented in the changelog).

Revision ID: e7b3c845f192
Revises: d5a8b29e0f47
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa


revision = "e7b3c845f192"
down_revision = "d5a8b29e0f47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_execution_results",
        sa.Column("sanity_override_reason", sa.String(length=500), nullable=True),
    )
    op.create_index(
        "ix_test_execution_results_sanity_override_reason",
        "test_execution_results",
        ["sanity_override_reason"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_test_execution_results_sanity_override_reason",
        table_name="test_execution_results",
    )
    op.drop_column("test_execution_results", "sanity_override_reason")
