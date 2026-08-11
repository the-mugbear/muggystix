"""drop the unenforced api_keys.allowed_ips column

Revision ID: e1b7d4c93f52
Revises: c9a41e7b2d68
Create Date: 2026-08-10

``api_keys.allowed_ips`` was declared as an "IP whitelist" and appeared nowhere
else in the backend — no writer, no reader, no enforcement in the auth
dependency. A security control that exists only as a column is worse than no
column at all: it reads like agent-key access is IP-pinned when it never was,
and the next person to look would reasonably assume the check exists.

Verified empty before removal (every row NULL). If key IP-pinning is wanted
later it needs an enforcement point in the auth path, and re-adding the column
is the trivial part.
"""
from alembic import op
import sqlalchemy as sa

revision = "e1b7d4c93f52"
down_revision = "c9a41e7b2d68"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("api_keys", "allowed_ips")


def downgrade() -> None:
    op.add_column("api_keys", sa.Column("allowed_ips", sa.JSON(), nullable=True))
