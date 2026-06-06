"""scans.start_time index for cross-project SOC activity queries

Revision ID: d5a2c8b14e93
Revises: b9d3e6a87f04
Create Date: 2026-05-26 19:00:00.000000

The new ``/api/v1/activity/scans-at`` endpoint serves SOC-correlation
queries that filter by a time window across ALL projects the caller
can see — there is no project_id in the path, so the planner can't
prune by project first.  A single-column btree on ``scans.start_time``
lets the planner pick the small slice of rows whose scan window could
overlap the requested timestamp, then in-memory filter on end_time
and on the accessible-project_ids list.

Composite ``(start_time, end_time)`` was considered but rejected:
the second column adds size for a near-zero planner win.  Most
callers narrow to a tolerance of seconds-to-minutes; the time prune
alone reduces the candidate set from "all scans ever" to "scans
within ~5 min".  A separate ``end_time`` index is also unnecessary
because we always anchor on start_time first.

``CREATE INDEX IF NOT EXISTS`` so partial reruns are safe.  Not
using CONCURRENTLY here — that requires running outside a
transaction which Alembic-managed revisions can't do without
``autocommit_block`` ceremony, and the scans table is small enough
that the ACCESS EXCLUSIVE lock at index build is sub-second on
realistic deployments.  Operators with very large scans tables can
manually run::

    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scans_start_time
        ON scans (start_time);

then ``alembic stamp d5a2c8b14e93``.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "d5a2c8b14e93"
down_revision: Union[str, None] = "b9d3e6a87f04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "idx_scans_start_time"
TABLE_NAME = "scans"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        f"ON {TABLE_NAME} (start_time)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
