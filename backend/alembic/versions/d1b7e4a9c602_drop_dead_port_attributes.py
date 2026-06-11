"""drop the dead port_attributes table

Revision ID: d1b7e4a9c602
Revises: c3f9a2e7d810
Create Date: 2026-06-10 22:10:00.000000

The 2026-06-10 schema review found ``port_attributes`` (the
``PortAttribute`` model) has ZERO writers and ZERO queries anywhere in
``app/`` — only a relationship definition on ``Port.attributes`` that
nothing ever reads.  It is the dead twin of ``host_attributes``, which
the vulnerability service does actively write.

Drops the dead table.  Downgrade is a documented no-op, matching the
``data_source_metadata`` / risk-scoring-drop precedent: this removes a
dead table, not a reversible schema tweak.

Phase 1.2 of the schema-review remediation.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd1b7e4a9c602'
down_revision: Union[str, None] = 'c3f9a2e7d810'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS port_attributes CASCADE")


def downgrade() -> None:
    # No-op: this drops a dead, never-used table. See the module docstring.
    pass
