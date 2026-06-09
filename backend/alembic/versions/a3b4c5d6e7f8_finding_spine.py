"""finding spine: findings + finding_hosts + finding_status_history (foundation 3)

Creates the unified Finding spine.  Purely additive — nothing is dropped
here; the dead SecurityFinding/RiskRecommendation/HostRiskAssessment tables
are removed in the contract phase once reads/writes move to Finding.

Severity/status/source are String columns (not Postgres ENUM) so the
vocabulary can evolve without ALTER TYPE.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id", sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "evidence_annotation_id", sa.Integer(),
            sa.ForeignKey("annotations.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("vuln_id", sa.Integer(), sa.ForeignKey("vulnerabilities.id", ondelete="CASCADE"), nullable=True),
        sa.Column(
            "exec_result_id", sa.Integer(),
            sa.ForeignKey("test_execution_results.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("dedup_key", sa.String(length=255), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_findings_project_id", "findings", ["project_id"])
    op.create_index("ix_findings_severity", "findings", ["severity"])
    op.create_index("ix_findings_status", "findings", ["status"])
    op.create_index("ix_findings_vuln_id", "findings", ["vuln_id"])
    op.create_index("ix_findings_exec_result_id", "findings", ["exec_result_id"])
    op.create_index("ix_findings_dedup_key", "findings", ["dedup_key"])
    op.create_index("idx_finding_project_status", "findings", ["project_id", "status"])

    op.create_table(
        "finding_hosts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_id", sa.Integer(), sa.ForeignKey("findings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("host_id", sa.Integer(), sa.ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("port_id", sa.Integer(), sa.ForeignKey("ports_v2.id", ondelete="SET NULL"), nullable=True),
        sa.Column("host_status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("finding_id", "host_id", name="uq_finding_host"),
    )
    op.create_index("ix_finding_hosts_finding_id", "finding_hosts", ["finding_id"])
    op.create_index("ix_finding_hosts_host_id", "finding_hosts", ["host_id"])

    op.create_table(
        "finding_status_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_id", sa.Integer(), sa.ForeignKey("findings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("changed_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_finding_status_history_finding_id", "finding_status_history", ["finding_id"])


def downgrade() -> None:
    op.drop_table("finding_status_history")
    op.drop_table("finding_hosts")
    op.drop_index("idx_finding_project_status", table_name="findings")
    op.drop_table("findings")
