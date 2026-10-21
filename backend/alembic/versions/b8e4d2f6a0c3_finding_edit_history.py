"""A finding's history records what an edit changed (v2.519.0)

The owner, 2026-10-21: any project analyst may now edit a finding's title
and report text, and a person's direct edit of the title, severity, owner or
report text was recorded nowhere.  Each such edit now writes a row on the
finding's history naming the FIELD and keeping the value BEFORE and AFTER, so
a reader can see what changed.

**Upgrade**: ADDS ``field`` (String 40), ``before_value`` and ``after_value``
(Text) to ``finding_status_history``, all nullable; a status or endpoint row
leaves them empty.  Existing rows are untouched.

**Downgrade**: DELETES the edit rows (``field`` set) — the old table could
not show what they changed and its reader took every row for a status or
endpoint line — then drops the three columns.

Revision ID: b8e4d2f6a0c3
Revises: a7d3c9e1f5b2
"""
import sqlalchemy as sa
from alembic import op

revision = "b8e4d2f6a0c3"
down_revision = "a7d3c9e1f5b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("finding_status_history", sa.Column("field", sa.String(length=40), nullable=True))
    op.add_column("finding_status_history", sa.Column("before_value", sa.Text(), nullable=True))
    op.add_column("finding_status_history", sa.Column("after_value", sa.Text(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM finding_status_history WHERE field IS NOT NULL")
    op.drop_column("finding_status_history", "after_value")
    op.drop_column("finding_status_history", "before_value")
    op.drop_column("finding_status_history", "field")
