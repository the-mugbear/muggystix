"""client reports: report_profiles, reports, report_files (v2.380.0)

The findings-first client report (Quarto): a per-project profile of
engagement details, the reports themselves (draft → issued → superseded, with
the frozen snapshot an addendum is compared against) and the rendered files
an issued report keeps.  See app/db/models_reports.py.

New tables only; the downgrade drops them.

Revision ID: b7d2f4a6c8e1
Revises: a1c4e7b9d2f3
Create Date: 2026-09-23
"""
import sqlalchemy as sa
from alembic import op


revision = "b7d2f4a6c8e1"
down_revision = "a1c4e7b9d2f3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "report_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_name", sa.String(255), nullable=True),
        sa.Column("classification", sa.String(100), nullable=True),
        sa.Column("engagement_type", sa.String(100), nullable=True),
        sa.Column("testers", sa.JSON(), nullable=True),
        sa.Column("distribution", sa.JSON(), nullable=True),
        sa.Column("system_description", sa.Text(), nullable=True),
        sa.Column("template", sa.String(100), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_report_profiles_id", "report_profiles", ["id"])
    op.create_index("ix_report_profiles_project_id", "report_profiles", ["project_id"], unique=True)

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("template", sa.String(100), nullable=False),
        sa.Column("baseline_report_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revision_of_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="SET NULL"), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("executive_summary", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("template_fingerprint", sa.String(64), nullable=True),
        sa.Column("quarto_version", sa.String(40), nullable=True),
        sa.Column("render_status", sa.String(20), nullable=True),
        sa.Column("render_error", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("issued_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "number", name="uq_report_project_number"),
    )
    op.create_index("ix_reports_id", "reports", ["id"])
    op.create_index("ix_reports_project_id", "reports", ["project_id"])
    op.create_index("idx_reports_project_status", "reports", ["project_id", "status"])

    op.create_table(
        "report_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("report_id", "format", name="uq_report_file_format"),
    )
    op.create_index("ix_report_files_id", "report_files", ["id"])
    op.create_index("ix_report_files_report_id", "report_files", ["report_id"])


def downgrade():
    op.drop_index("ix_report_files_report_id", table_name="report_files")
    op.drop_index("ix_report_files_id", table_name="report_files")
    op.drop_table("report_files")
    op.drop_index("idx_reports_project_status", table_name="reports")
    op.drop_index("ix_reports_project_id", table_name="reports")
    op.drop_index("ix_reports_id", table_name="reports")
    op.drop_table("reports")
    op.drop_index("ix_report_profiles_project_id", table_name="report_profiles")
    op.drop_index("ix_report_profiles_id", table_name="report_profiles")
    op.drop_table("report_profiles")
