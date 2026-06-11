"""drop never-written duplicate columns from netexec_results

Revision ID: a7d3e91c45b8
Revises: f3a8d24b9e17
Create Date: 2026-06-11 05:40:00.000000

The 2026-06-10 schema review flagged NetexecResult for duplicating
Host/Port fields.  Verified against the parser: ``_store_netexec_result``
never populates ``domain`` (it writes ``domain_name``), ``os_version``,
``arch``, ``service_banner``, ``service_version``, or ``response_time_ms``,
and nothing reads them — ``domain``/``os_version`` were even exposed in the
netexec API response but always null.  Drops the six dead columns.

Kept: the netexec-specific enumeration columns (shares/users/groups/
policies) — they have no Host/Port equivalent and represent intended
AD-enumeration capture — and the actively-written hostname/domain_name.

Downgrade re-adds the columns as nullable (data is unrecoverable; they
were always empty).

Phase 1.4 of the schema-review remediation.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a7d3e91c45b8'
down_revision: Union[str, None] = 'f3a8d24b9e17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DROPPED = [
    ("domain", sa.String()),
    ("os_version", sa.String()),
    ("arch", sa.String()),
    ("service_banner", sa.Text()),
    ("service_version", sa.String()),
    ("response_time_ms", sa.Float()),
]


def upgrade() -> None:
    for name, _type in _DROPPED:
        op.drop_column("netexec_results", name)


def downgrade() -> None:
    for name, type_ in _DROPPED:
        op.add_column("netexec_results", sa.Column(name, type_, nullable=True))
