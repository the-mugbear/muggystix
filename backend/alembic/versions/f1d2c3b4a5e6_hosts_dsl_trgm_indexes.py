"""pg_trgm GIN indexes for the /hosts boolean query DSL evidence search.

The DSL's evidence fields (``cve:``, ``vuln:``, ``header:``, ``webtitle:``,
``note:``, ``tech:``) resolve to ``ILIKE '%term%'`` substring matches.  A
plain b-tree can't serve a leading-wildcard ILIKE, so without these the
subqueries would sequentially scan ``vulnerabilities`` / ``web_interfaces``
/ ``host_notes`` — fine on a laptop, a cliff at 40k hosts.  A GIN index
with ``gin_trgm_ops`` turns each into a trigram index probe.

The DSL parser already rejects evidence values shorter than 3 characters,
so every indexed lookup has enough trigrams to use the index rather than
falling back to a scan.

Postgres: ``CREATE INDEX CONCURRENTLY`` inside an autocommit block so the
boot-time ``alembic upgrade head`` doesn't hold a lock while building.
SQLite (tests / round-trip): skipped entirely — ILIKE degrades to LIKE
and the test datasets are tiny.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "f1d2c3b4a5e6"
down_revision: Union[str, None] = "c3f9a1b7e240"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table, column) for the plain-column GIN trgm indexes.
_TRGM_INDEXES = [
    ("ix_trgm_vuln_cve_id", "vulnerabilities", "cve_id"),
    ("ix_trgm_vuln_title", "vulnerabilities", "title"),
    ("ix_trgm_web_server_header", "web_interfaces", "server_header"),
    ("ix_trgm_web_title", "web_interfaces", "title"),
    ("ix_trgm_host_notes_body", "host_notes", "body"),
]

# The tech: field matches against the JSON ``technologies`` column cast to
# text, so it needs an expression index rather than a plain-column one.
_TECH_INDEX = "ix_trgm_web_technologies"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        for name, table, column in _TRGM_INDEXES:
            op.create_index(
                name,
                table,
                [column],
                postgresql_using="gin",
                postgresql_ops={column: "gin_trgm_ops"},
                postgresql_concurrently=True,
                if_not_exists=True,
            )
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_TECH_INDEX} "
            "ON web_interfaces USING gin ((technologies::text) gin_trgm_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_TECH_INDEX}")
        for name, table, _column in _TRGM_INDEXES:
            op.drop_index(
                name,
                table_name=table,
                postgresql_concurrently=True,
                if_exists=True,
            )
    # Leave the pg_trgm extension installed — dropping an extension is
    # destructive and other features may come to rely on it.
