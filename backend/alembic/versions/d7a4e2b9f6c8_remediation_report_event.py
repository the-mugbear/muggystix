"""remediation_events: a timeline entry may record that a contact's list was prepared

``kind = 'report'`` — written when a project admin prepares the remediation
list of one contact (the per-contact report), once per host in it.  Constraint
only; no data change.

Revision ID: d7a4e2b9f6c8
Revises: c6f3d1a8e5b7
Create Date: 2026-10-08
"""
from alembic import op

revision = "d7a4e2b9f6c8"
down_revision = "c6f3d1a8e5b7"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_remediation_event_kind", "remediation_events", type_="check")
    op.create_check_constraint(
        "ck_remediation_event_kind", "remediation_events", "kind IN ('note','change','follow_up','report')",
    )


def downgrade():
    op.execute("DELETE FROM remediation_events WHERE kind = 'report'")
    op.drop_constraint("ck_remediation_event_kind", "remediation_events", type_="check")
    op.create_check_constraint(
        "ck_remediation_event_kind", "remediation_events", "kind IN ('note','change','follow_up')",
    )
