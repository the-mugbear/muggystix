"""operations_cursors table — per-user/project Operations "since last visit" cursor.

Backs the workbench "Since your last visit" diff (refactor P2).  Plain
transactional table create (no CONCURRENTLY), so it runs identically on
Postgres and the SQLite test backend.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c6d5e4f3a2"
down_revision: Union[str, None] = "a6b5c4d3e2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operations_cursors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "project_id", name="uq_operations_cursor_user_project"),
    )
    op.create_index("ix_operations_cursors_id", "operations_cursors", ["id"])
    op.create_index("ix_operations_cursors_user_id", "operations_cursors", ["user_id"])
    op.create_index("ix_operations_cursors_project_id", "operations_cursors", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_operations_cursors_project_id", table_name="operations_cursors")
    op.drop_index("ix_operations_cursors_user_id", table_name="operations_cursors")
    op.drop_index("ix_operations_cursors_id", table_name="operations_cursors")
    op.drop_table("operations_cursors")
