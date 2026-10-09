"""remediation: a deadline set by hand, and a deferral's review date

Three nullable dates on ``finding_host_remediation``:

* ``due_override_on`` — a deadline a project admin set by hand (an extension
  or an earlier date); when set it is the row's deadline in place of the
  assigned date plus the installation's days for the severity.
* ``deferred_review_on`` — the day a deferred row is to be looked at again.
  Rows deferred before this revision keep NULL (nothing is back-filled: no
  date was ever agreed for them) and are listed as due for review.
* ``deferral_alerted_for`` — the review date an alert was already raised for.

And a check, ``ck_remediation_deferred_review_date``: a review date belongs to
a deferred row.  No data change on upgrade.

The downgrade drops the three columns: every hand-set deadline and every
review date is lost, and the rows read with the policy's deadline again.

Revision ID: d3a0e8b5f2c4
Revises: c2f9d7a4e1b3
Create Date: 2026-10-09
"""
import sqlalchemy as sa
from alembic import op

revision = "d3a0e8b5f2c4"
down_revision = "c2f9d7a4e1b3"
branch_labels = None
depends_on = None

TABLE = "finding_host_remediation"
CHECK = "ck_remediation_deferred_review_date"


def upgrade():
    op.add_column(TABLE, sa.Column("due_override_on", sa.Date(), nullable=True))
    op.add_column(TABLE, sa.Column("deferred_review_on", sa.Date(), nullable=True))
    op.add_column(TABLE, sa.Column("deferral_alerted_for", sa.Date(), nullable=True))
    op.create_check_constraint(CHECK, TABLE, "deferred_review_on IS NULL OR status = 'deferred'")


def downgrade():
    op.drop_constraint(CHECK, TABLE, type_="check")
    op.drop_column(TABLE, "deferral_alerted_for")
    op.drop_column(TABLE, "deferred_review_on")
    op.drop_column(TABLE, "due_override_on")
