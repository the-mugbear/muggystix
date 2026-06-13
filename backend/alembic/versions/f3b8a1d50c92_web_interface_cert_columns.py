"""web_interfaces: promote cert predicates (not_after, self_signed) to columns

Cert expiry and self-signedness were re-parsed from the ``tls_info`` JSON blob on
every insight read (five datetime formats + issuer/subject key probing, per row).
Promote them to typed, indexed columns derived once at ingest so the hygiene
surfaces read columns instead of full-scanning + reparsing the blob, and so a
future DSL/dashboard "expiring certs" filter is indexable.

Backfills existing rows from ``tls_info`` using the same shared derivation the
parsers now use (``app.services.cert_fields.derive_cert_fields``).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.services.cert_fields import derive_cert_fields


revision: str = "f3b8a1d50c92"
down_revision: Union[str, None] = "c3a9d1f80b27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "web_interfaces",
        sa.Column("cert_not_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "web_interfaces",
        sa.Column("cert_self_signed", sa.Boolean(), nullable=True),
    )
    # Index names match SQLAlchemy's create_all default (ix_<table>_<column>)
    # so the migration-built prod schema matches the model-built test schema.
    op.create_index(
        "ix_web_interfaces_cert_not_after", "web_interfaces", ["cert_not_after"]
    )
    op.create_index(
        "ix_web_interfaces_cert_self_signed", "web_interfaces", ["cert_self_signed"]
    )

    # --- Backfill from the existing tls_info blob ---
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, tls_info FROM web_interfaces WHERE tls_info IS NOT NULL"
        )
    ).fetchall()
    for row_id, tls_info in rows:
        not_after, self_signed = derive_cert_fields(tls_info)
        if not_after is None and self_signed is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE web_interfaces "
                "SET cert_not_after = :na, cert_self_signed = :ss WHERE id = :id"
            ),
            {"na": not_after, "ss": self_signed, "id": row_id},
        )


def downgrade() -> None:
    op.drop_index("ix_web_interfaces_cert_self_signed", table_name="web_interfaces")
    op.drop_index("ix_web_interfaces_cert_not_after", table_name="web_interfaces")
    op.drop_column("web_interfaces", "cert_self_signed")
    op.drop_column("web_interfaces", "cert_not_after")
