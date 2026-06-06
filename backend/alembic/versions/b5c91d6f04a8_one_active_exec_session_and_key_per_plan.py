"""partial unique indexes: one ACTIVE execution session + active key per plan

Closes finding #2 from the second code review.  Pre-fix the
"one active execution session per plan" and "one active API key per
plan" invariants were enforced only at the application layer
(``test_plans.execute_test_plan`` and ``_mint_plan_agent_key``).
With no row lock and no DB constraint, two concurrent ``/execute`` or
``/resume`` calls could both pass the "is anything active?" check,
both mint live keys, and both insert ACTIVE rows — the agent's
``/execution-context`` resolution then picked an arbitrary winner and
the audit trail split between two sessions.

The plan-row lock added in the same revision serializes well-behaved
Postgres callers, but a partial-unique index is the defense-in-depth
backstop: even if the row lock degrades to a no-op (SQLite under
tests; pathological backend config), the second insert fails outright
and the endpoint returns 409.

Indexes:

  - ``uq_exec_session_plan_active``
        UNIQUE(test_plan_id) WHERE status = 'active'
  - ``uq_api_key_plan_active``
        UNIQUE(test_plan_id) WHERE is_active = TRUE
                              AND test_plan_id IS NOT NULL

Pre-existing duplicate rows would block the index build.  Detect and
fail loudly rather than silently dropping rows: production migration
should clear duplicates first.  Two queries up front identify any
offending plans.

Revision ID: b5c91d6f04a8
Revises: a4f2e8d10c39
Create Date: 2026-06-04
"""
from __future__ import annotations

import logging

from alembic import op
import sqlalchemy as sa


revision = "b5c91d6f04a8"
down_revision = "a4f2e8d10c39"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    dup_sessions = bind.execute(sa.text(
        "SELECT test_plan_id, COUNT(*) c FROM execution_sessions "
        "WHERE status = 'active' GROUP BY test_plan_id HAVING COUNT(*) > 1"
    )).fetchall()
    if dup_sessions:
        # Application-level invariant said this couldn't happen pre-fix,
        # but the race the fix closes COULD produce these rows.  Demand
        # a manual cleanup pass rather than picking a winner here.
        raise RuntimeError(
            "Cannot install uq_exec_session_plan_active — duplicate active "
            f"execution sessions exist for plan(s) {[r[0] for r in dup_sessions]}. "
            "Pause/abandon all but one ACTIVE session per plan before retrying "
            "this migration."
        )
    dup_keys = bind.execute(sa.text(
        "SELECT test_plan_id, COUNT(*) c FROM api_keys "
        "WHERE is_active = TRUE AND test_plan_id IS NOT NULL "
        "GROUP BY test_plan_id HAVING COUNT(*) > 1"
    )).fetchall()
    if dup_keys:
        raise RuntimeError(
            "Cannot install uq_api_key_plan_active — duplicate active API keys "
            f"exist for plan(s) {[r[0] for r in dup_keys]}. "
            "Revoke all but one active key per plan before retrying this migration."
        )

    if is_sqlite:
        # SQLite supports partial unique indexes via the WHERE clause on
        # CREATE INDEX since 3.8.  ``op.create_index(..., postgresql_where=...)``
        # would only emit the predicate for Postgres; use raw SQL so the
        # constraint exists in tests too.
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_exec_session_plan_active "
            "ON execution_sessions (test_plan_id) WHERE status = 'active'"
        )
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_api_key_plan_active "
            "ON api_keys (test_plan_id) "
            "WHERE is_active = 1 AND test_plan_id IS NOT NULL"
        )
    else:
        op.create_index(
            "uq_exec_session_plan_active",
            "execution_sessions",
            ["test_plan_id"],
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        )
        op.create_index(
            "uq_api_key_plan_active",
            "api_keys",
            ["test_plan_id"],
            unique=True,
            postgresql_where=sa.text(
                "is_active = TRUE AND test_plan_id IS NOT NULL"
            ),
        )


def downgrade() -> None:
    op.drop_index("uq_api_key_plan_active", table_name="api_keys")
    op.drop_index(
        "uq_exec_session_plan_active", table_name="execution_sessions"
    )
