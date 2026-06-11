"""drop the dead data_source_metadata table

Revision ID: c3f9a2e7d810
Revises: d4a1c8f73b69
Create Date: 2026-06-10 21:30:00.000000

The 2026-06-10 schema review found ``data_source_metadata`` (the
``DataSourceMetadata`` model) has ZERO reads or writes anywhere in
``app/`` — no service, endpoint, or parser ever touches it.  It also
duplicated columns already on ``scans`` (scan_type, command_line,
timing).  It survived only because the cascade-FK sweep
(``f1a9c7e3b528``) still listed it as a scan child.

This drops the dead table.  Per the precedent set by
``d6e7f8090a1b_drop_dead_risk_scoring_tables`` (which also removed a
never-populated subsystem), the downgrade is a documented no-op: this
removes a dead feature, not a reversible schema tweak, and recreating
an empty unused table on rollback has no value.

Phase 0b of the schema-review remediation.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c3f9a2e7d810'
down_revision: Union[str, None] = 'd4a1c8f73b69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_source_metadata CASCADE")


def downgrade() -> None:
    # No-op: this drops a dead, never-used table. Recreating an empty
    # unused table on rollback serves no purpose. See the module docstring.
    pass
