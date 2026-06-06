"""composite indexes for hot read paths (v2.85.0)

Targeted index adds for the queries identified in the v2.84.x perf
audit:

- ``vulnerabilities (host_id, severity)``: every dashboard hit groups
  vuln counts by host and severity; the v2.83.2 ``has_exploit_available``
  filter joins ``host_id`` then filters ``exploitable`` (which sits in
  the same row as severity, so the composite covers it cheaply).
- ``hosts_v2 (project_id, last_seen)``: drives /staleness + the
  "recent activity" tile on the dashboard.  ``project_id`` was the only
  single-column index; ``last_seen`` had none.
- ``test_plan_entries (host_id, status)``: the host-detail "tests
  against this host" panel filters by ``host_id`` then status; the
  existing ``(test_plan_id, status)`` index doesn't help that query.

Existing composite indexes are left alone — this revision only adds
the missing ones flagged by the audit.

Revision ID: e7a5b9f2c814
Revises: d3f8a91c2e57
Create Date: 2026-05-30
"""
from alembic import op


revision = "e7a5b9f2c814"
down_revision = "d3f8a91c2e57"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_vulnerability_host_severity",
        "vulnerabilities",
        ["host_id", "severity"],
    )
    op.create_index(
        "idx_host_project_last_seen",
        "hosts_v2",
        ["project_id", "last_seen"],
    )
    op.create_index(
        "idx_entry_host_status",
        "test_plan_entries",
        ["host_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_entry_host_status", table_name="test_plan_entries")
    op.drop_index("idx_host_project_last_seen", table_name="hosts_v2")
    op.drop_index("idx_vulnerability_host_severity", table_name="vulnerabilities")
