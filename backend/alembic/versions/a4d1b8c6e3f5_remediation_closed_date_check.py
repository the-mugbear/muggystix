"""finding_host_remediation: a closed date only on a closed row

Until v2.458.0 the API could store a closed date beside an open or deferred
status (a `closed_on` sent alone, or a status change that conflicted while
its date was accepted).  The service now decides status and date together;
this check stops any other writer.

A row that already holds the contradiction has its date cleared first: its
status says it is not closed, so the date described nothing.  The table is
days old and is expected to hold none.

Revision ID: a4d1b8c6e3f5
Revises: f3c9a5b7d2e4
Create Date: 2026-10-08
"""
from alembic import op

revision = "a4d1b8c6e3f5"
down_revision = "f3c9a5b7d2e4"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE finding_host_remediation SET closed_on = NULL "
               "WHERE closed_on IS NOT NULL AND status <> 'closed'")
    op.create_check_constraint(
        "ck_remediation_closed_date", "finding_host_remediation",
        "closed_on IS NULL OR status = 'closed'",
    )


def downgrade():
    op.drop_constraint("ck_remediation_closed_date", "finding_host_remediation", type_="check")
