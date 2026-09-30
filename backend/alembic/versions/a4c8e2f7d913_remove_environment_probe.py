"""Remove the environment probe (v2.434.0)

The probe (``POST /agent/session/environment``: OS family, shell, tools on
PATH) existed so the server could shape command guidance to the operator's
machine and check it against a recon tool catalogue.  BlueStick no longer
directs an agent's work — the recon planning service and the host-readiness
check are gone — so nothing reads it.  What the probe also carried, the
agent's model and client, stays in ``generated_by_model`` /
``generated_by_tool`` and now comes from the MCP handshake and an optional
self-report.

Drops ``environment``, ``environment_probed_at``,
``environment_probed_by_user_id`` (FK) and ``environment_probed_from_ip`` from
``agent_sessions``, ``execution_sessions`` and ``assist_sessions``.  The
downgrade recreates them empty.

Revision ID: a4c8e2f7d913
Revises: c7d2e9f4a1b6
Create Date: 2026-09-30
"""
import sqlalchemy as sa
from alembic import op


revision = "a4c8e2f7d913"
down_revision = "c7d2e9f4a1b6"
branch_labels = None
depends_on = None


# (table, name of its FK on environment_probed_by_user_id).  agent_sessions and
# assist_sessions declared theirs inline, so Postgres named them.
_TABLES = (
    ("agent_sessions", "agent_sessions_environment_probed_by_user_id_fkey"),
    ("execution_sessions", "fk_execution_sessions_environment_probed_by_user_id"),
    ("assist_sessions", "assist_sessions_environment_probed_by_user_id_fkey"),
)


def upgrade():
    for table, fk in _TABLES:
        op.drop_constraint(fk, table, type_="foreignkey")
        op.drop_column(table, "environment_probed_from_ip")
        op.drop_column(table, "environment_probed_by_user_id")
        op.drop_column(table, "environment_probed_at")
        op.drop_column(table, "environment")


def downgrade():
    for table, fk in _TABLES:
        op.add_column(table, sa.Column("environment", sa.JSON(), nullable=True))
        op.add_column(
            table, sa.Column("environment_probed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table, sa.Column("environment_probed_by_user_id", sa.Integer(), nullable=True),
        )
        op.add_column(
            table, sa.Column("environment_probed_from_ip", sa.String(length=45), nullable=True),
        )
        op.create_foreign_key(
            fk, table, "users", ["environment_probed_by_user_id"], ["id"], ondelete="SET NULL",
        )
