"""agent_sessions: capability grants; annotations: agent authorship

Two related changes behind the "assist agent may write to hosts assigned to
me" feature.

1. ``agent_sessions.capabilities`` / ``capability_constraint`` — splits *how
   much authority* a key has from *which surface* it can reach (``workflow``).
   Replaces the hand-written per-handler assist deny guards with a positive,
   fail-closed gate.  Existing rows default to ``[]``; plan/execution/recon
   keys resolve to the legacy write set in code (deps.resolve_capabilities),
   so their behaviour is unchanged and no backfill is needed.

2. ``annotations.actor_type`` / ``agent_session_id`` — a note written by an
   agent is stamped ``actor_type='agent'`` and linked to the session that
   produced it.  ``user_id`` remains the operator (the agent acts as them),
   so without this an agent-written note is indistinguishable from a
   hand-typed one.  Existing rows backfill to 'user', which is correct: no
   agent could write notes before this migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9b4e71c2a86"
down_revision: Union[str, None] = "c5f9a2d83b41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column(
            "capabilities",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("capability_constraint", sa.String(length=20), nullable=True),
    )

    op.add_column(
        "annotations",
        sa.Column(
            "actor_type",
            sa.String(length=10),
            nullable=False,
            server_default="user",
        ),
    )
    op.add_column(
        "annotations",
        sa.Column("agent_session_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_annotations_agent_session_id",
        "annotations",
        ["agent_session_id"],
    )
    op.create_foreign_key(
        "fk_annotations_agent_session_id",
        "annotations",
        "agent_sessions",
        ["agent_session_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_annotations_agent_session_id", "annotations", type_="foreignkey"
    )
    op.drop_index("ix_annotations_agent_session_id", table_name="annotations")
    op.drop_column("annotations", "agent_session_id")
    op.drop_column("annotations", "actor_type")
    op.drop_column("agent_sessions", "capability_constraint")
    op.drop_column("agent_sessions", "capabilities")
