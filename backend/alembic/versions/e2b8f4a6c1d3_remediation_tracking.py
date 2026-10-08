"""remediation tracking: who was told about a finding on a host, and where the fix stands

Two new tables, no change to existing data.

``finding_host_remediation`` holds the tracked fields of one finding on one
host (contact, date notified, status, closed date); ``remediation_events`` is
the host's timeline of field changes and notes.  Both are kept by project
admins and are separate from the assessor's own statuses.

Indexes are built inside the upgrade transaction, like every index in this
chain (see CLAUDE.md, "Boot migrations"); the tables are empty.

Revision ID: e2b8f4a6c1d3
Revises: d1a7e3c5b9f2
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "e2b8f4a6c1d3"
down_revision = "d1a7e3c5b9f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "finding_host_remediation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("finding_host_id", sa.Integer(),
                  sa.ForeignKey("finding_hosts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_email", sa.String(254)),
        sa.Column("contact_name", sa.String(200)),
        sa.Column("notified_on", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("closed_on", sa.Date()),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("finding_host_id"),
        sa.CheckConstraint("status IN ('open','closed','deferred')", name="ck_remediation_status"),
    )
    op.create_index("ix_remediation_contact_status", "finding_host_remediation", ["contact_email", "status"])
    op.create_index("ix_remediation_project_status", "finding_host_remediation", ["project_id", "status"])

    op.create_table(
        "remediation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("host_id", sa.Integer(),
                  sa.ForeignKey("hosts_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("finding_id", sa.Integer(), sa.ForeignKey("findings.id", ondelete="SET NULL")),
        sa.Column("finding_host_id", sa.Integer(), sa.ForeignKey("finding_hosts.id", ondelete="SET NULL")),
        sa.Column("finding_title", sa.String(500)),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("field", sa.String(30)),
        sa.Column("old_value", sa.Text()),
        sa.Column("new_value", sa.Text()),
        sa.Column("body", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("agent_session_id", sa.Integer(), sa.ForeignKey("agent_sessions.id", ondelete="SET NULL")),
        sa.Column("request_key", sa.String(100)),
        sa.CheckConstraint("kind IN ('note','change')", name="ck_remediation_event_kind"),
    )
    op.create_index("ix_remediation_event_host", "remediation_events", ["host_id", "occurred_at"])
    op.create_index("ix_remediation_event_finding_host", "remediation_events", ["finding_host_id"])
    op.create_index(
        "uq_remediation_event_request", "remediation_events", ["project_id", "request_key"],
        unique=True, postgresql_where=sa.text("request_key IS NOT NULL"),
    )


def downgrade():
    op.drop_table("remediation_events")
    op.drop_table("finding_host_remediation")
