"""notes-as-work — thread fields on host_notes + status-history table.

Refactor P3.  Adds thread-level work columns to ``host_notes`` (meaningful
on the root note of a thread) and a ``host_note_status_history`` audit
table.  ``pinned`` gets a server_default so the NOT NULL add is safe on
existing rows; the application default is False.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d7e6f5a4b3"
down_revision: Union[str, None] = "b8c6d5e4f3a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("host_notes", sa.Column("assignee_id", sa.Integer(), nullable=True))
    op.add_column("host_notes", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("host_notes", sa.Column("note_type", sa.String(length=20), nullable=True))
    op.add_column("host_notes", sa.Column("resolution_summary", sa.Text(), nullable=True))
    op.add_column(
        "host_notes",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_foreign_key(
        "fk_host_notes_assignee_id_users",
        "host_notes", "users",
        ["assignee_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_host_notes_assignee_id", "host_notes", ["assignee_id"])

    op.create_table(
        "host_note_status_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("note_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("changed_by_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["note_id"], ["host_notes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_host_note_status_history_id", "host_note_status_history", ["id"])
    op.create_index("ix_host_note_status_history_note_id", "host_note_status_history", ["note_id"])


def downgrade() -> None:
    op.drop_index("ix_host_note_status_history_note_id", table_name="host_note_status_history")
    op.drop_index("ix_host_note_status_history_id", table_name="host_note_status_history")
    op.drop_table("host_note_status_history")

    op.drop_index("ix_host_notes_assignee_id", table_name="host_notes")
    op.drop_constraint("fk_host_notes_assignee_id_users", "host_notes", type_="foreignkey")
    op.drop_column("host_notes", "pinned")
    op.drop_column("host_notes", "resolution_summary")
    op.drop_column("host_notes", "note_type")
    op.drop_column("host_notes", "due_at")
    op.drop_column("host_notes", "assignee_id")
