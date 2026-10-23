"""an index for "currently resolves to"

Review 2026-10-22, A-R8.  ``dns_name_service.current_binding_condition`` asks,
for each A/AAAA record it judges, which answer is the newest of that record's
name — ordered by ``coalesce(observed_at, created_at) DESC, id DESC``.  Only
``ix_dns_records_name_id`` served it, so every probe read and sorted all the
name's records; it runs in the Names summary (twice over in
``shared_address_condition``), the names list, ``scope:name|none``,
``/coverage`` and the Hosts row's scope.  This partial expression index holds
exactly the rule's rows in the rule's order: the newest answer is its first
entry under the name.

**Upgrade**: builds the index (inside the transaction, as every revision here
does).  No data changes.
**Downgrade**: drops it.

Revision ID: c8e2a4f6b1d9
Revises: b3d9f1a7c5e2
"""
from alembic import op
import sqlalchemy as sa

revision = "c8e2a4f6b1d9"
down_revision = "b3d9f1a7c5e2"
branch_labels = None
depends_on = None

_INDEX = "ix_dns_records_name_latest_binding"
_TABLE = "dns_records"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.create_index(
        _INDEX,
        _TABLE,
        [
            "name_id",
            sa.text("coalesce(observed_at, created_at) DESC"),
            sa.text("id DESC"),
        ],
        postgresql_where=sa.text("record_type IN ('A', 'AAAA')"),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_index(_INDEX, table_name=_TABLE)
