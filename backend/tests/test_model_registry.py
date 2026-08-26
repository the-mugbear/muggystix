"""The model registry must reference every model module on disk.

app/db/model_registry.py is the single import surface that Alembic, the workers,
and this test suite all rely on to populate Base.metadata. If someone adds a new
app/db/models_*.py but forgets the registry, autogenerate silently proposes
dropping its tables (the exact defect that motivated the registry — env.py was
missing attribution + tools). This guard fails the moment a module is unlisted.
"""
import re
from pathlib import Path

from app.db import model_registry
from app.db.model_registry import Base


def _model_module_stems() -> set[str]:
    db_dir = Path(model_registry.__file__).parent
    return {p.stem for p in db_dir.glob("models*.py")}


def test_registry_references_every_model_module_on_disk():
    src = Path(model_registry.__file__).read_text()
    missing = [
        stem
        for stem in _model_module_stems()
        # Whole-identifier match so `models` doesn't spuriously match inside
        # `models_agent` (a `_` is a word char, so \bmodels\b won't match there).
        if not re.search(rf"\b{re.escape(stem)}\b", src)
    ]
    assert not missing, (
        f"app/db/{{{','.join(sorted(missing))}}}.py exist but are not imported by "
        "model_registry.py — add them so their tables reach Base.metadata"
    )


def test_registry_import_populates_metadata_with_known_core_tables():
    # A cheap positive check that importing the registry actually registers
    # tables from the modules that used to be omitted from one list or another.
    for table in ("network_attributions", "host_network_attributions", "tool_registry"):
        assert table in Base.metadata.tables, (
            f"{table} missing from Base.metadata after importing the registry"
        )
