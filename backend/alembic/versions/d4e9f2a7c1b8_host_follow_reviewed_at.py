"""host_follows.reviewed_at: when the conclusion was recorded (v2.359.0)

"Changed since review" needs to know WHEN a host was reviewed.  The follow row
had only ``updated_at``, which is an ``onupdate`` column and is bumped by
every write to the row — including ``last_viewed_at`` each time the reviewer
merely opens the host.  A change queue measured against it would reset itself
whenever somebody looked, without anyone re-reviewing anything.

``reviewed_at`` is stamped when the status becomes ``reviewed`` and cleared
when it leaves.  Existing reviewed rows are backfilled from the best evidence
there is (``updated_at``, else ``created_at``): for them the date may be later
than the real review, which under-reports changes rather than inventing them.

Revision ID: d4e9f2a7c1b8
Revises: c3f8a1d2b7e4
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa


revision = "d4e9f2a7c1b8"
down_revision = "c3f8a1d2b7e4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("host_follows", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    # The enum's stored label is the member NAME (SQLAlchemy Enum default).
    op.execute(
        "UPDATE host_follows SET reviewed_at = COALESCE(updated_at, created_at) "
        "WHERE status::text IN ('REVIEWED', 'reviewed')"
    )


def downgrade():
    op.drop_column("host_follows", "reviewed_at")
