"""conflict_history: remove "state: unknown -> <known>" rows (v2.367.0)

A conflict is two scans DISAGREEING about a host.  The dedup service also
recorded one whenever a host it held as ``state='unknown'`` was later observed
with a real state — a blank being filled in.  Those rows were the most common
"conflict" in the inventory, put a "1 conflict" badge on hosts nothing
disagreed about, and counted toward the scan import summary's conflicts.

The service stopped writing them in the same release; this removes the ones
already stored so every reader (host list badge, host detail, scan summary,
reports) agrees without each carrying its own exclusion.  Nothing else lives
on these rows: confidence and method are always null for dedup-written
conflicts.  Not reversible — the downgrade is a no-op.

Revision ID: e5a1c7d3f9b2
Revises: d4e9f2a7c1b8
Create Date: 2026-09-19
"""
from alembic import op


revision = "e5a1c7d3f9b2"
down_revision = "d4e9f2a7c1b8"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "DELETE FROM conflict_history "
        "WHERE field_name = 'state' AND previous_value = 'unknown'"
    )


def downgrade():
    pass
