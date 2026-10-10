"""delete the notifications of the removed test plans

Test plans went with ``d7e1a9c4b602`` / ``f4b8d2a6c917`` (v2.442.0), but the
notifications they had raised stayed: ``plan_proposed`` ("Test plan … awaiting
approval") and ``plan_ready`` ("… is ready to close").  Nothing writes either
type any more, nothing they name exists, and an unread one counts in the
reader's bell for ever — an analyst walkthrough on 2026-10-10 found six of
them, up to seven weeks old, as the whole of "6 unread".

They are deleted, read or not: a notification about a record that no longer
exists has nowhere to open.

**Downgrade.**  A no-op.  The deleted rows are not restored (their plans are
not either), and the old schema needs nothing from this revision.

Revision ID: d9a3e6b0f4c2
Revises: c8f2d5a9e3b1
"""
import sqlalchemy as sa
from alembic import op

revision = "d9a3e6b0f4c2"
down_revision = "c8f2d5a9e3b1"
branch_labels = None
depends_on = None

# The types no writer produces any more.  A literal list on purpose: a
# migration must keep meaning what it meant the day it was written.
RETIRED_TYPES = ("plan_proposed", "plan_ready")


def upgrade() -> None:
    op.execute(
        sa.text("DELETE FROM notifications WHERE type IN :types")
        .bindparams(sa.bindparam("types", value=RETIRED_TYPES, expanding=True))
    )


def downgrade() -> None:
    # Nothing to undo in the schema; the rows are not recoverable.
    pass
