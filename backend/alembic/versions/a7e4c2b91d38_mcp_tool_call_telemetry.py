"""mcp_tool_calls — transport-layer telemetry for the MCP surface

Requests the MCP layer rejects never reach /agent/*, so agent_api_calls never
saw them: an unknown tool, arguments that don't fit the advertised schema, a
refused batch, a bad protocol version. That blind spot hid a real defect for two
releases (the environment-probe tool rejected the exact fields the assist prompt
tells agents to send), and nothing in the system could have surfaced it.

Deliberately FK-free — see the model docstring. This is diagnostics about the
transport and must outlive the session, key, and project it describes.

Revision ID: a7e4c2b91d38
Revises: d5c9e1a7f3b2
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = "a7e4c2b91d38"
down_revision = "d5c9e1a7f3b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_tool_calls",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("rpc_method", sa.String(length=64), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.Integer(), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("api_key_prefix", sa.String(length=16), nullable=True),
        sa.Column("source_ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("client_name", sa.String(length=128), nullable=True),
        sa.Column("client_version", sa.String(length=64), nullable=True),
        sa.Column("protocol_version", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_mcp_tool_calls_rpc_method", "mcp_tool_calls", ["rpc_method"])
    op.create_index("ix_mcp_tool_calls_tool_name", "mcp_tool_calls", ["tool_name"])
    op.create_index("ix_mcp_tool_calls_outcome", "mcp_tool_calls", ["outcome"])
    op.create_index("ix_mcp_tool_calls_api_key_prefix", "mcp_tool_calls", ["api_key_prefix"])
    op.create_index("ix_mcp_tool_calls_created_at", "mcp_tool_calls", ["created_at"])
    # The two queries the table exists to answer: what failed recently, and
    # which tool is failing.
    op.create_index(
        "idx_mcp_tool_call_outcome_created", "mcp_tool_calls", ["outcome", "created_at"]
    )
    op.create_index(
        "idx_mcp_tool_call_tool_created", "mcp_tool_calls", ["tool_name", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_mcp_tool_call_tool_created", table_name="mcp_tool_calls")
    op.drop_index("idx_mcp_tool_call_outcome_created", table_name="mcp_tool_calls")
    op.drop_index("ix_mcp_tool_calls_created_at", table_name="mcp_tool_calls")
    op.drop_index("ix_mcp_tool_calls_api_key_prefix", table_name="mcp_tool_calls")
    op.drop_index("ix_mcp_tool_calls_outcome", table_name="mcp_tool_calls")
    op.drop_index("ix_mcp_tool_calls_tool_name", table_name="mcp_tool_calls")
    op.drop_index("ix_mcp_tool_calls_rpc_method", table_name="mcp_tool_calls")
    op.drop_table("mcp_tool_calls")
