"""backfill finding owner from created_by for ownerless findings

Findings promoted before owner-on-promote landed have owner_id NULL and
render as "Unassigned".  Assign them to whoever created them (the promoter),
matching the new promote behavior.  Idempotent; only touches NULL owners.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5d6e7f8090a"
down_revision: Union[str, None] = "b4c5d6e7f809"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE findings SET owner_id = created_by_id "
        "WHERE owner_id IS NULL AND created_by_id IS NOT NULL"
    )


def downgrade() -> None:
    # No-op: we can't tell backfilled owners from intentionally-set ones.
    pass
