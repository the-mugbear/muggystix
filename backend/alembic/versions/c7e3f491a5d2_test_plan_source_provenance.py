"""typed source-provenance columns on test_plans

Revision ID: c7e3f491a5d2
Revises: a1c5e8f60d34
Create Date: 2026-05-15 14:00:00.000000

v3 alpha.3 — adds typed source provenance to ``test_plans`` so the v3
UI can render where a plan came from (which recon run, which manual
host set, or which filter criteria) without inferring from
``filter_criteria`` alone.

Discriminated by ``source_kind``:

    source_kind             populated column(s)
    ----------------        ---------------------------------------
    'recon_session'         source_recon_session_id (FK)
    'manual_hosts'          source_host_ids (int[])
    'filter_set'            existing filter_criteria column is reused
    'inherited'             source_plan_id (FK to test_plans)
    'unspecified'           none — applied to pre-alpha.3 rows

Existing rows default to ``'unspecified'`` so the migration is a pure
column-add and never destroys information.  The UI treats
``'unspecified'`` as "(provenance not recorded)" rather than as an
error state.

The four payload columns are mutually exclusive at the application
layer but not at the DB layer — keeping the constraint in code lets
the contract evolve (e.g. adding a new ``'multiple'`` kind that
populates two columns) without a follow-up migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7e3f491a5d2'
down_revision: Union[str, None] = 'a1c5e8f60d34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ``source_kind`` is a string rather than an enum so the contract
    # can add new kinds without a follow-up migration.  Default is
    # 'unspecified' so existing rows get a non-null value on column-add.
    op.add_column(
        'test_plans',
        sa.Column(
            'source_kind',
            sa.String(length=30),
            nullable=False,
            server_default='unspecified',
        ),
    )
    op.add_column(
        'test_plans',
        sa.Column('source_recon_session_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_test_plans_source_recon_session',
        'test_plans',
        'recon_sessions',
        ['source_recon_session_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'idx_test_plan_source_recon',
        'test_plans',
        ['source_recon_session_id'],
    )

    # List of host_ids the plan was scoped to at creation time.  Stored
    # as JSON for portability — Postgres ARRAY would give no extra
    # integrity here (FKs aren't enforced on array elements) and would
    # break SQLite test runs.  The application layer validates that
    # referenced hosts exist when the plan is created.
    op.add_column(
        'test_plans',
        sa.Column('source_host_ids', sa.JSON(), nullable=True),
    )

    # Self-reference for 'inherited' (a plan derived from another plan,
    # e.g. a re-run with adjusted entries).  Not used yet but defined
    # so the discriminator's shape is complete.
    op.add_column(
        'test_plans',
        sa.Column('source_plan_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_test_plans_source_plan',
        'test_plans',
        'test_plans',
        ['source_plan_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_test_plans_source_plan', 'test_plans', type_='foreignkey',
    )
    op.drop_column('test_plans', 'source_plan_id')
    op.drop_column('test_plans', 'source_host_ids')
    op.drop_index('idx_test_plan_source_recon', table_name='test_plans')
    op.drop_constraint(
        'fk_test_plans_source_recon_session',
        'test_plans',
        type_='foreignkey',
    )
    op.drop_column('test_plans', 'source_recon_session_id')
    op.drop_column('test_plans', 'source_kind')
