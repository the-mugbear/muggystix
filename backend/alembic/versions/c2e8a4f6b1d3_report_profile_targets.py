"""report_profiles: application / thick-client / other target lists (v2.382.0)

The client report's System Description follows the original template: the
scope's networks and domains, plus application URLs / API endpoints, thick
clients and other targets where they apply.  The profile carries the defaults
for the three free-text lists (a report's own copy lives in reports.settings,
JSON, so it needs no column).

Additive; the downgrade drops the columns.

Revision ID: c2e8a4f6b1d3
Revises: b7d2f4a6c8e1
Create Date: 2026-09-23
"""
import sqlalchemy as sa
from alembic import op


revision = "c2e8a4f6b1d3"
down_revision = "b7d2f4a6c8e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("report_profiles", sa.Column("applications", sa.Text(), nullable=True))
    op.add_column("report_profiles", sa.Column("thick_clients", sa.Text(), nullable=True))
    op.add_column("report_profiles", sa.Column("other_targets", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("report_profiles", "other_targets")
    op.drop_column("report_profiles", "thick_clients")
    op.drop_column("report_profiles", "applications")
