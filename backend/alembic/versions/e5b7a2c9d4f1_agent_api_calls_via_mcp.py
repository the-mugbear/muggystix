"""agent_api_calls.via_mcp — did the call arrive over the MCP transport

The assist-session list said "Not yet connected" from the environment probe,
which measures the wrong thing: a client can call tools over MCP without ever
posting the probe, and a curl can post the probe without MCP being involved.
The honest signal is an observed, authenticated call — and whether it came
through the MCP loopback or by direct HTTP is only known at the transport, so
the audit row has to carry it.

Nullable: rows written before this column existed are "unknown", not "curl".
No backfill — the distinction was never recorded, and guessing it would put a
false label on the very rows this exists to make honest.

Revision ID: e5b7a2c9d4f1
Revises: c3f7a9d2e4b8
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e5b7a2c9d4f1"
down_revision = "c3f7a9d2e4b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_api_calls",
        sa.Column("via_mcp", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_api_calls", "via_mcp")
