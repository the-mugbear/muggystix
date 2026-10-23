"""The demo seed's findings are written directly (owner, 2026-10-22).

A scanner finding is a promoted observation — one per issue, keyed by its
``dedup_key``, carrying its vulnerabilities — and the seed's made-up findings
are none of that, so they are ``source="manual"`` (written directly), as
``FindingService.create_finding`` records a person's.

Skipped when the scripts directory isn't reachable (the plain test recipe
mounts only backend/; the gate mounts scripts/).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.db.models_findings import Finding, FindingSource


def _load_seed_module():
    candidates = [
        Path(__file__).resolve().parents[2] / "scripts" / "seed_demo_data.py",
        Path("/app/scripts/seed_demo_data.py"),
    ]
    for path in candidates:
        if path.exists():
            spec = importlib.util.spec_from_file_location("seed_demo_data", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod
    pytest.skip("scripts/seed_demo_data.py not reachable from this checkout")


def test_the_demo_findings_are_written_directly(db_session, test_user):
    seed = _load_seed_module()
    project = seed.seed(db_session, "Demo seed source test", host_count=16, owner=test_user)

    sources = {
        source for (source,) in db_session.query(Finding.source).filter(Finding.project_id == project.id)
    }
    assert sources == {FindingSource.MANUAL.value}
