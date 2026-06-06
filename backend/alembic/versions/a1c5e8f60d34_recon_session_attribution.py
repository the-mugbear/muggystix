"""executing-agent attribution columns on recon_sessions

Revision ID: a1c5e8f60d34
Revises: f4a72e1c8b03
Create Date: 2026-05-16 00:30:00.000000

Mirrors the v2.28.0 (f4a72e1c8b03) addition of
``generated_by_model``, ``generated_by_tool``, ``prompt_version`` to
``execution_sessions`` — onto ``recon_sessions``.  Closes the
asymmetry that prevented cross-workflow comparison of "everything
claude-opus-4-7 did on this project" in v3 UI work.

All three nullable so pre-2.30 sessions and any agent that doesn't
report attribution stay valid.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c5e8f60d34'
down_revision: Union[str, None] = 'f4a72e1c8b03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'recon_sessions',
        sa.Column('generated_by_model', sa.String(length=100), nullable=True),
    )
    op.add_column(
        'recon_sessions',
        sa.Column('generated_by_tool', sa.String(length=100), nullable=True),
    )
    op.add_column(
        'recon_sessions',
        sa.Column('prompt_version', sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('recon_sessions', 'prompt_version')
    op.drop_column('recon_sessions', 'generated_by_tool')
    op.drop_column('recon_sessions', 'generated_by_model')
