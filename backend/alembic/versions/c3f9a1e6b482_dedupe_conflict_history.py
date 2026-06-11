"""dedupe conflict_history rows (one per distinct disagreement)

Revision ID: c3f9a1e6b482
Revises: b1d4e7f20c93
Create Date: 2026-06-10 00:00:00.000000

`_record_conflict` used to insert a ConflictHistory row unconditionally, so
the same disagreement (held value vs reported value) accumulated a fresh row
on every re-scan — and even multiple times within one scan.  That inflated
the host conflict count and made the host-detail "Resolution history" list
the same line many times.  The service is now idempotent on
(object, field, previous_value, new_value); this migration collapses the
rows that already piled up, keeping the most recent (max id) per group.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c3f9a1e6b482'
down_revision: Union[str, None] = 'b1d4e7f20c93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep the highest id per distinct disagreement; drop the rest.
    # IS NOT DISTINCT FROM so NULLs (port_id on host conflicts, null values)
    # group together rather than each being treated as unique.
    op.execute(
        """
        DELETE FROM conflict_history a
        USING conflict_history b
        WHERE a.id < b.id
          AND a.host_id        IS NOT DISTINCT FROM b.host_id
          AND a.port_id        IS NOT DISTINCT FROM b.port_id
          AND a.field_name     IS NOT DISTINCT FROM b.field_name
          AND a.previous_value IS NOT DISTINCT FROM b.previous_value
          AND a.new_value      IS NOT DISTINCT FROM b.new_value
        """
    )


def downgrade() -> None:
    # Collapsing duplicate audit rows is not reversible.
    pass
