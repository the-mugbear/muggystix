"""Add missing FK indexes on confidence / history / netexec tables.

A bunch of hot-path lookups ("show conflicts for host X", "show
confidence for port Y", "list netexec findings by scan") were
silently seq-scanning because PostgreSQL doesn't auto-index FK
columns and the original baseline schema only created primary-key
indexes.  This migration adds the missing ones in one place so
future maintainers don't sprinkle them across feature work.

Indexes added (skipping ones already covered by a composite
``UniqueConstraint`` whose leading column is the FK — e.g.
``host_scan_history(host_id)`` is covered by ``uq_host_scan(host_id,
scan_id)``):

* ``host_confidence(host_id, field_name)``  — hot path: "all
  confidence rows for this host's hostname/os/etc."
* ``host_confidence(scan_id)``               — FK
* ``port_confidence(port_id, field_name)``  — hot path
* ``port_confidence(scan_id)``               — FK
* ``conflict_history(object_type, object_id)`` — polymorphic lookup
* ``conflict_history(previous_scan_id)``     — FK
* ``conflict_history(new_scan_id)``          — FK
* ``conflict_history(resolved_at)``          — timeline queries
* ``data_source_metadata(scan_id)``          — FK
* ``netexec_results(scan_id)``               — FK
* ``netexec_results(host_id)``               — FK
* ``out_of_scope_hosts(scan_id)``            — FK
* ``scan_info(scan_id)``                     — FK
* ``port_scan_history(scan_id)``             — FK (uq_port_scan
  covers port_id leading but not scan_id)

PostgreSQL: built with ``CREATE INDEX CONCURRENTLY`` inside an
autocommit block so the boot-time ``alembic upgrade head`` doesn't
hold an ``ACCESS EXCLUSIVE`` lock while building on a multi-million
row table.  ``IF NOT EXISTS`` makes the migration idempotent if a
prior partial run left a broken index entry behind (the
``CONCURRENTLY`` failure mode that requires ``DROP INDEX
CONCURRENTLY`` to clean up — operators can drop the named index
manually if needed; restart of this migration is then safe).

SQLite (test runs): plain ``CREATE INDEX`` — small tables, no lock
concern.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9d3e6a87f04"
down_revision: Union[str, None] = "a6f4d29e8b15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table, columns)
_INDEXES = [
    ("idx_host_confidence_host_field", "host_confidence", ["host_id", "field_name"]),
    ("idx_host_confidence_scan", "host_confidence", ["scan_id"]),
    ("idx_port_confidence_port_field", "port_confidence", ["port_id", "field_name"]),
    ("idx_port_confidence_scan", "port_confidence", ["scan_id"]),
    ("idx_conflict_history_object", "conflict_history", ["object_type", "object_id"]),
    ("idx_conflict_history_prev_scan", "conflict_history", ["previous_scan_id"]),
    ("idx_conflict_history_new_scan", "conflict_history", ["new_scan_id"]),
    ("idx_conflict_history_resolved_at", "conflict_history", ["resolved_at"]),
    ("idx_data_source_metadata_scan", "data_source_metadata", ["scan_id"]),
    ("idx_netexec_results_scan", "netexec_results", ["scan_id"]),
    ("idx_netexec_results_host", "netexec_results", ["host_id"]),
    ("idx_out_of_scope_hosts_scan", "out_of_scope_hosts", ["scan_id"]),
    ("idx_scan_info_scan", "scan_info", ["scan_id"]),
    ("idx_port_scan_history_scan", "port_scan_history", ["scan_id"]),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # CREATE INDEX CONCURRENTLY can't run inside a transaction —
        # ``autocommit_block`` exits the surrounding txn for the
        # duration so each CREATE is its own statement.  IF NOT EXISTS
        # makes re-runs idempotent.
        with op.get_context().autocommit_block():
            for name, table, cols in _INDEXES:
                op.create_index(
                    name, table, cols,
                    postgresql_concurrently=True,
                    if_not_exists=True,
                )
    else:
        for name, table, cols in _INDEXES:
            op.create_index(name, table, cols, if_not_exists=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for name, table, _cols in reversed(_INDEXES):
                op.drop_index(
                    name, table_name=table,
                    postgresql_concurrently=True,
                    if_exists=True,
                )
    else:
        for name, table, _cols in reversed(_INDEXES):
            op.drop_index(name, table_name=table, if_exists=True)
