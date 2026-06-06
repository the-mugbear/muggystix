"""dns_records.resolver_name column (v2.89.0 — closes #44.1)

Adds ``resolver_name`` to ``dns_records`` so every row can carry the
DNS server that produced it.  Set by the dnsx parser (operator runs
``dnsx -j -resp -r resolvers.txt`` and ships the JSON); left NULL by
the existing CSV / amass paths whose source files don't carry
resolver information.

The column is indexed because the analytical query this unlocks —
"show me the records resolver A returned that resolver B didn't" —
filters by ``resolver_name`` first.  An unindexed column would still
work but would force a full scan; an index keeps it cheap at any
data volume.

Pre-existing dns_records rows get NULL ``resolver_name`` on upgrade
(no backfill is possible — we don't know which resolver answered
historically).  The dnsx parser populates the column on every fresh
ingest going forward.

Revision ID: d5a8b29e0f47
Revises: d7e8b942f06a
Create Date: 2026-06-03

Note: when this migration was authored the local alembic chain had two
heads — ``e2b8c14f9a37`` (the chain CLAUDE.md documents) and
``d7e8b942f06a`` (an ingestion_job dismissed_at column).  Rather than
add a merge revision, this migration depends on ``d7e8b942f06a`` so
it becomes the new single head; ``e2b8c14f9a37`` is already in
``d7e8b942f06a``'s ancestry via the c4d70a8b6e2f → ... lineage.
"""
from alembic import op
import sqlalchemy as sa


revision = "d5a8b29e0f47"
down_revision = "d7e8b942f06a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dns_records",
        sa.Column("resolver_name", sa.String(), nullable=True),
    )
    # Indexed for the "filter by resolver" lookup.  Partial index
    # would be tidier (only non-NULL rows are interesting) but
    # plain b-tree keeps the migration portable across the
    # SQLite test-harness fallback some unit tests still use.
    op.create_index(
        "ix_dns_records_resolver_name",
        "dns_records",
        ["resolver_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_dns_records_resolver_name", table_name="dns_records")
    op.drop_column("dns_records", "resolver_name")
