"""users.is_active and api_keys.is_active are NOT NULL, true by default

Review 2026-10-23b, PLT-8.  Both were nullable booleans with only a Python
default, and every reader takes NULL for false — so a row written by raw SQL
(a seed, a restore script) that left the column out was silently a disabled
account or a dead key.  The state is now unrepresentable.

**Upgrade**: a NULL becomes ``false`` — what every reader already took it for,
so no account or key changes what it can do — then the column is NOT NULL
with ``server_default true``.
**Downgrade**: the column is nullable again with no server default (as the
chain left it); the rows keep their values.

Revision ID: 9c4e2b7d1a3f
Revises: 7b83a791a783
"""
from alembic import op
import sqlalchemy as sa

revision = "9c4e2b7d1a3f"
down_revision = "7b83a791a783"
branch_labels = None
depends_on = None

_TABLES = ("users", "api_keys")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(sa.text(f"UPDATE {table} SET is_active = false WHERE is_active IS NULL"))
        op.alter_column(
            table, "is_active", existing_type=sa.Boolean(), nullable=False,
            server_default=sa.true(),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table, "is_active", existing_type=sa.Boolean(), nullable=True,
            server_default=None,
        )
