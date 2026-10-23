"""No btree index another index of its table already provides (review
2026-10-10, R8; revision ``a5d9c3e1f7b2``).

Every insert and update maintains every index of its table.  A non-unique,
non-partial btree index whose columns are the LEADING columns of another
non-partial btree index (or of the primary key or a unique constraint) of the
same table serves no lookup the other cannot, and costs a write per row: 54
tables carried a second index on their primary key (``primary_key=True,
index=True``), ``hosts_v2`` two identical ``(ip_address)`` indexes,
``vulnerabilities`` one led by ``host_id`` beside two others led by it.

Read from the catalog of the suite's own database (built from the models;
``alembic check`` keeps them equal to the chain).  A partial or expression
index is not compared: its predicate or expression is what it is for.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(not USING_POSTGRES, reason="reads pg_index")

_REDUNDANT = text("""
    WITH idx AS (
        SELECT i.indexrelid, i.indrelid,
               c.relname AS tbl, ic.relname AS name,
               i.indisunique AS uq,
               i.indkey::text AS keys, i.indclass::text AS ops,
               i.indpred IS NULL AND i.indexprs IS NULL AS plain,
               am.amname
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indrelid
          JOIN pg_class ic ON ic.oid = i.indexrelid
          JOIN pg_am am ON am.oid = ic.relam
         WHERE c.relnamespace = current_schema()::regnamespace
    )
    SELECT DISTINCT a.tbl, a.name, b.name AS covered_by
      FROM idx a
      JOIN idx b ON b.indrelid = a.indrelid AND b.indexrelid <> a.indexrelid
     WHERE NOT a.uq AND a.plain AND b.plain
       AND a.amname = 'btree' AND b.amname = 'btree'
       AND (b.keys = a.keys OR b.keys LIKE a.keys || ' %')
       AND (b.ops = a.ops OR b.ops LIKE a.ops || ' %')
       -- two identical non-unique indexes: report one of the pair
       AND (b.keys <> a.keys OR b.uq OR a.name > b.name)
     ORDER BY 1, 2
""")


def test_no_index_duplicates_the_leading_columns_of_another(db_session):
    found = [
        f"{row.tbl}.{row.name} (covered by {row.covered_by})"
        for row in db_session.execute(_REDUNDANT)
    ]
    assert found == [], (
        "Indexes another index of the same table already provides — each costs a write per row "
        "and serves nothing. Drop index=True / the Index(...) on the model (a primary key or a "
        "unique constraint is already indexed): " + ", ".join(found)
    )


def test_the_query_sees_a_redundant_index(db_session):
    """The catalog query is not vacuous: a left prefix and a primary-key
    duplicate are reported; a partial index is not."""
    db_session.execute(text("CREATE TABLE _idx_probe (id serial PRIMARY KEY, a int, b int)"))
    try:
        db_session.execute(text("CREATE INDEX _idx_probe_ab ON _idx_probe (a, b)"))
        db_session.execute(text("CREATE INDEX _idx_probe_a ON _idx_probe (a)"))
        db_session.execute(text("CREATE INDEX _idx_probe_id ON _idx_probe (id)"))
        db_session.execute(text("CREATE INDEX _idx_probe_b_part ON _idx_probe (b) WHERE b > 0"))
        names = {row.name for row in db_session.execute(_REDUNDANT) if row.tbl == "_idx_probe"}
        assert names == {"_idx_probe_a", "_idx_probe_id"}
    finally:
        db_session.execute(text("DROP TABLE IF EXISTS _idx_probe"))


def test_the_revision_drops_what_the_models_no_longer_declare():
    """Every index ``a5d9c3e1f7b2`` drops is gone from the models too, or
    ``create_all`` (and ``alembic check``) would bring it back."""
    import importlib.util
    from pathlib import Path

    from app.db import models

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "a5d9c3e1f7b2_drop_redundant_indexes.py"
    spec = importlib.util.spec_from_file_location("drop_redundant_indexes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dropped = module.dropped()
    assert len(dropped) == 100
    for name, table, _column in dropped:
        declared = {index.name for index in models.Base.metadata.tables[table].indexes}
        assert name not in declared, (table, name)
