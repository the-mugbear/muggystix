"""report_jobs.inputs_fingerprint — what a draft's preview rendered

The report workspace (owner, 2026-10-14) says when a preview is out of date:
the worker records the fingerprint of the inputs a draft's preview rendered
(``client_report_render.inputs_fingerprint`` — the dataset without today's
date, the template's fingerprint, the bytes of every image it names) and the
page compares it with the value as it would be now.

**Upgrade.**  Adds the nullable column.  A preview rendered before it has
none, and the page says it cannot tell whether that preview is current.

**Downgrade.**  Drops the column.

Revision ID: d4b8e2a6c9f3
Revises: c9e4a7b2d6f1
"""
import sqlalchemy as sa
from alembic import op

revision = "d4b8e2a6c9f3"
down_revision = "c9e4a7b2d6f1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("report_jobs", sa.Column("inputs_fingerprint", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("report_jobs", "inputs_fingerprint")
