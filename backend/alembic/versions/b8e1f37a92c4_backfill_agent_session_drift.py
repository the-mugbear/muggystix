"""backfill agent_session_id drift accumulated since v2.116.0

Revision ID: b8e1f37a92c4
Revises: a7d3e91c45b8
Create Date: 2026-06-11 06:30:00.000000

The original expand migration (c2d4e6f8a0b1) backfilled the unified
``agent_sessions`` base rows ONCE, but every session created afterwards left
``agent_session_id`` null on its detail row + key (the write paths never
populated it).  R5 fixes the write paths going forward; this migration cleans
up the drift those rows accumulated in between.

Idempotent: every statement is guarded on ``agent_session_id IS NULL``, so
re-running (or running after the forward-fix is deployed) is a no-op.  Same
temp-correlation-column pattern as c2d4e6f8a0b1 (Postgres-only DDL, matching
this project's migration target).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b8e1f37a92c4'
down_revision: Union[str, None] = 'a7d3e91c45b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Temp correlation columns so a freshly-inserted base row can be linked
    # back to its source detail row (dropped at the end).
    op.add_column("agent_sessions", sa.Column("_bf2_src", sa.String(), nullable=True))
    op.add_column("agent_sessions", sa.Column("_bf2_id", sa.Integer(), nullable=True))

    # --- Base rows for detail rows still missing the link -------------------
    bind.execute(sa.text(
        """
        INSERT INTO agent_sessions
          (workflow, project_id, agent_id, started_by_id, plan_id, status,
           started_at, completed_at, created_at, environment,
           environment_probed_at, environment_probed_by_user_id,
           environment_probed_from_ip, generated_by_model, generated_by_tool,
           prompt_version, notes, _bf2_src, _bf2_id)
        SELECT 'execution', tp.project_id, es.agent_id, es.started_by_id,
               es.test_plan_id, es.status, es.started_at, es.completed_at,
               es.created_at, es.environment, es.environment_probed_at,
               es.environment_probed_by_user_id, es.environment_probed_from_ip,
               es.generated_by_model, es.generated_by_tool, es.prompt_version,
               es.notes, 'exec', es.id
        FROM execution_sessions es
        JOIN test_plans tp ON tp.id = es.test_plan_id
        WHERE es.agent_session_id IS NULL
        """
    ))
    bind.execute(sa.text(
        """
        INSERT INTO agent_sessions
          (workflow, project_id, agent_id, started_by_id, scope_id, status,
           started_at, completed_at, created_at, environment,
           environment_probed_at, environment_probed_by_user_id,
           environment_probed_from_ip, generated_by_model, generated_by_tool,
           prompt_version, notes, _bf2_src, _bf2_id)
        SELECT 'recon', rs.project_id, rs.agent_id, rs.started_by_id,
               rs.scope_id, rs.status, rs.started_at, rs.completed_at,
               rs.started_at, rs.environment, rs.environment_probed_at,
               rs.environment_probed_by_user_id, rs.environment_probed_from_ip,
               rs.generated_by_model, rs.generated_by_tool, rs.prompt_version,
               rs.notes, 'recon', rs.id
        FROM recon_sessions rs
        WHERE rs.agent_session_id IS NULL
        """
    ))
    bind.execute(sa.text(
        """
        INSERT INTO agent_sessions
          (workflow, project_id, agent_id, started_by_id, status,
           started_at, completed_at, created_at, environment,
           environment_probed_at, environment_probed_by_user_id,
           environment_probed_from_ip, generated_by_model, generated_by_tool,
           prompt_version, _bf2_src, _bf2_id)
        SELECT 'assist', a.project_id, a.agent_id, a.started_by_id, a.status,
               a.started_at, a.ended_at, a.started_at, a.environment,
               a.environment_probed_at, a.environment_probed_by_user_id,
               a.environment_probed_from_ip, a.generated_by_model,
               a.generated_by_tool, a.prompt_version, 'assist', a.id
        FROM assist_sessions a
        WHERE a.agent_session_id IS NULL
        """
    ))

    op.create_index("ix_agent_sessions_bf2", "agent_sessions", ["_bf2_src", "_bf2_id"])

    # Link each detail row to its freshly-created base row.
    bind.execute(sa.text(
        "UPDATE execution_sessions SET agent_session_id = "
        "(SELECT g.id FROM agent_sessions g WHERE g._bf2_src='exec' "
        "AND g._bf2_id = execution_sessions.id) WHERE agent_session_id IS NULL"
    ))
    bind.execute(sa.text(
        "UPDATE recon_sessions SET agent_session_id = "
        "(SELECT g.id FROM agent_sessions g WHERE g._bf2_src='recon' "
        "AND g._bf2_id = recon_sessions.id) WHERE agent_session_id IS NULL"
    ))
    bind.execute(sa.text(
        "UPDATE assist_sessions SET agent_session_id = "
        "(SELECT g.id FROM agent_sessions g WHERE g._bf2_src='assist' "
        "AND g._bf2_id = assist_sessions.id) WHERE agent_session_id IS NULL"
    ))

    # --- Repoint api_keys at the right base row (only those still null) -----
    bind.execute(sa.text(
        "UPDATE api_keys SET agent_session_id = "
        "(SELECT rs.agent_session_id FROM recon_sessions rs "
        "WHERE rs.id = api_keys.recon_session_id) "
        "WHERE recon_session_id IS NOT NULL AND agent_session_id IS NULL"
    ))
    bind.execute(sa.text(
        "UPDATE api_keys SET agent_session_id = "
        "(SELECT a.agent_session_id FROM assist_sessions a "
        "WHERE a.id = api_keys.assist_session_id) "
        "WHERE assist_session_id IS NOT NULL AND agent_session_id IS NULL"
    ))
    bind.execute(sa.text(
        "UPDATE api_keys SET agent_session_id = "
        "(SELECT es.agent_session_id FROM execution_sessions es "
        "WHERE es.test_plan_id = api_keys.test_plan_id "
        "AND es.status = 'active' ORDER BY es.id DESC LIMIT 1) "
        "WHERE test_plan_id IS NOT NULL AND agent_session_id IS NULL"
    ))
    # Remaining plan keys (generation phase, no execution session) → create a
    # plan_generation base row and point at it.
    bind.execute(sa.text(
        """
        INSERT INTO agent_sessions
          (workflow, project_id, agent_id, plan_id, status, started_at,
           created_at, _bf2_src, _bf2_id)
        SELECT 'plan_generation', tp.project_id, k.agent_id, k.test_plan_id,
               'active', k.created_at, k.created_at, 'plankey', k.id
        FROM api_keys k
        JOIN test_plans tp ON tp.id = k.test_plan_id
        WHERE k.test_plan_id IS NOT NULL AND k.agent_session_id IS NULL
        """
    ))
    bind.execute(sa.text(
        "UPDATE api_keys SET agent_session_id = "
        "(SELECT g.id FROM agent_sessions g WHERE g._bf2_src='plankey' "
        "AND g._bf2_id = api_keys.id) "
        "WHERE test_plan_id IS NOT NULL AND agent_session_id IS NULL"
    ))

    op.drop_index("ix_agent_sessions_bf2", table_name="agent_sessions")
    op.drop_column("agent_sessions", "_bf2_id")
    op.drop_column("agent_sessions", "_bf2_src")


def downgrade() -> None:
    # Data-only backfill — nothing to reverse (the link columns predate this).
    pass
