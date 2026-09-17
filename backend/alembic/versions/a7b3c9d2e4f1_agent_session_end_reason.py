"""agent_sessions.end_reason (v2.343.0)

How a session ended — 'agent' (its own POST /agent/session/end), 'operator'
(End on Agent Runs or the sessions panel), or 'lapsed' (the hourly sweep, after
the key expired past the renewal window).  Until now the only record was a
free-text line appended to ``notes``, which cannot be counted; the reason this
column exists is to count clean exits against abandoned ones, so it is a typed
column (CLAUDE.md column-vs-blob policy).

The backfill reads the ``notes`` lines the three end paths have always written,
so existing rows get the same classification new rows will.  Anything it cannot
classify stays NULL rather than being guessed.

Revision ID: a7b3c9d2e4f1
Revises: c2d9e51f7a84
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa


revision = "a7b3c9d2e4f1"
down_revision = "c2d9e51f7a84"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_sessions",
        sa.Column("end_reason", sa.String(length=16), nullable=True),
    )
    # Backfill from the notes line each path writes (see
    # agent_session_service.end_agent_session): the sweep passes the reason
    # "keys expired past the renewal window", the agent's own exit passes
    # "closed by the agent", and an operator end names the person.
    op.execute(
        """
        UPDATE agent_sessions
           SET end_reason = 'lapsed'
         WHERE status <> 'active'
           AND end_reason IS NULL
           AND notes LIKE '%Session ended by system: keys expired past the renewal window%'
        """
    )
    op.execute(
        """
        UPDATE agent_sessions
           SET end_reason = 'agent'
         WHERE status <> 'active'
           AND end_reason IS NULL
           AND notes LIKE '%Session ended by system: closed by the agent%'
        """
    )
    op.execute(
        """
        UPDATE agent_sessions
           SET end_reason = 'operator'
         WHERE status <> 'active'
           AND end_reason IS NULL
           AND notes LIKE '%Session ended by %'
           AND notes NOT LIKE '%Session ended by system%'
        """
    )


def downgrade():
    op.drop_column("agent_sessions", "end_reason")
