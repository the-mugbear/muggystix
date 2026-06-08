"""agent_sessions base table — expand phase (WS2c collapse, part 1/2).

Creates the unified ``agent_sessions`` base every agent API key will point
at, and adds a nullable ``agent_session_id`` to the three detail session
tables + ``api_keys``.  Backfills one base row per existing
ExecutionSession / ReconSession / AssistSession (copying the shared
lifecycle columns) and a ``plan_generation`` base row per plan-bound key
that has no execution session yet, then repoints ``api_keys`` at the right
base row.

This is purely additive — nothing is dropped here.  The contract phase
(next revision) switches deps/minting to read the base, then drops the four
legacy scope FKs on ``api_keys`` and the now-duplicated shared columns on
the detail tables.

NOTE: the backfill touches live session/key rows; rehearse on a staging
copy before deploying to a populated production database.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2d4e6f8a0b1"
down_revision: Union[str, None] = "f3a1b2c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workflow", sa.String(length=20), nullable=False),
        sa.Column(
            "project_id", sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "agent_id", sa.Integer(),
            sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "started_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "plan_id", sa.Integer(),
            # Named to match the model's use_alter FK so metadata drop_all
            # (the test suite) can find it; use_alter breaks the
            # test_plans→recon_sessions→agent_sessions→test_plans cycle.
            sa.ForeignKey(
                "test_plans.id", ondelete="CASCADE",
                name="fk_agent_sessions_plan_id",
            ),
            nullable=True,
        ),
        sa.Column(
            "scope_id", sa.Integer(),
            sa.ForeignKey("scopes.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("environment", sa.JSON(), nullable=True),
        sa.Column("environment_probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "environment_probed_by_user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("environment_probed_from_ip", sa.String(length=45), nullable=True),
        sa.Column("generated_by_model", sa.String(length=100), nullable=True),
        sa.Column("generated_by_tool", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        # Temporary correlation columns for the backfill — dropped at the end.
        sa.Column("_backfill_src", sa.String(length=16), nullable=True),
        sa.Column("_backfill_src_id", sa.Integer(), nullable=True),
    )
    op.create_index("idx_agent_session_project", "agent_sessions", ["project_id"])
    op.create_index(
        "idx_agent_session_workflow_status", "agent_sessions", ["workflow", "status"],
    )
    op.create_index("ix_agent_sessions_plan_id", "agent_sessions", ["plan_id"])
    op.create_index("ix_agent_sessions_scope_id", "agent_sessions", ["scope_id"])

    for table in ("execution_sessions", "recon_sessions", "assist_sessions", "api_keys"):
        op.add_column(
            table,
            sa.Column("agent_session_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table}_agent_session_id",
            table, "agent_sessions",
            ["agent_session_id"], ["id"], ondelete="CASCADE",
        )
        op.create_index(
            f"ix_{table}_agent_session_id", table, ["agent_session_id"],
        )

    bind = op.get_bind()

    # --- Backfill base rows from each detail table (JSON copied DB-side) ---
    bind.execute(sa.text(
        """
        INSERT INTO agent_sessions
          (workflow, project_id, agent_id, started_by_id, plan_id, status,
           started_at, completed_at, created_at, environment,
           environment_probed_at, environment_probed_by_user_id,
           environment_probed_from_ip, generated_by_model, generated_by_tool,
           prompt_version, notes, _backfill_src, _backfill_src_id)
        SELECT 'execution', tp.project_id, es.agent_id, es.started_by_id,
               es.test_plan_id, es.status, es.started_at, es.completed_at,
               es.created_at, es.environment, es.environment_probed_at,
               es.environment_probed_by_user_id, es.environment_probed_from_ip,
               es.generated_by_model, es.generated_by_tool, es.prompt_version,
               es.notes, 'exec', es.id
        FROM execution_sessions es
        JOIN test_plans tp ON tp.id = es.test_plan_id
        """
    ))
    bind.execute(sa.text(
        """
        INSERT INTO agent_sessions
          (workflow, project_id, agent_id, started_by_id, scope_id, status,
           started_at, completed_at, created_at, environment,
           environment_probed_at, environment_probed_by_user_id,
           environment_probed_from_ip, generated_by_model, generated_by_tool,
           prompt_version, notes, _backfill_src, _backfill_src_id)
        SELECT 'recon', rs.project_id, rs.agent_id, rs.started_by_id,
               rs.scope_id, rs.status, rs.started_at, rs.completed_at,
               rs.started_at, rs.environment, rs.environment_probed_at,
               rs.environment_probed_by_user_id, rs.environment_probed_from_ip,
               rs.generated_by_model, rs.generated_by_tool, rs.prompt_version,
               rs.notes, 'recon', rs.id
        FROM recon_sessions rs
        """
    ))
    bind.execute(sa.text(
        """
        INSERT INTO agent_sessions
          (workflow, project_id, agent_id, started_by_id, status,
           started_at, completed_at, created_at, environment,
           environment_probed_at, environment_probed_by_user_id,
           environment_probed_from_ip, generated_by_model, generated_by_tool,
           prompt_version, _backfill_src, _backfill_src_id)
        SELECT 'assist', a.project_id, a.agent_id, a.started_by_id, a.status,
               a.started_at, a.ended_at, a.started_at, a.environment,
               a.environment_probed_at, a.environment_probed_by_user_id,
               a.environment_probed_from_ip, a.generated_by_model,
               a.generated_by_tool, a.prompt_version, 'assist', a.id
        FROM assist_sessions a
        """
    ))

    # Link each detail row to its freshly-created base row.
    bind.execute(sa.text(
        "UPDATE execution_sessions SET agent_session_id = "
        "(SELECT g.id FROM agent_sessions g WHERE g._backfill_src='exec' "
        "AND g._backfill_src_id = execution_sessions.id)"
    ))
    bind.execute(sa.text(
        "UPDATE recon_sessions SET agent_session_id = "
        "(SELECT g.id FROM agent_sessions g WHERE g._backfill_src='recon' "
        "AND g._backfill_src_id = recon_sessions.id)"
    ))
    bind.execute(sa.text(
        "UPDATE assist_sessions SET agent_session_id = "
        "(SELECT g.id FROM agent_sessions g WHERE g._backfill_src='assist' "
        "AND g._backfill_src_id = assist_sessions.id)"
    ))

    # --- Repoint api_keys at the right base row ---
    # Recon keys → their recon session's base row.
    bind.execute(sa.text(
        "UPDATE api_keys SET agent_session_id = "
        "(SELECT rs.agent_session_id FROM recon_sessions rs "
        "WHERE rs.id = api_keys.recon_session_id) "
        "WHERE recon_session_id IS NOT NULL"
    ))
    # Assist keys → their assist session's base row.
    bind.execute(sa.text(
        "UPDATE api_keys SET agent_session_id = "
        "(SELECT a.agent_session_id FROM assist_sessions a "
        "WHERE a.id = api_keys.assist_session_id) "
        "WHERE assist_session_id IS NOT NULL"
    ))
    # Plan keys → the active execution session for that plan, if any.
    bind.execute(sa.text(
        "UPDATE api_keys SET agent_session_id = "
        "(SELECT es.agent_session_id FROM execution_sessions es "
        "WHERE es.test_plan_id = api_keys.test_plan_id "
        "AND es.status = 'active' ORDER BY es.id DESC LIMIT 1) "
        "WHERE test_plan_id IS NOT NULL AND agent_session_id IS NULL"
    ))
    # Remaining plan keys (generation phase, no execution session yet) →
    # create a plan_generation base row and point at it.
    bind.execute(sa.text(
        """
        INSERT INTO agent_sessions
          (workflow, project_id, agent_id, plan_id, status, started_at,
           created_at, _backfill_src, _backfill_src_id)
        SELECT 'plan_generation', tp.project_id, k.agent_id, k.test_plan_id,
               'active', k.created_at, k.created_at, 'plankey', k.id
        FROM api_keys k
        JOIN test_plans tp ON tp.id = k.test_plan_id
        WHERE k.test_plan_id IS NOT NULL AND k.agent_session_id IS NULL
        """
    ))
    bind.execute(sa.text(
        "UPDATE api_keys SET agent_session_id = "
        "(SELECT g.id FROM agent_sessions g WHERE g._backfill_src='plankey' "
        "AND g._backfill_src_id = api_keys.id) "
        "WHERE test_plan_id IS NOT NULL AND agent_session_id IS NULL"
    ))

    # Drop the temporary correlation columns.
    op.drop_column("agent_sessions", "_backfill_src_id")
    op.drop_column("agent_sessions", "_backfill_src")


def downgrade() -> None:
    for table in ("api_keys", "assist_sessions", "recon_sessions", "execution_sessions"):
        op.drop_index(f"ix_{table}_agent_session_id", table_name=table)
        op.drop_constraint(f"fk_{table}_agent_session_id", table, type_="foreignkey")
        op.drop_column(table, "agent_session_id")
    op.drop_index("ix_agent_sessions_scope_id", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_plan_id", table_name="agent_sessions")
    op.drop_index("idx_agent_session_workflow_status", table_name="agent_sessions")
    op.drop_index("idx_agent_session_project", table_name="agent_sessions")
    op.drop_table("agent_sessions")
