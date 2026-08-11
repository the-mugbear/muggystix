"""drop the unenforced security_policies table and four dead columns

Revision ID: a2f6b8d0e153
Revises: e1b7d4c93f52
Create Date: 2026-08-10

Orphan sweep (see TODO.md, 2026-08-10).

``security_policies`` advertised a configurable password / session / lockout
policy: minimum length, composition rules, expiry, session timeout, max
concurrent sessions, failed-login lockout, audit retention. **None of it was
ever enforced.** No service read the table, no endpoint exposed it, no UI
touched it, and it held zero rows in every deployment — it was never even
instantiated. Same shape as ``api_keys.allowed_ips``, dropped in 2.240.4: a
schema that claims a security control the system does not implement is worse
than no schema at all, because it reads as reassurance.

Dropping only the 14 policy columns would have left an ``id`` + timestamps
husk that still nothing used, so the table goes.

The four columns are ordinary dead weight:
  * ``network_attributions.cloud_service`` — stranded when the ``cloud:`` DSL
    filter was withdrawn; no reader, and no writer ever populated it.
  * ``users.last_activity_seen_at`` / ``user_sessions.device_info`` /
    ``imported_result_files.imported_at`` — never read anywhere.

Downgrade recreates all of it faithfully (the table per its baseline
definition), but the data is gone — which costs nothing here, since none of
these columns ever held a value the application read.
"""
from alembic import op
import sqlalchemy as sa

revision = "a2f6b8d0e153"
down_revision = "e1b7d4c93f52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(op.f("ix_security_policies_id"), table_name="security_policies")
    op.drop_table("security_policies")

    op.drop_column("network_attributions", "cloud_service")
    op.drop_column("users", "last_activity_seen_at")
    op.drop_column("user_sessions", "device_info")
    op.drop_column("imported_result_files", "imported_at")


def downgrade() -> None:
    op.add_column(
        "imported_result_files",
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_sessions", sa.Column("device_info", sa.String(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("last_activity_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "network_attributions", sa.Column("cloud_service", sa.String(), nullable=True),
    )

    # Mirrors the baseline definition (b46cd59c17f5) so a downgrade lands on
    # the same schema the chain built.
    op.create_table(
        "security_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("password_min_length", sa.Integer(), nullable=True),
        sa.Column("password_require_uppercase", sa.Boolean(), nullable=True),
        sa.Column("password_require_lowercase", sa.Boolean(), nullable=True),
        sa.Column("password_require_numbers", sa.Boolean(), nullable=True),
        sa.Column("password_require_symbols", sa.Boolean(), nullable=True),
        sa.Column("password_expiry_days", sa.Integer(), nullable=True),
        sa.Column("session_timeout_minutes", sa.Integer(), nullable=True),
        sa.Column("max_concurrent_sessions", sa.Integer(), nullable=True),
        sa.Column("max_failed_login_attempts", sa.Integer(), nullable=True),
        sa.Column("lockout_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("audit_retention_days", sa.Integer(), nullable=True),
        sa.Column("require_audit_login", sa.Boolean(), nullable=True),
        sa.Column("require_audit_data_access", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"], ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_security_policies_id"), "security_policies", ["id"], unique=False,
    )
