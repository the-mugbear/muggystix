"""Agent evidence records and agent proposals (v2.436.0)

``evidence_records``: what an agent ran against a host and what came back —
recorded directly, never changed (raw output kept as a file).
``agent_proposals``: a change an agent (or the in-app drafter) proposes to
what the team has concluded or the client report says — a finding's report
text, a new finding, promoting / dismissing a scanner observation, an
endpoint's status — which a person accepts or rejects.

Revision ID: b6d4f1e8c203
Revises: a4c8e2f7d913
Create Date: 2026-09-30
"""
import sqlalchemy as sa
from alembic import op


revision = "b6d4f1e8c203"
down_revision = "a4c8e2f7d913"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evidence_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("host_id", sa.Integer(), sa.ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("finding_id", sa.Integer(), sa.ForeignKey("findings.id", ondelete="SET NULL"), nullable=True),
        sa.Column("finding_host_id", sa.Integer(), sa.ForeignKey("finding_hosts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tool", sa.String(100), nullable=False),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("raw_output_path", sa.String(500), nullable=True),
        sa.Column("raw_output_bytes", sa.Integer(), nullable=True),
        sa.Column("raw_output_preview", sa.Text(), nullable=True),
        sa.Column("observed_ip", sa.String(45), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_session_id", sa.Integer(), sa.ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recorded_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_model", sa.String(100), nullable=True),
        sa.Column("agent_client", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for col in ("id", "project_id", "host_id", "finding_id", "agent_session_id"):
        op.create_index(f"ix_evidence_records_{col}", "evidence_records", [col])

    op.create_table(
        "agent_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("source", sa.String(20), nullable=False, server_default="agent"),
        sa.Column("finding_id", sa.Integer(), sa.ForeignKey("findings.id", ondelete="CASCADE"), nullable=True),
        sa.Column("vulnerability_id", sa.Integer(), sa.ForeignKey("vulnerabilities.id", ondelete="CASCADE"), nullable=True),
        sa.Column("finding_host_id", sa.Integer(), sa.ForeignKey("finding_hosts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("field", sa.String(40), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("evidence_ids", sa.JSON(), nullable=True),
        sa.Column("agent_session_id", sa.Integer(), sa.ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("proposed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_model", sa.String(100), nullable=True),
        sa.Column("agent_client", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("result_finding_id", sa.Integer(), sa.ForeignKey("findings.id", ondelete="SET NULL"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    for col in ("id", "project_id", "finding_id", "vulnerability_id", "agent_session_id"):
        op.create_index(f"ix_agent_proposals_{col}", "agent_proposals", [col])
    op.create_index("idx_agent_proposals_project_status", "agent_proposals", ["project_id", "status"])


def downgrade():
    op.drop_table("agent_proposals")
    op.drop_table("evidence_records")
