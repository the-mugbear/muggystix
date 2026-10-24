"""The Playwright overflow suite (`frontend/e2e/presentation-overflow.spec.ts`)
draws the slide builder's OWN output for a worst-case report: this test pins
`frontend/e2e/fixtures/presentation-worst-case.json` to what
`presentation_slides` makes of `tests/presentation_worst_case.py` today, so a
change to the estimator or the paginator cannot leave the browser check
drawing yesterday's slides.

Regenerate after such a change (from the repo root, the frontend mounted):

    R=$PWD; docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -e HOME=/tmp \\
      -v "$R/backend:/app" -v "$R:/repo" \\
      -e BLUESTICK_REPO_ROOT=/repo -e UPDATE_PRESENTATION_FIXTURE=1 backend \\
      sh -c "cd /tmp && python -m pytest /app/tests/test_presentation_e2e_fixture.py \\
             -q -p no:cacheprovider --rootdir=/app -c /app/pytest.ini --no-cov"
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from app.services.presentation_slides import check_filter, presentation_slides, scope_segments, scope_sites
from tests import presentation_worst_case as worst

HERE = pathlib.Path(__file__).resolve()


def _fixture_path() -> pathlib.Path:
    roots = [pathlib.Path(p) for p in (os.getenv("BLUESTICK_REPO_ROOT"), "/repo") if p] + [HERE.parents[2]]
    for root in roots:
        if (root / "frontend" / "package.json").is_file():
            return root / "frontend" / "e2e" / "fixtures" / "presentation-worst-case.json"
    pytest.skip("the repository root is not mounted here (check.sh mounts it)")


def _expected() -> str:
    ds = worst.dataset()
    variants = {}
    for name, (kind, value) in {"none": (None, None), "site:DMZ": ("site", "DMZ"),
                                "text:long": ("text", worst.LONG_FILTER)}.items():
        # The widest case: a template that prints the status and the State column.
        result = presentation_slides(ds, check_filter(ds, kind, value), set(worst.FROZEN_IMAGES),
                                     {"findings": True, "endpoints": True})
        result["sites"] = scope_sites(ds)
        result["segments"] = scope_segments(ds)
        variants[name] = result
    return json.dumps(variants, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"


def test_the_playwright_fixture_is_the_slide_builders_output():
    path = _fixture_path()
    expected = _expected()
    if os.getenv("UPDATE_PRESENTATION_FIXTURE"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
    assert path.is_file(), f"{path} is missing: regenerate it (see this file's docstring)"
    assert path.read_text(encoding="utf-8") == expected, (
        "The Playwright fixture is not the slide builder's output: regenerate it (see this file's docstring)"
    )
