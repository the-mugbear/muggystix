"""Notes are discussion — drop the work-state columns (v2.447.0).

v2.446.0 stopped reading and writing a note's status, assignee, due date and
resolution summary, and the status history.  This removes them from the
schema: ``annotations.status`` (and its ``notestatus`` type), ``assignee_id``,
``due_at``, ``resolution_summary``, and the table
``annotation_status_history``.

IRREVERSIBLE DATA LOSS: the values are gone after the upgrade.  Run
``scripts/backup-db.sh`` first.  The downgrade recreates the schema as the
chain built it — every note ``OPEN``, unassigned, with no history.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c2e6a4f8d103"
down_revision = "a9c3e7f1b248"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("annotation_status_history")
    op.drop_index("ix_annotations_assignee_id", table_name="annotations")
    op.drop_constraint("fk_host_notes_assignee_id_users", "annotations", type_="foreignkey")
    op.drop_column("annotations", "assignee_id")
    op.drop_column("annotations", "due_at")
    op.drop_column("annotations", "resolution_summary")
    op.drop_column("annotations", "status")
    op.execute("DROP TYPE notestatus")


def downgrade():
    notestatus = postgresql.ENUM("OPEN", "IN_PROGRESS", "RESOLVED", name="notestatus")
    notestatus.create(op.get_bind())
    # Existing rows need a value; the column itself carries no default.
    op.add_column("annotations", sa.Column(
        "status", postgresql.ENUM(name="notestatus", create_type=False), nullable=False, server_default="OPEN",
    ))
    op.alter_column("annotations", "status", server_default=None)
    op.add_column("annotations", sa.Column("assignee_id", sa.Integer(), nullable=True))
    op.add_column("annotations", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("annotations", sa.Column("resolution_summary", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_host_notes_assignee_id_users", "annotations", "users", ["assignee_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_annotations_assignee_id", "annotations", ["assignee_id"])

    op.create_table(
        "annotation_status_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("note_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("changed_by_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id", name="host_note_status_history_pkey"),
        sa.ForeignKeyConstraint(["note_id"], ["annotations.id"], name="host_note_status_history_note_id_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"], name="host_note_status_history_changed_by_id_fkey", ondelete="SET NULL"),
    )
    op.create_index("ix_annotation_status_history_id", "annotation_status_history", ["id"])
    op.create_index("ix_annotation_status_history_note_id", "annotation_status_history", ["note_id"])
