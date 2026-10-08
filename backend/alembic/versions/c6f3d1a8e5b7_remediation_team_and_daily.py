"""remediation: a team per finding on a host; a daily count per project

* ``finding_host_remediation.team`` — the group that owns the fix (free text).
* ``remediation_daily`` — one row per project per day with its findings on
  hosts by deadline state, written by the worker from the day the installation
  tracks remediation.  It starts empty: a deadline is derived from the current
  timeline and severity, so no earlier day can be reconstructed honestly.

No data change.

Revision ID: c6f3d1a8e5b7
Revises: b5e2c9d7f4a6
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

revision = "c6f3d1a8e5b7"
down_revision = "b5e2c9d7f4a6"
branch_labels = None
depends_on = None

_STATES = ("overdue", "due_soon", "on_track", "not_assigned", "no_deadline", "deferred", "closed")


def upgrade():
    op.add_column("finding_host_remediation", sa.Column("team", sa.String(100)))
    op.create_table(
        "remediation_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        *[sa.Column(name, sa.Integer(), nullable=False, server_default="0") for name in _STATES],
    )
    op.create_index("uq_remediation_daily_project_day", "remediation_daily", ["project_id", "day"], unique=True)
    op.create_index("ix_remediation_daily_day", "remediation_daily", ["day"])


def downgrade():
    op.drop_index("ix_remediation_daily_day", table_name="remediation_daily")
    op.drop_index("uq_remediation_daily_project_day", table_name="remediation_daily")
    op.drop_table("remediation_daily")
    # A team recorded as a change entry has no field in the older schema.
    op.execute("DELETE FROM remediation_events WHERE kind = 'change' AND field = 'team'")
    op.drop_column("finding_host_remediation", "team")
