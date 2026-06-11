"""backfill assigned_at for hosts already In Review / Reviewed

Revision ID: d4a1c8f73b69
Revises: c3f9a1e6b482
Create Date: 2026-06-10 00:00:00.000000

Taking a host into review now stamps assigned_at so it shows under
"Assigned to me".  Rows reviewed BEFORE that change have status
IN_REVIEW/REVIEWED but a null assigned_at, so they'd stay invisible to the
filter until re-touched.  Backfill them to the reviewer (best-effort
timestamp from updated_at/created_at).  Explicit unassignment is unaffected
(it leaves status set but clears assigned_at — but here those rows had never
been assigned at all, so there's nothing to preserve).
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd4a1c8f73b69'
down_revision: Union[str, None] = 'c3f9a1e6b482'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE host_follows
        SET assigned_at = COALESCE(updated_at, created_at, now()),
            assigned_by_id = user_id
        WHERE assigned_at IS NULL
          AND status IN ('IN_REVIEW', 'REVIEWED')
        """
    )


def downgrade() -> None:
    # Can't tell backfilled assignments from real ones; leave them in place.
    pass
