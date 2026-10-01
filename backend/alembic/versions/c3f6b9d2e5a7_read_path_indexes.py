"""Read-path indexes (review 2026-10-01 R19, R20, N9).

Three groups, all additive:

* **Port substring search (R19).**  The Hosts quick search and the DSL's
  ``service:`` / ``version:`` match ``ILIKE '%term%'`` on ``ports_v2`` — a
  leading wildcard a b-tree cannot serve — and the only trigram indexes were
  on vulnerabilities, web interfaces and notes (``f1d2c3b4a5e6``).  Adds
  ``gin_trgm_ops`` indexes on ``service_name``, ``service_product`` and on the
  "product version" expression ``version:`` matches.  That expression must stay
  textually what ``host_query_predicates.product_version_text`` renders, or
  Postgres will not use the index.

* **Issue lookups (R20).**  ``vulnerabilities.issue_key`` had no index: each
  expanded issue, promotion and ``issue:`` host filter read every vulnerability
  row.  A plain b-tree; the column is at most 600 characters, the same width
  ``findings.dedup_key`` is indexed at, well inside the b-tree entry limit.

* **Foreign keys on the newer tables (N9)** — Postgres does not index a
  referencing column, so deleting a session, a name, a finding or a finding
  endpoint scanned these tables to apply ``SET NULL`` / ``CASCADE``, and
  "this session's tests" was a scan — plus ``port_scan_history (scan_id,
  state_at_scan)`` for the Scans summary's open-port count.

Nothing is dropped.  ``idx_port_scan_history_scan (scan_id)`` is now a prefix
of the new composite, and the duplicate primary-key / ``ip_address`` indexes on
``hosts_v2`` / ``ports_v2`` the review lists are still there: removing an index
is a separate, measured decision.

Postgres: ``CREATE INDEX CONCURRENTLY`` in an autocommit block, as the earlier
index revisions do, so the boot-time ``alembic upgrade head`` takes no lock
that blocks writes while a large table is indexed; ``IF NOT EXISTS`` makes a
re-run after an interrupted build safe (drop an INVALID index by name first).
SQLite (tests / round-trip): the plain b-trees only — no pg_trgm there.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "c3f6b9d2e5a7"
down_revision: Union[str, None] = "b2e5a8c1d4f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table, columns) — declared on the models under the same names.
_BTREE_INDEXES = [
    ("ix_vulnerabilities_issue_key", "vulnerabilities", ["issue_key"]),
    ("ix_host_tests_agent_session_id", "host_tests", ["agent_session_id"]),
    ("ix_host_tests_name_id", "host_tests", ["name_id"]),
    ("ix_agent_proposals_finding_host_id", "agent_proposals", ["finding_host_id"]),
    ("ix_agent_proposals_result_finding_id", "agent_proposals", ["result_finding_id"]),
    ("ix_evidence_records_finding_host_id", "evidence_records", ["finding_host_id"]),
    ("idx_port_scan_history_scan_state", "port_scan_history", ["scan_id", "state_at_scan"]),
]

# (index_name, table, column) — migration-only, like every ``ix_trgm_`` index
# (alembic/env.py ignores the prefix; the test schema has no pg_trgm).
_TRGM_INDEXES = [
    ("ix_trgm_port_service_name", "ports_v2", "service_name"),
    ("ix_trgm_port_service_product", "ports_v2", "service_product"),
]

# Keep in step with ``host_query_predicates.product_version_text``.
_PRODUCT_VERSION_INDEX = "ix_trgm_port_product_version"
_PRODUCT_VERSION_EXPR = "(coalesce(service_product, '') || ' ' || coalesce(service_version, ''))"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        for name, table, cols in _BTREE_INDEXES:
            op.create_index(name, table, cols, if_not_exists=True)
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        for name, table, cols in _BTREE_INDEXES:
            op.create_index(
                name, table, cols,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
        for name, table, column in _TRGM_INDEXES:
            op.create_index(
                name, table, [column],
                postgresql_using="gin",
                postgresql_ops={column: "gin_trgm_ops"},
                postgresql_concurrently=True,
                if_not_exists=True,
            )
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_PRODUCT_VERSION_INDEX} "
            f"ON ports_v2 USING gin ({_PRODUCT_VERSION_EXPR} gin_trgm_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        for name, table, _cols in reversed(_BTREE_INDEXES):
            op.drop_index(name, table_name=table, if_exists=True)
        return

    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_PRODUCT_VERSION_INDEX}")
        for name, table, _column in reversed(_TRGM_INDEXES):
            op.drop_index(
                name, table_name=table,
                postgresql_concurrently=True,
                if_exists=True,
            )
        for name, table, _cols in reversed(_BTREE_INDEXES):
            op.drop_index(
                name, table_name=table,
                postgresql_concurrently=True,
                if_exists=True,
            )
    # pg_trgm stays installed (f1d2c3b4a5e6 created it and other indexes use it).
