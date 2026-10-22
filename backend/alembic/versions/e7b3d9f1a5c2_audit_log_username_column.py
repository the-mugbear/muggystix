"""The sign-in limiter reads a typed audit_logs.username (backlog 2026-10-22)

Review 2026-10-10, R10: the failed sign-in limits filtered on
``json_extract_path_text(audit_logs.details, 'username')`` — a value read from
a JSON blob in a WHERE over a table kept for ever (the column-vs-blob rule).
The name the event is about now has its own column, set by
``security.log_audit_event`` from ``details["username"]``, and the limiter
filters on it.

**Upgrade**: ADDS ``audit_logs.username`` (varchar 255, nullable) and the index
``ix_audit_logs_username_timestamp``; FILLS the column from each existing row's
``details.username`` (a JSON object's string member; anything else stays NULL),
so the limits count the failures recorded before the upgrade.

**Downgrade**: drops the index and the column.  ``details`` still holds every
name, so nothing is lost.

Revision ID: e7b3d9f1a5c2
Revises: d5a1f3c7e9b2
"""
import sqlalchemy as sa
from alembic import op

revision = "e7b3d9f1a5c2"
down_revision = "d5a1f3c7e9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("username", sa.String(length=255), nullable=True))
    # ``details`` is ``json`` (not ``jsonb``).  Only an object's string member
    # is taken: ``->>`` on another JSON kind answers NULL, and json_typeof
    # keeps a scalar or array from being read at all.
    op.execute(
        """
        UPDATE audit_logs
           SET username = left(details ->> 'username', 255)
         WHERE details IS NOT NULL
           AND json_typeof(details) = 'object'
           AND json_typeof(details -> 'username') = 'string'
        """
    )
    op.create_index("ix_audit_logs_username_timestamp", "audit_logs", ["username", "timestamp"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_username_timestamp", table_name="audit_logs")
    op.drop_column("audit_logs", "username")
