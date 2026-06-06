"""composite indexes for hot host/port/scan-history queries

Revision ID: d8f1a4b9c205
Revises: b7c2a09f1d44
Create Date: 2026-05-17 18:30:00.000000

Audit (v2.41.0):  three hot read paths previously fell through to in-table
state-filtering after the single-column index lookup:

  * /hosts list filtered by ``(project_id, state='up')``
  * "open ports for host X" filtered by ``(host_id, state='open')``
  * scan timeline queries on ``host_scan_history`` filtered by ``scan_id``
    and ordered by ``discovered_at``

The single-column indexes (``project_id``, ``host_id``, ``discovered_at``)
were doing the heavy lifting but the planner still had to filter rows from
the index lookup. These composite indexes let the planner serve the common
filter combinations entirely from the index leaf.

``CREATE INDEX IF NOT EXISTS`` is used because some long-running deployments
may have added one of these by hand already.

Use ``CONCURRENTLY`` is NOT available inside an Alembic-managed transaction;
the default behavior here will hold an ACCESS EXCLUSIVE lock briefly on each
table while the index builds.  On a fresh install this is instantaneous; on a
production install with millions of host rows, an operator may want to create
these by hand with ``CREATE INDEX CONCURRENTLY`` first and then stamp the
revision.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd8f1a4b9c205'
down_revision: Union[str, None] = 'b7c2a09f1d44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COMPOSITE_INDEXES = [
    ('idx_host_project_state', 'hosts_v2', ['project_id', 'state']),
    ('idx_port_host_state', 'ports_v2', ['host_id', 'state']),
    (
        'idx_host_scan_history_scan_discovered',
        'host_scan_history',
        ['scan_id', 'discovered_at'],
    ),
]


def upgrade() -> None:
    for index_name, table_name, columns in COMPOSITE_INDEXES:
        column_list = ', '.join(columns)
        op.execute(
            f'CREATE INDEX IF NOT EXISTS {index_name} '
            f'ON {table_name} ({column_list})'
        )


def downgrade() -> None:
    for index_name, _table_name, _columns in COMPOSITE_INDEXES:
        op.execute(f'DROP INDEX IF EXISTS {index_name}')
