"""A finding carries a revision; its write answers are merged by it

Backlog 2026-10-22, H-f: every finding write route answers with the whole
finding as it stood when that write committed, and two writes of different
fields can answer in the other order than they committed (a late "Assign to
me" put the old title back after a rename, review 2026-10-10).  The page used
to put an answer only when nothing had written or was reading the record
since the write was sent, and read the finding again otherwise.  With a
number that rises with every write (``models_findings._count_finding_revisions``
is its one writer), the page keeps whichever of two answers is higher.

**Upgrade**: ADDS ``findings.revision`` (integer, NOT NULL, default 1).  Every
existing finding starts at 1.  No data changes.

**Downgrade**: drops the column.

Revision ID: f2b8d4a6c1e3
Revises: d5a1f3c7e9b2
"""
import sqlalchemy as sa
from alembic import op

revision = "f2b8d4a6c1e3"
down_revision = "d5a1f3c7e9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )


def downgrade() -> None:
    op.drop_column("findings", "revision")
