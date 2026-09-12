"""One agent session per operator: phases link to it, the key no longer binds a workflow

v2.337.0 — the four agent entry points (assist / recon / plan generation /
execution) collapse into one project-scoped session.  A key does whatever its
operator may do (that has been true since 2.309.0); what it is *working on* is
recorded per phase — a ``recon_sessions`` row per scope it scans, a
``test_plans`` row per plan it drafts, an ``execution_sessions`` row per plan
it executes — each linked to the session through ``agent_session_id``.

Schema changes:

* ``agent_sessions``: gains ``purpose`` and ``last_activity_at`` (moved from
  ``assist_sessions``); loses ``plan_id`` / ``scope_id`` and the
  workflow↔target CHECK — the target lives on the phase row now.
* ``test_plans.agent_session_id`` (SET NULL): the plan-drafting phase record.
  Backfilled from the ``plan_generation`` sessions that used to point at the
  plan, so history keeps its attribution.
* ``agent_feedback.agent_session_id`` and ``agent_api_calls.agent_session_id``
  (SET NULL): session attribution on every row, backfilled through the
  per-phase ids each row already carried.

Existing sessions keep their legacy ``workflow`` label and their single detail
row; ``recon_sessions`` / ``execution_sessions`` already carried
``agent_session_id`` (non-unique), so no change is needed for them to become
many-to-one.

Revision ID: a8d4f27c1e63
Revises: d9e4b7a2c615
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a8d4f27c1e63"
down_revision: Union[str, None] = "d9e4b7a2c615"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- agent_sessions: purpose + last_activity_at ------------------------
    op.add_column("agent_sessions", sa.Column("purpose", sa.Text(), nullable=True))
    op.add_column(
        "agent_sessions",
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE agent_sessions AS s SET purpose = a.purpose, "
            "last_activity_at = a.last_activity_at "
            "FROM assist_sessions AS a WHERE a.agent_session_id = s.id"
        )
    )

    # --- test_plans.agent_session_id --------------------------------------
    op.add_column(
        "test_plans", sa.Column("agent_session_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_test_plans_agent_session_id", "test_plans", "agent_sessions",
        ["agent_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_test_plans_agent_session_id", "test_plans", ["agent_session_id"])
    # The newest plan_generation session for each plan is the one that drafted
    # it (a resume re-used the same row; a rotate never made a new one).
    op.execute(
        sa.text(
            "UPDATE test_plans AS tp SET agent_session_id = ("
            "  SELECT s.id FROM agent_sessions s "
            "  WHERE s.plan_id = tp.id AND s.workflow = 'plan_generation' "
            "  ORDER BY s.id DESC LIMIT 1"
            ") WHERE tp.agent_session_id IS NULL"
        )
    )

    # --- agent_feedback.agent_session_id ---------------------------------
    op.add_column(
        "agent_feedback", sa.Column("agent_session_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_agent_feedback_agent_session_id", "agent_feedback", "agent_sessions",
        ["agent_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_feedback_agent_session_id", "agent_feedback", ["agent_session_id"]
    )
    for table, column in (
        ("recon_sessions", "recon_session_id"),
        ("assist_sessions", "assist_session_id"),
        ("execution_sessions", "execution_session_id"),
    ):
        op.execute(
            sa.text(
                f"UPDATE agent_feedback AS f SET agent_session_id = d.agent_session_id "
                f"FROM {table} AS d WHERE d.id = f.{column} "
                f"AND f.agent_session_id IS NULL AND d.agent_session_id IS NOT NULL"
            )
        )
    op.execute(
        sa.text(
            "UPDATE agent_feedback AS f SET agent_session_id = tp.agent_session_id "
            "FROM test_plans AS tp WHERE tp.id = f.test_plan_id "
            "AND f.agent_session_id IS NULL AND tp.agent_session_id IS NOT NULL"
        )
    )

    # --- agent_api_calls.agent_session_id --------------------------------
    op.add_column(
        "agent_api_calls", sa.Column("agent_session_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_agent_api_calls_agent_session_id", "agent_api_calls", "agent_sessions",
        ["agent_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "idx_agent_api_call_session_created", "agent_api_calls",
        ["agent_session_id", "created_at"],
    )
    for table, column in (
        ("recon_sessions", "recon_session_id"),
        ("assist_sessions", "assist_session_id"),
        ("execution_sessions", "execution_session_id"),
    ):
        op.execute(
            sa.text(
                f"UPDATE agent_api_calls AS c SET agent_session_id = d.agent_session_id "
                f"FROM {table} AS d WHERE d.id = c.{column} "
                f"AND c.agent_session_id IS NULL AND d.agent_session_id IS NOT NULL"
            )
        )
    op.execute(
        sa.text(
            "UPDATE agent_api_calls AS c SET agent_session_id = tp.agent_session_id "
            "FROM test_plans AS tp WHERE tp.id = c.test_plan_id "
            "AND c.agent_session_id IS NULL AND tp.agent_session_id IS NOT NULL"
        )
    )

    # --- agent_sessions: the per-key target goes ---------------------------
    op.drop_constraint("ck_agent_sessions_workflow_target", "agent_sessions", type_="check")
    op.drop_index("ix_agent_sessions_plan_id", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_scope_id", table_name="agent_sessions")
    op.drop_constraint("fk_agent_sessions_plan_id", "agent_sessions", type_="foreignkey")
    op.drop_constraint("agent_sessions_scope_id_fkey", "agent_sessions", type_="foreignkey")
    op.drop_column("agent_sessions", "plan_id")
    op.drop_column("agent_sessions", "scope_id")


def downgrade() -> None:
    op.add_column("agent_sessions", sa.Column("plan_id", sa.Integer(), nullable=True))
    op.add_column("agent_sessions", sa.Column("scope_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_agent_sessions_plan_id", "agent_sessions", "test_plans",
        ["plan_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "agent_sessions_scope_id_fkey", "agent_sessions", "scopes",
        ["scope_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_agent_sessions_plan_id", "agent_sessions", ["plan_id"])
    op.create_index("ix_agent_sessions_scope_id", "agent_sessions", ["scope_id"])
    # Re-derive the legacy targets from the phase rows.  A consolidated session
    # that opened several phases gets its first one — the old shape had room
    # for exactly one, and the CHECK below needs something there.
    op.execute(
        sa.text(
            "UPDATE agent_sessions AS s SET scope_id = ("
            "  SELECT r.scope_id FROM recon_sessions r "
            "  WHERE r.agent_session_id = s.id ORDER BY r.id LIMIT 1"
            ") WHERE s.workflow = 'recon'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE agent_sessions AS s SET plan_id = ("
            "  SELECT e.test_plan_id FROM execution_sessions e "
            "  WHERE e.agent_session_id = s.id ORDER BY e.id LIMIT 1"
            ") WHERE s.workflow = 'execution'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE agent_sessions AS s SET plan_id = ("
            "  SELECT tp.id FROM test_plans tp "
            "  WHERE tp.agent_session_id = s.id ORDER BY tp.id LIMIT 1"
            ") WHERE s.workflow = 'plan_generation'"
        )
    )
    # Project sessions have no single target; the old CHECK only constrains
    # the four legacy kinds, so they pass it as-is.
    op.create_check_constraint(
        "ck_agent_sessions_workflow_target",
        "agent_sessions",
        "(workflow NOT IN ('execution','plan_generation') OR plan_id IS NOT NULL) "
        "AND (workflow <> 'recon' OR scope_id IS NOT NULL) "
        "AND (workflow <> 'assist' OR (plan_id IS NULL AND scope_id IS NULL))",
    )

    op.drop_index("idx_agent_api_call_session_created", table_name="agent_api_calls")
    op.drop_constraint("fk_agent_api_calls_agent_session_id", "agent_api_calls", type_="foreignkey")
    op.drop_column("agent_api_calls", "agent_session_id")

    op.drop_index("ix_agent_feedback_agent_session_id", table_name="agent_feedback")
    op.drop_constraint("fk_agent_feedback_agent_session_id", "agent_feedback", type_="foreignkey")
    op.drop_column("agent_feedback", "agent_session_id")

    op.drop_index("ix_test_plans_agent_session_id", table_name="test_plans")
    op.drop_constraint("fk_test_plans_agent_session_id", "test_plans", type_="foreignkey")
    op.drop_column("test_plans", "agent_session_id")

    op.execute(
        sa.text(
            "UPDATE assist_sessions AS a SET purpose = COALESCE(a.purpose, s.purpose), "
            "last_activity_at = COALESCE(a.last_activity_at, s.last_activity_at) "
            "FROM agent_sessions AS s WHERE s.id = a.agent_session_id"
        )
    )
    op.drop_column("agent_sessions", "last_activity_at")
    op.drop_column("agent_sessions", "purpose")
