"""functional jsonb GIN indexes for agent_api_calls host/ip filters

Revision ID: b2e6d9f04a17
Revises: f1a9c7e3b528
Create Date: 2026-06-09 23:58:00.000000

The activity-tab "did this agent touch host X / IP Y?" filter
(agent_activity.py) used ``cast(referenced_host_ids, String).contains(str(id))``
— a ``LIKE '%5%'`` over the JSON-as-text, which (a) false-matched host 5
against [15, 25, 512] and (b) seq-scanned agent_api_calls, the
highest-write-volume table in the app.

The endpoint now uses a real jsonb containment (``col::jsonb @> '[id]'``).
These functional GIN indexes (jsonb_path_ops supports ``@>``) make that
an index probe.  Built on the ``::jsonb`` cast expression rather than
changing the column type to jsonb — that avoids an ACCESS EXCLUSIVE
table rewrite on a hot table; the json->jsonb cast is IMMUTABLE so the
functional index is valid, and the planner matches the endpoint's
``CAST(col AS JSONB)`` to the index expression.

Note: plain CREATE INDEX takes a brief lock during the build.  On a very
large agent_api_calls table an operator may prefer to build these
``CONCURRENTLY`` by hand (cannot run inside Alembic's transaction).
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b2e6d9f04a17'
down_revision: Union[str, None] = 'f1a9c7e3b528'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_api_calls_referenced_host_ids_gin "
        "ON agent_api_calls USING gin ((referenced_host_ids::jsonb) jsonb_path_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_api_calls_referenced_target_ips_gin "
        "ON agent_api_calls USING gin ((referenced_target_ips::jsonb) jsonb_path_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_api_calls_referenced_target_ips_gin")
    op.execute("DROP INDEX IF EXISTS ix_agent_api_calls_referenced_host_ids_gin")
