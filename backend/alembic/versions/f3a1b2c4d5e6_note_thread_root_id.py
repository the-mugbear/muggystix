"""host_notes.thread_root_id — persisted thread root (review #5).

Lets activity status filters/counts query by the thread's (root's) status
instead of a reply's, and resolves history by root.  Backfill is portable
(no recursive CTE): root notes point at themselves, then replies inherit
their parent's root iteratively until the graph is closed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a1b2c4d5e6"
down_revision: Union[str, None] = "e2f9a8b7c6d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("host_notes", sa.Column("thread_root_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_host_notes_thread_root_id",
        "host_notes", "host_notes",
        ["thread_root_id"], ["id"],
    )
    op.create_index("ix_host_notes_thread_root_id", "host_notes", ["thread_root_id"])

    bind = op.get_bind()
    # Root notes are their own thread root.
    bind.execute(sa.text(
        "UPDATE host_notes SET thread_root_id = id WHERE parent_id IS NULL"
    ))
    # Replies inherit their parent's root; iterate until the graph closes
    # (depth-bounded loop, safe on Postgres and SQLite alike).
    for _ in range(64):
        result = bind.execute(sa.text(
            """
            UPDATE host_notes AS child
            SET thread_root_id = parent.thread_root_id
            FROM host_notes AS parent
            WHERE child.parent_id = parent.id
              AND child.thread_root_id IS NULL
              AND parent.thread_root_id IS NOT NULL
            """
        )) if bind.dialect.name == "postgresql" else bind.execute(sa.text(
            # SQLite has no UPDATE…FROM in older versions — correlated subquery.
            """
            UPDATE host_notes
            SET thread_root_id = (
                SELECT p.thread_root_id FROM host_notes p
                WHERE p.id = host_notes.parent_id
            )
            WHERE thread_root_id IS NULL
              AND parent_id IS NOT NULL
              AND (SELECT p.thread_root_id FROM host_notes p
                   WHERE p.id = host_notes.parent_id) IS NOT NULL
            """
        ))
        if not result.rowcount:
            break
    # Any leftover (orphaned parent / cycle) falls back to self.
    bind.execute(sa.text(
        "UPDATE host_notes SET thread_root_id = id WHERE thread_root_id IS NULL"
    ))


def downgrade() -> None:
    op.drop_index("ix_host_notes_thread_root_id", table_name="host_notes")
    op.drop_constraint("fk_host_notes_thread_root_id", "host_notes", type_="foreignkey")
    op.drop_column("host_notes", "thread_root_id")
