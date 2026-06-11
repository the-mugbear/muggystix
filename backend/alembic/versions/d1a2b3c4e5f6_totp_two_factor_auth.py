"""TOTP two-factor authentication

Revision ID: d1a2b3c4e5f6
Revises: c9f4a13e7b56
Create Date: 2026-06-11 19:10:00.000000

Adds TOTP 2FA: three columns on ``users`` (encrypted secret, enabled flag,
confirmed timestamp) and a ``user_recovery_codes`` table for one-time backup
codes.  All additive + nullable/defaulted, so existing rows are unaffected and
2FA is opt-in per user.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1a2b3c4e5f6'
down_revision: Union[str, None] = 'c9f4a13e7b56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('totp_secret_encrypted', sa.Text(), nullable=True))
    op.add_column(
        'users',
        sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.add_column('users', sa.Column('totp_confirmed_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'user_recovery_codes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id', sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_user_recovery_codes_user_id', 'user_recovery_codes', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_user_recovery_codes_user_id', table_name='user_recovery_codes')
    op.drop_table('user_recovery_codes')
    op.drop_column('users', 'totp_confirmed_at')
    op.drop_column('users', 'totp_enabled')
    op.drop_column('users', 'totp_secret_encrypted')
