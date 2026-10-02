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

**Revised before release (review 2026-10-01 B1) — the indexes are built
INSIDE the migration transaction, not ``CONCURRENTLY``.**  As first written
this revision used ``CREATE INDEX CONCURRENTLY`` in an autocommit block, like
the older index revisions.  At boot that is the wrong trade:

* A concurrent build waits for every older snapshot in the database to end.
  The other starting processes, waiting for the migration lock, each held one
  (fixed in ``app/db/init.py``) — and an analyst's open ``psql`` transaction
  or one stuck session would stall the boot the same way, for as long as it
  lives.  A plain build waits only for writers of the table it is indexing.
* The autocommit block COMMITS everything before it.  ``alembic/env.py`` runs
  the whole upgrade as one transaction, so without that commit a failure
  anywhere rolls the database back to the revision it started at — one the
  previous build runs.  With it, an interrupted upgrade was left committed at
  ``b2e5a8c1d4f6``, past what the previous build can run.
* An interrupted concurrent build leaves an INVALID index that ``IF NOT
  EXISTS`` then skips for ever, while alembic records the revision as done.
* What ``CONCURRENTLY`` buys — writes continuing during the build — is worth
  nothing here: this runs while the application is starting, before it serves.

Cost, measured on Postgres 16 with 70,000 hosts, 1,000,000 ports, 500,000
vulnerabilities and 2,000,000 port-history rows: about 15 s for all ten
(12 s of it the product-version trigram index over unusually varied text);
the same indexes built concurrently took about 24 s.  Under a second on a
database of a few thousand hosts.

Safe on every database this can meet:

* never run — builds the ten indexes;
* already run, in either form (a development database) — alembic does not
  run it again; if it is run again by hand, every valid index is kept as is;
* run in its first form and interrupted — an index of one of these names
  that is INVALID is dropped and rebuilt (``_drop_if_invalid``).

SQLite (tests / round-trip): the plain b-trees only — no pg_trgm there.
"""
from typing import Sequence, Union

import sqlalchemy as sa
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


def _drop_if_invalid(bind, name: str) -> None:
    """Drop index ``name`` when it exists and is INVALID — what an interrupted
    ``CREATE INDEX CONCURRENTLY`` leaves.  ``IF NOT EXISTS`` goes by name
    alone, so without this the half-built index would be kept and never used.
    The names are this module's own constants."""
    invalid = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relname = :name AND n.nspname = current_schema() AND NOT i.indisvalid"
        ),
        {"name": name},
    ).scalar()
    if invalid:
        op.execute(f'DROP INDEX "{name}"')


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        for name, table, cols in _BTREE_INDEXES:
            op.create_index(name, table, cols, if_not_exists=True)
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, table, cols in _BTREE_INDEXES:
        _drop_if_invalid(bind, name)
        op.create_index(name, table, cols, if_not_exists=True)
    for name, table, column in _TRGM_INDEXES:
        _drop_if_invalid(bind, name)
        op.create_index(
            name, table, [column],
            postgresql_using="gin",
            postgresql_ops={column: "gin_trgm_ops"},
            if_not_exists=True,
        )
    _drop_if_invalid(bind, _PRODUCT_VERSION_INDEX)
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {_PRODUCT_VERSION_INDEX} "
        f"ON ports_v2 USING gin ({_PRODUCT_VERSION_EXPR} gin_trgm_ops)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        for name, table, _cols in reversed(_BTREE_INDEXES):
            op.drop_index(name, table_name=table, if_exists=True)
        return

    # In the transaction too, for the same reason: no commit mid-chain.
    op.execute(f"DROP INDEX IF EXISTS {_PRODUCT_VERSION_INDEX}")
    for name, table, _column in reversed(_TRGM_INDEXES):
        op.drop_index(name, table_name=table, if_exists=True)
    for name, table, _cols in reversed(_BTREE_INDEXES):
        op.drop_index(name, table_name=table, if_exists=True)
    # pg_trgm stays installed (f1d2c3b4a5e6 created it and other indexes use it).
