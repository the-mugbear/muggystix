"""add plan generation metadata to test_plans

Revision ID: ba03c988c559
Revises: b46cd59c17f5
Create Date: 2026-05-15 04:25:08.300017

Adds three nullable columns on ``test_plans`` so an AI agent can stamp
its own identity onto the plan it just populated:

* ``generated_by_model`` — model id, e.g. ``claude-opus-4-7``.
* ``generated_by_tool``  — harness/CLI, e.g. ``claude-code``, ``codex``.
* ``prompt_version``     — ``PROMPT_VERSION`` value the agent followed.

All nullable so existing plans (and plans where the agent skips the
PATCH step) keep working — the UI surfaces ``not recorded`` for null.

Note: autogenerate against the real ``networkMapper`` DB also detected
several unrelated index drops/adds (model-vs-DB drift in
``host_filter_views``, ``test_execution_results``, ``web_interfaces``,
etc.) left over from the hand-rolled migrations that the v2.17.0
Alembic baseline absorbed.  Those are a separate cleanup — this
migration is deliberately scoped to its own subject and **only** edits
``test_plans``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ba03c988c559'
down_revision: Union[str, None] = 'b46cd59c17f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('test_plans', sa.Column('generated_by_model', sa.String(length=100), nullable=True))
    op.add_column('test_plans', sa.Column('generated_by_tool', sa.String(length=100), nullable=True))
    op.add_column('test_plans', sa.Column('prompt_version', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('test_plans', 'prompt_version')
    op.drop_column('test_plans', 'generated_by_tool')
    op.drop_column('test_plans', 'generated_by_model')
