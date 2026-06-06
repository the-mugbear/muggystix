"""agent_feedback: add recon_session_id / assist_session_id (v2.85.0)

Pre-v2.85.0 the AgentFeedbackCreate schema only carried test_plan_id /
execution_session_id, so feedback from the recon and assist workflows
could not link back to their session.  The recon prompt was already
passing recon_session_id but Pydantic was silently dropping it.

Adds the two FK columns + sibling indexes (the existing `agent_feedback`
filtering surface is by project_id / status / source — the new columns
are point-lookup material for "show feedback from this session", so
single-column indexes are enough).

Revision ID: d3f8a91c2e57
Revises: c8a5e2f91b47
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa


revision = "d3f8a91c2e57"
down_revision = "c8a5e2f91b47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_feedback",
        sa.Column("recon_session_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_feedback",
        sa.Column("assist_session_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_feedback_recon_session",
        "agent_feedback",
        "recon_sessions",
        ["recon_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_agent_feedback_assist_session",
        "agent_feedback",
        "assist_sessions",
        ["assist_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_feedback_recon_session_id",
        "agent_feedback",
        ["recon_session_id"],
    )
    op.create_index(
        "ix_agent_feedback_assist_session_id",
        "agent_feedback",
        ["assist_session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_feedback_assist_session_id", table_name="agent_feedback")
    op.drop_index("ix_agent_feedback_recon_session_id", table_name="agent_feedback")
    op.drop_constraint(
        "fk_agent_feedback_assist_session", "agent_feedback", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_agent_feedback_recon_session", "agent_feedback", type_="foreignkey"
    )
    op.drop_column("agent_feedback", "assist_session_id")
    op.drop_column("agent_feedback", "recon_session_id")
