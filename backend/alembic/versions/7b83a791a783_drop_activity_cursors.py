"""drop the Activity "seen" cursor

Review 2026-10-23, R-FE-P1 (owner: remove it all).  ``activity_cursors`` was
written by ``POST …/hosts/notes/mark-seen`` (Collaboration, on every visit) and
read only by ``GET …/hosts/notes/unread-count``, which nothing called — no page,
MCP tool, agent route or script.  Both routes and the model went with this
revision.

**Upgrade**: drops the table (its rows only said when each member last opened
Collaboration; nothing reads them).
**Downgrade**: recreates the table empty, as the chain left it — the
``d1e8f7a6b5c4`` table less the two indexes ``a5d9c3e1f7b2`` dropped.

Revision ID: 7b83a791a783
Revises: c8e2a4f6b1d9
"""
from alembic import op
import sqlalchemy as sa

revision = "7b83a791a783"
down_revision = "c8e2a4f6b1d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_activity_cursors_project_id", table_name="activity_cursors")
    op.drop_table("activity_cursors")


def downgrade() -> None:
    op.create_table(
        "activity_cursors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "project_id", name="uq_activity_cursor_user_project"),
    )
    op.create_index("ix_activity_cursors_project_id", "activity_cursors", ["project_id"])
