"""remediation: an installation-level switch and timelines; deadlines; follow-ups

Remediation tracking becomes a decision of the INSTALLATION, not of a project:

* ``remediation_policy`` — one row (id 1): whether the feature is on, the days
  a contact has per severity (NULL: no deadline) and the "due soon" window.
  Created empty: no row is the defaults with the feature OFF, so an upgraded
  installation shows nothing about remediation until a global admin turns it
  on in System settings.
* ``projects.remediation_in_oversight`` is DROPPED (the per-project opt-in it
  replaced).  The downgrade recreates it false everywhere.
* ``finding_host_remediation`` gains ``closed_due_on`` (the deadline in force
  when the row was closed — an open row's deadline is derived, never stored),
  ``last_follow_up_on`` and the two "already alerted for this deadline" dates.
* a timeline entry may be a recorded follow-up (``kind = 'follow_up'``).

No data change besides the dropped column.  Rows closed before this revision
keep ``closed_due_on`` NULL: they read as closed, neither on time nor late.

Revision ID: b5e2c9d7f4a6
Revises: a4d1b8c6e3f5
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

revision = "b5e2c9d7f4a6"
down_revision = "a4d1b8c6e3f5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "remediation_policy",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("days_critical", sa.Integer()),
        sa.Column("days_high", sa.Integer()),
        sa.Column("days_medium", sa.Integer()),
        sa.Column("days_low", sa.Integer()),
        sa.Column("days_info", sa.Integer()),
        sa.Column("due_soon_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_remediation_policy_one_row"),
    )
    op.drop_column("projects", "remediation_in_oversight")

    op.add_column("finding_host_remediation", sa.Column("closed_due_on", sa.Date()))
    op.add_column("finding_host_remediation", sa.Column("last_follow_up_on", sa.Date()))
    op.add_column("finding_host_remediation", sa.Column("due_soon_alerted_for", sa.Date()))
    op.add_column("finding_host_remediation", sa.Column("overdue_alerted_for", sa.Date()))
    op.create_check_constraint(
        "ck_remediation_closed_due_date", "finding_host_remediation",
        "closed_due_on IS NULL OR status = 'closed'",
    )

    op.drop_constraint("ck_remediation_event_kind", "remediation_events", type_="check")
    op.create_check_constraint(
        "ck_remediation_event_kind", "remediation_events", "kind IN ('note','change','follow_up')",
    )


def downgrade():
    # A recorded follow-up has no place in the older schema.
    op.execute("DELETE FROM remediation_events WHERE kind = 'follow_up'")
    op.drop_constraint("ck_remediation_event_kind", "remediation_events", type_="check")
    op.create_check_constraint(
        "ck_remediation_event_kind", "remediation_events", "kind IN ('note','change')",
    )

    op.drop_constraint("ck_remediation_closed_due_date", "finding_host_remediation", type_="check")
    op.drop_column("finding_host_remediation", "overdue_alerted_for")
    op.drop_column("finding_host_remediation", "due_soon_alerted_for")
    op.drop_column("finding_host_remediation", "last_follow_up_on")
    op.drop_column("finding_host_remediation", "closed_due_on")

    op.add_column(
        "projects",
        sa.Column("remediation_in_oversight", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.drop_table("remediation_policy")
