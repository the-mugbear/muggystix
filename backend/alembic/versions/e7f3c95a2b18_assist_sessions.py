"""assist_sessions table + FK columns on api_keys and agent_api_calls

Revision ID: e7f3c95a2b18
Revises: d5a2c8b14e93
Create Date: 2026-05-27 23:30:00.000000

Adds the fourth agent surface: read-only, project-scoped, short-TTL
"assist" sessions for interactive host queries that don't fit the
plan-or-recon ceremony.

Three things ship together so a partial deploy can't leave the
auth layer broken:

1. ``assist_sessions`` table — parallel to ``recon_sessions`` /
   ``execution_sessions``.  Project-bound, with environment probe and
   attribution columns mirroring the other workflow sessions so the
   audit story stays symmetric.
2. ``api_keys.assist_session_id`` — fourth scope column.  Mutually
   exclusive with ``test_plan_id`` / ``scope_id`` / ``recon_session_id``
   (enforced by the minting code, not by the DB — same convention as
   the existing three columns).  Indexed because the audit-middleware
   join queries on it.
3. ``agent_api_calls.assist_session_id`` — attribution column for the
   call log.  Indexed (``agent_id``, ``created_at``) and
   (``assist_session_id``, ``created_at``) for the per-session
   timeline view.

ondelete behaviour matches the existing pattern:
* ``api_keys.assist_session_id`` CASCADE — kill a session, the key
  goes with it (an orphaned key for a deleted session would let an
  agent keep reading project data after the human ended the
  session).
* ``agent_api_calls.assist_session_id`` SET NULL — preserve audit
  history when a session is hard-deleted (which v1 never does — the
  ENDED status is the terminal state; rows stay forever).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7f3c95a2b18"
down_revision: Union[str, None] = "d5a2c8b14e93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- assist_sessions table -------------------------------------------
    op.create_table(
        "assist_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "started_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("environment", sa.JSON(), nullable=True),
        sa.Column("environment_probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "environment_probed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("environment_probed_from_ip", sa.String(length=45), nullable=True),
        sa.Column("generated_by_model", sa.String(length=100), nullable=True),
        sa.Column("generated_by_tool", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=20), nullable=True),
    )
    op.create_index(
        "idx_assist_session_project", "assist_sessions", ["project_id"]
    )
    op.create_index(
        "idx_assist_session_status", "assist_sessions", ["status"]
    )

    # --- api_keys.assist_session_id --------------------------------------
    op.add_column(
        "api_keys",
        sa.Column("assist_session_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_api_keys_assist_session_id",
        "api_keys",
        "assist_sessions",
        ["assist_session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_api_keys_assist_session_id",
        "api_keys",
        ["assist_session_id"],
    )

    # --- agent_api_calls.assist_session_id -------------------------------
    op.add_column(
        "agent_api_calls",
        sa.Column("assist_session_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_api_calls_assist_session_id",
        "agent_api_calls",
        "assist_sessions",
        ["assist_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_agent_api_call_assist_created",
        "agent_api_calls",
        ["assist_session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_agent_api_call_assist_created", table_name="agent_api_calls"
    )
    op.drop_constraint(
        "fk_agent_api_calls_assist_session_id",
        "agent_api_calls",
        type_="foreignkey",
    )
    op.drop_column("agent_api_calls", "assist_session_id")

    op.drop_index("ix_api_keys_assist_session_id", table_name="api_keys")
    op.drop_constraint(
        "fk_api_keys_assist_session_id", "api_keys", type_="foreignkey"
    )
    op.drop_column("api_keys", "assist_session_id")

    op.drop_index("idx_assist_session_status", table_name="assist_sessions")
    op.drop_index("idx_assist_session_project", table_name="assist_sessions")
    op.drop_table("assist_sessions")
