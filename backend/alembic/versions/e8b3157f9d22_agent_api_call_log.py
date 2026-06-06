"""agent API call audit log table

Revision ID: e8b3157f9d22
Revises: d172a0e34b91
Create Date: 2026-05-15 11:30:00.000000

Adds ``agent_api_calls`` — one row per inbound /agent/* request that
authenticated with an agent API key.  The capture point is a Starlette
middleware that writes AFTER the response is sent, so the agent's
request loop is unaffected.

Stored: method, path, resolved path params, query, status, response
size, duration, request-body summary (mutations only, capped), and a
parsed-out index of touched host_ids / entry_ids / target_ips so a
reviewer can ask "did the agent query the right hosts?".

Indexes are designed for per-workflow timelines: (agent, time),
(plan, time), (recon_session, time), (execution_session, time),
(project, time).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e8b3157f9d22'
down_revision: Union[str, None] = 'd172a0e34b91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_api_calls',
        sa.Column('id', sa.BigInteger(), primary_key=True),

        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('api_key_id', sa.Integer(), nullable=True),
        sa.Column('api_key_prefix', sa.String(length=16), nullable=True),
        sa.Column('source_ip', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),

        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('test_plan_id', sa.Integer(), nullable=True),
        sa.Column('execution_session_id', sa.Integer(), nullable=True),
        sa.Column('scope_id', sa.Integer(), nullable=True),
        sa.Column('recon_session_id', sa.Integer(), nullable=True),

        sa.Column('method', sa.String(length=8), nullable=False),
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('path_template', sa.Text(), nullable=True),
        sa.Column('path_params', sa.JSON(), nullable=True),
        sa.Column('query_params', sa.JSON(), nullable=True),
        sa.Column('request_body_summary', sa.JSON(), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('response_bytes', sa.Integer(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False),

        sa.Column('referenced_host_ids', sa.JSON(), nullable=True),
        sa.Column('referenced_entry_ids', sa.JSON(), nullable=True),
        sa.Column('referenced_target_ips', sa.JSON(), nullable=True),

        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),

        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['test_plan_id'], ['test_plans.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['execution_session_id'], ['execution_sessions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['scope_id'], ['scopes.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['recon_session_id'], ['recon_sessions.id'], ondelete='SET NULL'),
    )
    op.create_index('idx_agent_api_call_agent_created', 'agent_api_calls', ['agent_id', 'created_at'])
    op.create_index('idx_agent_api_call_plan_created', 'agent_api_calls', ['test_plan_id', 'created_at'])
    op.create_index('idx_agent_api_call_recon_created', 'agent_api_calls', ['recon_session_id', 'created_at'])
    op.create_index('idx_agent_api_call_exec_created', 'agent_api_calls', ['execution_session_id', 'created_at'])
    op.create_index('idx_agent_api_call_project_created', 'agent_api_calls', ['project_id', 'created_at'])
    op.create_index('ix_agent_api_calls_agent_id', 'agent_api_calls', ['agent_id'])
    op.create_index('ix_agent_api_calls_project_id', 'agent_api_calls', ['project_id'])
    op.create_index('ix_agent_api_calls_test_plan_id', 'agent_api_calls', ['test_plan_id'])
    op.create_index('ix_agent_api_calls_execution_session_id', 'agent_api_calls', ['execution_session_id'])
    op.create_index('ix_agent_api_calls_recon_session_id', 'agent_api_calls', ['recon_session_id'])
    op.create_index('ix_agent_api_calls_created_at', 'agent_api_calls', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_agent_api_calls_created_at', 'agent_api_calls')
    op.drop_index('ix_agent_api_calls_recon_session_id', 'agent_api_calls')
    op.drop_index('ix_agent_api_calls_execution_session_id', 'agent_api_calls')
    op.drop_index('ix_agent_api_calls_test_plan_id', 'agent_api_calls')
    op.drop_index('ix_agent_api_calls_project_id', 'agent_api_calls')
    op.drop_index('ix_agent_api_calls_agent_id', 'agent_api_calls')
    op.drop_index('idx_agent_api_call_project_created', 'agent_api_calls')
    op.drop_index('idx_agent_api_call_exec_created', 'agent_api_calls')
    op.drop_index('idx_agent_api_call_recon_created', 'agent_api_calls')
    op.drop_index('idx_agent_api_call_plan_created', 'agent_api_calls')
    op.drop_index('idx_agent_api_call_agent_created', 'agent_api_calls')
    op.drop_table('agent_api_calls')
