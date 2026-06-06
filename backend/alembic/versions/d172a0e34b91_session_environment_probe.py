"""environment probe columns on recon_sessions + execution_sessions

Revision ID: d172a0e34b91
Revises: c81a9d2e1f47
Create Date: 2026-05-15 09:30:00.000000

Adds four columns to both ``recon_sessions`` and ``execution_sessions``
so the agent can record (per session, per user) the environment it is
executing from — operating system, shell, available Python, PowerShell
version + execution policy, WSL availability, tools on PATH, etc.

Per-session by design: the same user running from a different machine
re-probes, so plans stay environment-agnostic at the intent level and
the agent picks command flavour from what's actually available right
now.

* ``environment`` — JSON blob of probe results (shape defined by the
  ``EnvironmentSummary`` schema; intentionally loose so the agent can
  include extra observed facts without a schema migration).
* ``environment_probed_at`` — when the probe was recorded.
* ``environment_probed_by_user_id`` — denormalized FK to ``users.id``
  for cheap audit queries ("show me every environment my user has
  reported"); the derivation chain (session → agent → owner_id) is
  the source of truth.  Tamper-evidence: disagreement between the two
  signals something was edited out from under us.
* ``environment_probed_from_ip`` — source IP at probe time (the
  request.client.host the middleware saw).

All four are nullable: existing sessions (created before this
migration) have no probe yet and should not block on one.

Hand-written for the same reason as c81a9d2e1f47 — tightly scoped.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd172a0e34b91'
down_revision: Union[str, None] = 'c81a9d2e1f47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PROBE_COLUMNS = (
    ('environment', sa.JSON(), True),
    ('environment_probed_at', sa.DateTime(timezone=True), True),
    ('environment_probed_by_user_id', sa.Integer(), True),
    ('environment_probed_from_ip', sa.String(length=45), True),
)


def upgrade() -> None:
    for table in ('execution_sessions', 'recon_sessions'):
        for name, type_, nullable in _PROBE_COLUMNS:
            op.add_column(table, sa.Column(name, type_, nullable=nullable))
        op.create_foreign_key(
            f"fk_{table}_environment_probed_by_user_id",
            table,
            'users',
            ['environment_probed_by_user_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    for table in ('execution_sessions', 'recon_sessions'):
        op.drop_constraint(
            f"fk_{table}_environment_probed_by_user_id",
            table,
            type_='foreignkey',
        )
        for name, _type, _nullable in reversed(_PROBE_COLUMNS):
            op.drop_column(table, name)
