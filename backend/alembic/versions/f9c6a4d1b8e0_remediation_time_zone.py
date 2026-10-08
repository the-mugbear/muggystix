"""remediation_policy: the installation's time zone

``time_zone`` (an IANA name, ``UTC`` until a global admin sets another) is the
zone whose calendar day is "today" for every remediation deadline state.  The
assigned and closed dates are entered by hand from a local calendar; compared
with the server's UTC day, rows became overdue in the middle of the afternoon
west of Greenwich.  One column on the one settings row; no data change.

Revision ID: f9c6a4d1b8e0
Revises: e8b5f3c0a7d9
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

revision = "f9c6a4d1b8e0"
down_revision = "e8b5f3c0a7d9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "remediation_policy",
        sa.Column("time_zone", sa.String(length=64), nullable=False, server_default="UTC"),
    )


def downgrade():
    op.drop_column("remediation_policy", "time_zone")
