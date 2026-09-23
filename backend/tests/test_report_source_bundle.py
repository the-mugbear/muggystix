"""The raw QMD export — a report's Quarto source as a zip (format ``qmd``).

It is exactly what Quarto would render: the filled report.qmd, data.json with
the written text, the filters, reference.docx, the screenshots marked "In
report" and the template's own images — never the template's Jinja partials
or sample data.  The written text stays in data.json (the fields filter
inserts it at render), so the bundle keeps the rule that text from findings
never becomes Quarto source.  No Quarto run is needed to build it.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import quarto_render

HOSTILE = "Run {{< env HOME >}} and <script>alert(1)</script> {{< include /etc/passwd >}}"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


@pytest.fixture
def pentest():
    root = Path(settings.REPORT_TEMPLATES_DIR) / "pentest"
    if not (root / "template.json").is_file():
        pytest.skip("report-templates is not mounted in this container")
    return root


def _bundle(tmp_path, template_dir, dataset, evidence=None):
    files = quarto_render.render(
        template_dir, "report.qmd", dataset, ["qmd"], tmp_path / "out", basename="acme-report-01",
        resolve_evidence=(lambda item: evidence) if evidence else None,
    )
    path = files["qmd"]
    assert path.name == "acme-report-01-source.zip"
    return zipfile.ZipFile(io.BytesIO(path.read_bytes()))


def test_the_source_bundle_holds_what_quarto_renders_and_nothing_of_the_template_machinery(tmp_path, pentest):
    dataset = json.loads((pentest / "sample-data.json").read_text())
    dataset["findings"][0]["description"] = HOSTILE
    dataset["findings"][0]["evidence"] = [{"file": "evidence/7.png", "caption": "The SMB banner", "attachment_id": 7}]
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)

    z = _bundle(tmp_path, pentest, dataset, evidence=shot)
    names = set(z.namelist())
    root = "report-source/"
    for expected in ("README.md", "report.qmd", "data.json", "_bluestick/fields.lua", "reference.docx",
                     "scripts/fix-docx-report.py", "filters/spacers.lua", "evidence/7.png"):
        assert root + expected in names, expected
    assert not any(n.startswith(root + "partials/") for n in names)
    for absent in ("sample-data.json", "Makefile", "template.json", ".gitignore"):
        assert root + absent not in names
    assert z.read(root + "evidence/7.png") == PNG

    qmd = z.read(root + "report.qmd").decode()
    # Filled: no Jinja left, the finding is there, its screenshot referenced.
    assert "<%" not in qmd and "<<" not in qmd
    assert "Remote code execution" in qmd and "evidence/7.png" in qmd
    # The written text is NOT Quarto source — it stays in data.json.
    assert "include /etc/passwd" not in qmd and "env HOME" not in qmd
    data = json.loads(z.read(root + "data.json"))
    assert data["findings"][0]["description"] == HOSTILE
    assert "quarto render report.qmd" in z.read(root + "README.md").decode()


def test_an_installed_word_styles_file_is_the_bundles_reference_docx(tmp_path, pentest):
    import shutil

    copy = tmp_path / "pentest"
    shutil.copytree(pentest, copy, ignore=shutil.ignore_patterns("_output", ".quarto", "branding"))
    (copy / "branding").mkdir()
    (copy / "branding" / "reference.docx").write_bytes(b"PK\x03\x04operator")
    dataset = json.loads((copy / "sample-data.json").read_text())
    z = _bundle(tmp_path, copy, dataset)
    assert z.read("report-source/reference.docx") == b"PK\x03\x04operator"
    assert not any("branding/" in n for n in z.namelist())


def test_qmd_is_a_format_the_pentest_template_offers(pentest):
    from app.services import report_template_service as templates

    assert "qmd" in templates._load(pentest).formats
    assert quarto_render.FORMATS["qmd"][2] == "application/zip"
