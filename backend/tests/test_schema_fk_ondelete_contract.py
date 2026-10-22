"""A model's foreign keys say what the database enforces (ON DELETE included).

The test database is built by the migration chain (``tests/conftest.py``,
2026-10-22), so its catalog IS what production enforces; this compares every
model ``ForeignKey`` with it, both ways. Until then the test schema was
``create_all()`` from the models, and this file carried a hand-kept map of
every FK's rule "captured from the live database" that had to be updated with
each new FK; the catalog replaced the map.

Why the model must still match: ``alembic check`` compares models with a
chain-built database, and code that reads ``fk.ondelete`` (or a SQLite
fallback built from the models) would otherwise believe something the
database does not do.
"""
import pytest
from sqlalchemy import text

from app.db import model_registry  # noqa: F401 — every model module on Base
from app.db import models
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(not USING_POSTGRES, reason="reads the chain-built Postgres catalog")

_RULES = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}


def _normalize(ondelete):
    """A missing ``ondelete`` is emitted as the SQL default, NO ACTION."""
    return (ondelete or "NO ACTION").upper()


def _model_fks():
    out = {}
    for table in models.Base.metadata.tables.values():
        for col in table.columns:
            for fk in col.foreign_keys:
                out[(table.name, col.name)] = (fk.column.table.name, _normalize(fk.ondelete))
    return out


def _database_fks(db):
    rows = db.execute(text("""
        SELECT c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text, c.confdeltype
          FROM pg_constraint c
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
         WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
           AND array_length(c.conkey, 1) = 1
    """)).all()
    return {(t, col): (ref, _RULES[rule]) for t, col, ref, rule in rows}


def test_model_fk_ondelete_matches_database(db_session):
    """Every model FK exists in the database, to the same table, with the same rule."""
    model, database = _model_fks(), _database_fks(db_session)
    drift = sorted(
        f"{t}.{c}: model={model[(t, c)]} database={database.get((t, c))}"
        for (t, c) in model
        if model[(t, c)] != database.get((t, c))
    )
    assert not drift, "Model FKs differ from the chain-built database:\n  " + "\n  ".join(drift)


def test_no_database_fk_is_missing_from_the_models(db_session):
    """A foreign key a revision made that no model declares."""
    model, database = _model_fks(), _database_fks(db_session)
    missing = sorted(f"{t}.{c}" for (t, c) in database if (t, c) not in model)
    assert not missing, "Database FKs no model declares:\n  " + "\n  ".join(missing)
