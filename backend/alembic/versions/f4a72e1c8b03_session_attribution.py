"""executing-agent attribution columns on execution_sessions

Revision ID: f4a72e1c8b03
Revises: e8b3157f9d22
Create Date: 2026-05-15 22:00:00.000000

Adds three columns to ``execution_sessions`` so each run can record
which agent / model / prompt version executed the plan.  Symmetric
with the v2.19.0 plan-generation provenance columns on
``test_plans``; lets users compare results from different agents and
models running against the same plan.

All three nullable because pre-2.28 sessions and bundle-exported
runs never report them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4a72e1c8b03'
down_revision: Union[str, None] = 'e8b3157f9d22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'execution_sessions',
        sa.Column('generated_by_model', sa.String(length=100), nullable=True),
    )
    op.add_column(
        'execution_sessions',
        sa.Column('generated_by_tool', sa.String(length=100), nullable=True),
    )
    op.add_column(
        'execution_sessions',
        sa.Column('prompt_version', sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('execution_sessions', 'prompt_version')
    op.drop_column('execution_sessions', 'generated_by_tool')
    op.drop_column('execution_sessions', 'generated_by_model')
