"""Project status: 'in_progress' merged into 'active' (v2.398.0)

A project had two statuses for "under way" — active and in_progress — and
every reader (Portfolio, Oversight, the attention signals, the activity
badge) treated them the same, so choosing between them meant nothing.  One
status remains: active.  The API still accepts 'in_progress' from an older
client and stores 'active'.

Downgrade is a no-op: which projects were marked in_progress is not
recoverable, and 'active' is valid in the older code.

Revision ID: d9f1b3c5e7a2
Revises: b8e1d3f5a7c9
Create Date: 2026-09-23
"""
from alembic import op


revision = "d9f1b3c5e7a2"
down_revision = "b8e1d3f5a7c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE projects SET status = 'active' WHERE status = 'in_progress'")


def downgrade() -> None:
    pass
