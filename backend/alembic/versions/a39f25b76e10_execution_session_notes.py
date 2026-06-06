"""notes column on execution_sessions

Revision ID: a39f25b76e10
Revises: c7e3f491a5d2
Create Date: 2026-05-16 12:00:00.000000

v4 beta.7 — adds an operator-writable ``notes`` column to
``execution_sessions`` to mirror the existing column on
``recon_sessions``.  Needed by the symmetric "Abandon" endpoint
(``POST /projects/{id}/execution-sessions/{id}/abandon``), which
appends an audit line identifying who abandoned the session and
when.  Without a notes column the abandon reason has nowhere to
land except the agent_api_calls audit log, which isn't reachable
from the session row.

Pure column-add — nullable, no default, no data migration.  Existing
rows simply have ``notes = NULL`` and the abandon endpoint treats
that as the first-write case.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a39f25b76e10'
down_revision: Union[str, None] = 'c7e3f491a5d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'execution_sessions',
        sa.Column('notes', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('execution_sessions', 'notes')
