"""add web_interfaces.tls_weak_protocol for queryable weak-TLS detection

Revision ID: c2e5a9f10b47
Revises: f1a3c7d29b84
Create Date: 2026-08-11

Promotes "offers a weak TLS protocol (SSLv2/SSLv3/TLS1.0/TLS1.1)" out of the
tls_info blob into a typed, indexed column so the encryption-&-trust systemic
condition and the has:weak_tls /hosts drill-down can filter and aggregate on it
(column-vs-blob policy). Nullable — None means the scan didn't enumerate
protocols; populated at ingest by the web parsers and testssl.
"""
from alembic import op
import sqlalchemy as sa

revision = "c2e5a9f10b47"
down_revision = "f1a3c7d29b84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "web_interfaces",
        sa.Column("tls_weak_protocol", sa.Boolean(), nullable=True),
    )
    op.create_index(
        op.f("ix_web_interfaces_tls_weak_protocol"),
        "web_interfaces", ["tls_weak_protocol"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_web_interfaces_tls_weak_protocol"), table_name="web_interfaces")
    op.drop_column("web_interfaces", "tls_weak_protocol")
