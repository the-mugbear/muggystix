"""activity_cursors table — per-user/project Activity "seen" cursor (RV-6).

Replaces the single ``users.last_activity_seen_at`` column (left in place,
now unused) with a per-(user, project) cursor so marking one project's
Activity feed seen no longer hides unread activity in other projects.
Plain transactional create — runs on Postgres and the SQLite test backend.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e8f7a6b5c4"
down_revision: Union[str, None] = "c9d7e6f5a4b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
    op.create_index("ix_activity_cursors_id", "activity_cursors", ["id"])
    op.create_index("ix_activity_cursors_user_id", "activity_cursors", ["user_id"])
    op.create_index("ix_activity_cursors_project_id", "activity_cursors", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_activity_cursors_project_id", table_name="activity_cursors")
    op.drop_index("ix_activity_cursors_user_id", table_name="activity_cursors")
    op.drop_index("ix_activity_cursors_id", table_name="activity_cursors")
    op.drop_table("activity_cursors")
