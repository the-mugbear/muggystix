"""host_query_history table — recent boolean queries per user/project.

Backs the Hosts command-bar "recent queries" dropdown.  Plain
transactional table create (no CONCURRENTLY), so it runs identically on
Postgres and the SQLite test backend.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6b5c4d3e2f1"
down_revision: Union[str, None] = "f1d2c3b4a5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "host_query_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("q", sa.Text(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_host_query_history_id", "host_query_history", ["id"])
    op.create_index("ix_host_query_history_user_id", "host_query_history", ["user_id"])
    op.create_index("ix_host_query_history_project_id", "host_query_history", ["project_id"])
    op.create_index(
        "ix_host_query_history_user_project_created",
        "host_query_history",
        ["user_id", "project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_host_query_history_user_project_created", table_name="host_query_history")
    op.drop_index("ix_host_query_history_project_id", table_name="host_query_history")
    op.drop_index("ix_host_query_history_user_id", table_name="host_query_history")
    op.drop_index("ix_host_query_history_id", table_name="host_query_history")
    op.drop_table("host_query_history")
