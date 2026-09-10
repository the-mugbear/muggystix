"""host_scan_history: re-backfill hosts that have no creating observation

The masscan parser's bulk path inserted host_scan_history rows without
``host_created`` (server default false), so every host a masscan import
introduced had no observation marked as its creator and the scan reported
0 new hosts on /scans.  a1d3f7c920e4 backfilled the rows that existed when
the column landed; this repeats that rule for hosts created since — by the
masscan path, or by any ingest that predates the flag on a deployment whose
worker ran older code.

Rule (same as a1d3f7c920e4): a host with no ``host_created`` row gets its
earliest observation (discovered_at, id as tiebreak) marked as the creator.
Hosts that already have a creator are untouched, so the migration is
idempotent and never moves a correct flag.

Revision ID: b8d4e6f1a903
Revises: f6c3d8a1b2e4
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8d4e6f1a903"
down_revision: Union[str, None] = "f6c3d8a1b2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE host_scan_history
            SET host_created = true
            WHERE id IN (
                SELECT DISTINCT ON (h.host_id) h.id
                FROM host_scan_history h
                WHERE NOT EXISTS (
                    SELECT 1 FROM host_scan_history x
                    WHERE x.host_id = h.host_id AND x.host_created
                )
                ORDER BY h.host_id, h.discovered_at ASC NULLS FIRST, h.id ASC
            )
            """
        )
    )


def downgrade() -> None:
    # Data repair only: the rows it flagged are indistinguishable from rows
    # the parsers flagged correctly, and un-flagging them would recreate the
    # defect.  Nothing to undo.
    pass
