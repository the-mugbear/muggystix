"""Every foreign key into a table whose rows are deleted in bulk has an index
led by the child column (review 2026-10-10, H23).

Postgres enforces ``ON DELETE CASCADE`` / ``SET NULL`` with one lookup of the
child table PER DELETED PARENT ROW, by the child column.  With no index led by
that column each lookup reads the whole child table: deleting a scan that
brought 300,000 ports ran 300,000 sequential scans of ``finding_hosts``
(``finding_hosts.port_id``), and every scan delete read every host and every
port once (``last_updated_scan_id``).  Revision ``a4f8c2e6b0d1`` added the four
that were missing; this test keeps a new foreign key from reopening it.

The parents named here are the ones deleted many rows at a time: a scan, its
ports and hosts (a hand delete, a failed import's cleanup, a project delete),
and agent keys (every session end or renewal, a user delete).  A partial
index does not count: the lookup's ``WHERE child = $1`` does not imply its
predicate.

Read from the catalog of the suite's own database (built from the models);
``alembic check`` in the gate keeps the models and the migration chain equal.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import USING_POSTGRES

BULK_DELETED_PARENTS = ("scans", "ports_v2", "hosts_v2", "api_keys")

pytestmark = pytest.mark.skipif(not USING_POSTGRES, reason="reads pg_constraint / pg_index")

_UNINDEXED = text("""
    SELECT c.confrelid::regclass::text AS parent,
           c.conrelid::regclass::text  AS child,
           a.attname                   AS child_column
      FROM pg_constraint c
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
     WHERE c.contype = 'f'
       AND c.connamespace = current_schema()::regnamespace
       AND c.confrelid::regclass::text = ANY(:parents)
       AND NOT EXISTS (
            SELECT 1 FROM pg_index i
             WHERE i.indrelid = c.conrelid
               AND i.indkey[0] = c.conkey[1]
               AND i.indpred IS NULL
               AND i.indisvalid)
     ORDER BY 1, 2, 3
""")


def test_every_foreign_key_into_a_bulk_deleted_table_has_an_index_led_by_its_column(db_session):
    missing = [
        f"{row.child}.{row.child_column} -> {row.parent}"
        for row in db_session.execute(_UNINDEXED, {"parents": list(BULK_DELETED_PARENTS)})
    ]
    assert missing == [], (
        "Foreign keys with no index led by the child column — deleting one parent row scans the "
        "whole child table. Add index=True on the column AND an index in a revision: "
        + ", ".join(missing)
    )


def test_the_query_can_see_a_missing_index(db_session):
    """The catalog query is not vacuous: a foreign key made without an index
    is reported, and stops being reported once it has one."""
    db_session.execute(text(
        "CREATE TABLE _fk_probe (id serial PRIMARY KEY, "
        "scan_id integer REFERENCES scans(id) ON DELETE SET NULL)"
    ))
    try:
        found = [(r.child, r.child_column) for r in db_session.execute(_UNINDEXED, {"parents": ["scans"]})]
        assert ("_fk_probe", "scan_id") in found
        db_session.execute(text("CREATE INDEX _fk_probe_partial ON _fk_probe (scan_id) WHERE scan_id > 5"))
        found = [(r.child, r.child_column) for r in db_session.execute(_UNINDEXED, {"parents": ["scans"]})]
        assert ("_fk_probe", "scan_id") in found, "a partial index must not count"
        db_session.execute(text("CREATE INDEX _fk_probe_scan ON _fk_probe (scan_id)"))
        found = [(r.child, r.child_column) for r in db_session.execute(_UNINDEXED, {"parents": ["scans"]})]
        assert ("_fk_probe", "scan_id") not in found
    finally:
        db_session.execute(text("DROP TABLE IF EXISTS _fk_probe"))
