"""Project-default host filter view

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-06-11 22:10:00.000000

Adds ``host_filter_views.is_project_default`` — a flag a project admin sets on
one saved view to make it the project's default Hosts filter. Additive +
defaulted, so existing rows are unaffected (none default).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'host_filter_views',
        sa.Column('is_project_default', sa.Boolean(), nullable=False, server_default='false'),
    )
    # Partial unique index: at most one default per project (Postgres).  The
    # promote endpoint also enforces this in code; the index is the backstop.
    op.create_index(
        'uq_host_filter_view_project_default',
        'host_filter_views',
        ['project_id'],
        unique=True,
        postgresql_where=sa.text('is_project_default'),
    )


def downgrade() -> None:
    op.drop_index('uq_host_filter_view_project_default', table_name='host_filter_views')
    op.drop_column('host_filter_views', 'is_project_default')
