"""users: drop the per-account lockout columns

``failed_login_attempts`` and ``locked_until`` held a lockout on the ACCOUNT.
The sign-in lockout is now per (username, client address) and is counted from
the audit log, so nothing reads or writes either column.  A lockout in force
when this runs ends with it; the audit rows that the new lockout counts are
untouched.

Revision ID: a0d7b5e2c9f1
Revises: f9c6a4d1b8e0
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

revision = "a0d7b5e2c9f1"
down_revision = "f9c6a4d1b8e0"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("users", "failed_login_attempts")
    op.drop_column("users", "locked_until")


def downgrade():
    # As the baseline created them: nullable, no server default.  The counts
    # and lock times are not restored (every account reads as not locked).
    op.add_column("users", sa.Column("failed_login_attempts", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
