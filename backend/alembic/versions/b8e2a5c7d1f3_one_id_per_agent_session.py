"""one id per agent session: assist_sessions folded into agent_sessions (review 2026-10-01, B3)

Since v2.337.0 a session is one ``agent_sessions`` row, but every start also
wrote an ``assist_sessions`` "pointer" row because the review routes were
keyed by ITS id.  This revision ends that:

* ``agent_sessions.legacy_assist_session_id`` keeps the old pointer id, so a
  bookmarked ``/assist-sessions/{id}`` link still finds its session.
* A pointer row with no base session (possible only for a row written before
  the base table existed and missed by its backfills) gets one — workflow
  ``assist``, every column taken from the pointer row.
* A base row takes what only its pointer row carried: purpose, the last
  activity time, the model / client / prompt version; a legacy ``assist``
  session also takes the pointer's ended status and time (the pointer row was
  where that workflow's lifecycle lived).
* ``agent_api_calls`` and ``agent_feedback`` rows that named only the pointer
  (``assist_session_id``) are stamped with its session
  (``agent_session_id``); a row that already names a session keeps it.  Both
  ``assist_session_id`` columns are then dropped, and so is ``assist_sessions``.

If two pointer rows share one base session (never written by the application;
not ruled out by the schema), the session keeps the LOWEST pointer id as its
legacy id; calls and feedback of both are kept.

Downgrade recreates ``assist_sessions`` (DDL as the chain built it), one row
per ``project`` / ``assist`` session — under its old id where it had one, a new
id otherwise, which is what the previous release's pages need — and re-adds
both columns.  ``assist_session_id`` is refilled for legacy ``assist``
sessions' calls and feedback only: which individual rows carried it before is
not kept (before v2.337.0 every assist call did; after it, none).

Revision ID: b8e2a5c7d1f3
Revises: a7d1f4b6c9e2
Create Date: 2026-10-01
"""
import sqlalchemy as sa
from alembic import op


revision = "b8e2a5c7d1f3"
down_revision = "a7d1f4b6c9e2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_sessions",
        sa.Column("legacy_assist_session_id", sa.Integer(), nullable=True),
    )

    # 1. A base session for every pointer row that has none.
    op.execute(
        """
        INSERT INTO agent_sessions (
            workflow, project_id, agent_id, started_by_id, status,
            started_at, completed_at, created_at, purpose, last_activity_at,
            generated_by_model, generated_by_tool, prompt_version,
            legacy_assist_session_id
        )
        SELECT
            'assist', a.project_id, a.agent_id, a.started_by_id, a.status,
            a.started_at, a.ended_at, COALESCE(a.started_at, now()), a.purpose,
            a.last_activity_at, a.generated_by_model, a.generated_by_tool,
            a.prompt_version, a.id
        FROM assist_sessions a
        WHERE a.agent_session_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE assist_sessions a
        SET agent_session_id = s.id
        FROM agent_sessions s
        WHERE a.agent_session_id IS NULL AND s.legacy_assist_session_id = a.id
        """
    )

    # 2. The legacy id on every other base row (the lowest, should two pointer
    #    rows share a session).
    op.execute(
        """
        UPDATE agent_sessions s
        SET legacy_assist_session_id = m.assist_id
        FROM (
            SELECT agent_session_id, MIN(id) AS assist_id
            FROM assist_sessions
            GROUP BY agent_session_id
        ) m
        WHERE s.id = m.agent_session_id AND s.legacy_assist_session_id IS NULL
        """
    )

    # 3. What only the pointer row carried.
    op.execute(
        """
        UPDATE agent_sessions s
        SET purpose = COALESCE(s.purpose, a.purpose),
            last_activity_at = GREATEST(s.last_activity_at, a.last_activity_at),
            generated_by_model = COALESCE(s.generated_by_model, a.generated_by_model),
            generated_by_tool = COALESCE(s.generated_by_tool, a.generated_by_tool),
            prompt_version = COALESCE(s.prompt_version, a.prompt_version),
            agent_id = COALESCE(s.agent_id, a.agent_id),
            started_by_id = COALESCE(s.started_by_id, a.started_by_id),
            started_at = COALESCE(s.started_at, a.started_at)
        FROM assist_sessions a
        WHERE a.id = s.legacy_assist_session_id
        """
    )
    op.execute(
        """
        UPDATE agent_sessions s
        SET status = a.status,
            completed_at = COALESCE(s.completed_at, a.ended_at)
        FROM assist_sessions a
        WHERE a.id = s.legacy_assist_session_id
          AND s.workflow = 'assist'
          AND s.status = 'active'
          AND a.status <> 'active'
        """
    )

    # 4. Calls and feedback that named only the pointer row.
    for table in ("agent_api_calls", "agent_feedback"):
        op.execute(
            f"""
            UPDATE {table} t
            SET agent_session_id = a.agent_session_id
            FROM assist_sessions a
            WHERE t.assist_session_id = a.id AND t.agent_session_id IS NULL
            """
        )

    op.create_index(
        "uq_agent_session_legacy_assist", "agent_sessions",
        ["legacy_assist_session_id"], unique=True,
    )

    # 5. The pointer columns and the pointer table.
    op.drop_index("idx_agent_api_call_assist_created", table_name="agent_api_calls")
    op.drop_constraint(
        "fk_agent_api_calls_assist_session_id", "agent_api_calls", type_="foreignkey",
    )
    op.drop_column("agent_api_calls", "assist_session_id")

    op.drop_index("ix_agent_feedback_assist_session_id", table_name="agent_feedback")
    op.drop_constraint(
        "fk_agent_feedback_assist_session", "agent_feedback", type_="foreignkey",
    )
    op.drop_column("agent_feedback", "assist_session_id")

    op.drop_table("assist_sessions")


def downgrade():
    # DDL as the chain built it at a7d1f4b6c9e2 (\d assist_sessions on a
    # database made by ``alembic upgrade``), names included.
    op.create_table(
        "assist_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("started_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generated_by_model", sa.String(length=100), nullable=True),
        sa.Column("generated_by_tool", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=20), nullable=True),
        sa.Column("agent_session_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="assist_sessions_pkey"),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"],
            name="assist_sessions_agent_id_fkey", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name="assist_sessions_project_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["started_by_id"], ["users.id"],
            name="assist_sessions_started_by_id_fkey", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_session_id"], ["agent_sessions.id"],
            name="fk_assist_sessions_agent_session_id", ondelete="CASCADE",
        ),
    )
    op.create_index("idx_assist_session_project", "assist_sessions", ["project_id"])
    op.create_index("idx_assist_session_status", "assist_sessions", ["status"])
    op.create_index(
        "ix_assist_sessions_agent_session_id", "assist_sessions", ["agent_session_id"],
    )
    op.create_index("ix_assist_sessions_id", "assist_sessions", ["id"])

    columns = (
        "project_id, agent_id, started_by_id, status, purpose, started_at, "
        "ended_at, last_activity_at, generated_by_model, generated_by_tool, "
        "prompt_version, agent_session_id"
    )
    values = (
        "s.project_id, s.agent_id, s.started_by_id, s.status, s.purpose, "
        "s.started_at, s.completed_at, s.last_activity_at, s.generated_by_model, "
        "s.generated_by_tool, s.prompt_version, s.id"
    )
    # Sessions that had a pointer row get it back under its old id …
    op.execute(
        f"""
        INSERT INTO assist_sessions (id, {columns})
        SELECT s.legacy_assist_session_id, {values}
        FROM agent_sessions s
        WHERE s.legacy_assist_session_id IS NOT NULL
        """
    )
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('assist_sessions', 'id'),
            COALESCE((SELECT MAX(id) FROM assist_sessions), 0) + 1,
            false
        )
        """
    )
    # … and one started since the upgrade gets a new one: the previous
    # release's session pages read through this table.
    op.execute(
        f"""
        INSERT INTO assist_sessions ({columns})
        SELECT {values}
        FROM agent_sessions s
        WHERE s.legacy_assist_session_id IS NULL
          AND s.workflow IN ('project', 'assist')
        ORDER BY s.id
        """
    )

    op.add_column(
        "agent_api_calls", sa.Column("assist_session_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_api_calls_assist_session_id", "agent_api_calls", "assist_sessions",
        ["assist_session_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column(
        "agent_feedback", sa.Column("assist_session_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_feedback_assist_session", "agent_feedback", "assist_sessions",
        ["assist_session_id"], ["id"], ondelete="SET NULL",
    )
    for table in ("agent_api_calls", "agent_feedback"):
        op.execute(
            f"""
            UPDATE {table} t
            SET assist_session_id = a.id
            FROM assist_sessions a
            JOIN agent_sessions s ON s.id = a.agent_session_id
            WHERE t.agent_session_id = s.id AND s.workflow = 'assist'
            """
        )
    op.create_index(
        "idx_agent_api_call_assist_created", "agent_api_calls",
        ["assist_session_id", "created_at"],
    )
    op.create_index(
        "ix_agent_feedback_assist_session_id", "agent_feedback", ["assist_session_id"],
    )

    op.drop_index("uq_agent_session_legacy_assist", table_name="agent_sessions")
    op.drop_column("agent_sessions", "legacy_assist_session_id")
