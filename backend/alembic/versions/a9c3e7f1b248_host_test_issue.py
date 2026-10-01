"""host_tests.issue_key / issue_title — the weakness a test confirms (v2.445.0).

The issue's identity (``vuln_identity.issue_key``), not a vulnerability row
id: scanner rows are re-created across scans and go with a deleted scan; the
issue on the host does not.
"""
import sqlalchemy as sa
from alembic import op

revision = "a9c3e7f1b248"
down_revision = "f4b8d2a6c917"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("host_tests", sa.Column("issue_key", sa.String(length=600), nullable=True))
    op.add_column("host_tests", sa.Column("issue_title", sa.String(length=500), nullable=True))
    op.create_index("ix_host_test_issue", "host_tests", ["host_id", "issue_key"])


def downgrade():
    op.drop_index("ix_host_test_issue", table_name="host_tests")
    op.drop_column("host_tests", "issue_title")
    op.drop_column("host_tests", "issue_key")
