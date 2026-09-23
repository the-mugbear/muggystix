"""web_paths: content-discovery results as rows (v2.390.0)

ffuf / gobuster / feroxbuster / dirsearch / dirbuster paths were joined into
``ports_v2.service_extrainfo`` — unqueryable, capped at 50, and able to
overwrite nmap's service identification (the tool's "https" beat nmap's
"http" under the longer-name rule and NULLed product/version).  They are rows
now; the parser no longer writes service fields over a named port.

Additive; the downgrade drops the table.  Existing extrainfo strings are left
as they are (they cannot be told from a real nmap extrainfo reliably).

Revision ID: b5d7f9a1c3e4
Revises: a4c6e8f0b2d3
Create Date: 2026-09-23
"""
import sqlalchemy as sa
from alembic import op


revision = "b5d7f9a1c3e4"
down_revision = "a4c6e8f0b2d3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "web_paths",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("host_id", sa.Integer(), sa.ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("port_id", sa.Integer(), sa.ForeignKey("ports_v2.id", ondelete="SET NULL"), nullable=True),
        sa.Column("scan_id", sa.Integer(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("scan_id", "url", name="uq_web_paths_scan_url"),
    )
    for col in ("id", "project_id", "host_id", "port_id", "scan_id", "path", "status_code"):
        op.create_index(f"ix_web_paths_{col}", "web_paths", [col])


def downgrade():
    for col in ("status_code", "path", "scan_id", "port_id", "host_id", "project_id", "id"):
        op.drop_index(f"ix_web_paths_{col}", table_name="web_paths")
    op.drop_table("web_paths")
