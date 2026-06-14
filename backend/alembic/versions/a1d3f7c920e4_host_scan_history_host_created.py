"""host_scan_history: add host_created flag (new-vs-updated per scan)

The /scans inventory showed a per-scan host count + raw port/vuln metrics —
both already available on the host pages. To make the inventory convey what a
scan actually *introduced*, record whether each (host, scan) observation was
the host's first (created the row) or a re-observation (updated an existing
row). The dedup service already decides this at ingest; this persists it.

Backfills existing rows: the earliest host_scan_history row per host (by
discovered_at, id as tiebreak) is the observation that created that host.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1d3f7c920e4"
down_revision: Union[str, None] = "f3b8a1d50c92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "host_scan_history",
        sa.Column(
            "host_created",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Backfill: the first observation of each host (earliest row) is the one
    # that created it. DISTINCT ON picks exactly one row per host_id.
    op.execute(
        sa.text(
            """
            UPDATE host_scan_history
            SET host_created = true
            WHERE id IN (
                SELECT DISTINCT ON (host_id) id
                FROM host_scan_history
                ORDER BY host_id, discovered_at ASC NULLS FIRST, id ASC
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("host_scan_history", "host_created")
