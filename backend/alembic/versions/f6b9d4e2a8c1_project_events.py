"""project_events: a project's changelog

One row per action a project member — or an agent working for one — took on
the project's record: who (or whose agent), what, to what, when.  Written by
``services/project_events.record`` in the transaction of the change itself;
read by project auditors and above.

A new table only: no existing row changes, nothing is refused.  History
starts empty at this revision (decided 2026-11-18: no back-fill from the
per-subject history tables).  The downgrade drops the table and its lines
with it.

Revision ID: f6b9d4e2a8c1
Revises: e5a8c3d1f7b4
Create Date: 2026-11-18
"""
import sqlalchemy as sa
from alembic import op

revision = "f6b9d4e2a8c1"
down_revision = "e5a8c3d1f7b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "agent_session_id", sa.Integer(),
            sa.ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("by_agent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("subject_type", sa.String(24), nullable=True),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("subject_label", sa.String(300), nullable=True),
        sa.Column("summary", sa.String(500), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    # The list's one order and the trim's cut; the two SET NULL parents are
    # found by their column when an account or a session is deleted.
    op.create_index("ix_project_events_project_created", "project_events", ["project_id", "created_at", "id"])
    op.create_index("ix_project_events_actor_user_id", "project_events", ["actor_user_id"])
    op.create_index("ix_project_events_agent_session_id", "project_events", ["agent_session_id"])


def downgrade() -> None:
    op.drop_index("ix_project_events_agent_session_id", table_name="project_events")
    op.drop_index("ix_project_events_actor_user_id", table_name="project_events")
    op.drop_index("ix_project_events_project_created", table_name="project_events")
    op.drop_table("project_events")
