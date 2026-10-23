"""The Executive brief ships byte-identical copies of two Penetration test
report files (owner, 2026-10-22): the Word post-processing script and the Word
reference document.  A fix made to one copy and not the other ships two
behaviours under one name, so the pair is guarded here.

If the brief is meant to diverge from the pentest template for one of them,
remove that pair from ``COPIES`` with the reason, in the same change.

Needs the repo's ``report-templates/`` (the gate mounts it); the plain test
recipe skips it, and ``check.sh`` fails on that skip reason.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
ROOT = next(
    (p for p in (Path("/app/report-templates"), HERE.parents[2] / "report-templates")
     if (p / "pentest" / "template.json").is_file()),
    None,
)
needs_templates = pytest.mark.skipif(ROOT is None, reason="report-templates/ is not mounted here")

# (the copy, the original) — relative to report-templates/.
COPIES = [
    ("executive-brief/scripts/fix-docx-report.py", "pentest/scripts/fix-docx-report.py"),
    ("executive-brief/reference.docx", "pentest/reference.docx"),
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@needs_templates
@pytest.mark.parametrize("copy, original", COPIES, ids=[c for c, _ in COPIES])
def test_the_executive_brief_copies_stay_identical(copy, original):
    copy_path, original_path = ROOT / copy, ROOT / original
    assert copy_path.is_file() and original_path.is_file(), f"{copy} or {original} is missing"
    assert _digest(copy_path) == _digest(original_path), (
        f"report-templates/{copy} differs from report-templates/{original}. They are one file "
        "shipped twice: change both. If the Executive brief deliberately diverges, remove this "
        "pair from COPIES in tests/test_report_template_copies.py with the reason."
    )
