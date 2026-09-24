"""v2.381.0 — the Quarto renderer and the report template.

The escaping rules run everywhere.  The renders need Quarto, which only the
report-worker image carries; they skip elsewhere.  Run them there:

    docker compose run --rm --no-deps -v "$PWD/backend:/app" report-worker \\
      sh -c "cd /tmp && python -m pytest /app/tests/test_quarto_render.py -q \\
             -p no:cacheprovider --rootdir=/app -c /app/pytest.ini --no-cov"

The hostile-input test is the contract: text from the database — a finding
title, a description — must come out as text in every format, whatever
Markdown, Quarto shortcode, raw HTML or LaTeX it contains.
"""
from __future__ import annotations

import copy
import json
import shutil
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

from app.services import quarto_render
from app.services.quarto_render import RenderError, escape_md, image, md, plain

HERE = Path(__file__).resolve()
TEMPLATE_CANDIDATES = [
    Path("/app/report-templates/pentest"),
    HERE.parents[2] / "report-templates" / "pentest",
]
TEMPLATE = next((p for p in TEMPLATE_CANDIDATES if (p / "template.json").is_file()), None)
needs_template = pytest.mark.skipif(TEMPLATE is None, reason="report-templates/ is not mounted here")
needs_quarto = pytest.mark.skipif(shutil.which("quarto") is None, reason="Quarto is only in the report-worker image")


# --- escaping --------------------------------------------------------------------

def test_escape_md_escapes_every_ascii_punctuation_and_folds_lines():
    assert escape_md("a*b_c") == r"a\*b\_c"
    assert escape_md("{{< env X >}}") == r"\{\{\< env X \>\}\}"
    assert escape_md("line one\nline two\r\n# three") == r"line one line two  \# three"
    assert escape_md("tab\there\x00") == "tab here"


def test_md_placeholders_only_take_data_paths():
    assert 'key="findings.3.description"' in md({"_path": "findings.3"}, "description")
    assert 'key="executive_summary"' in md("executive_summary")
    for bad in ('findings.3" onclick="x', "../etc", "a b"):
        with pytest.raises(RenderError):
            md(bad)


def test_image_and_plain_refuse_anything_unexpected():
    out = image({"file": "evidence/12.png", "caption": "a [link](x)"})
    assert "(evidence/12.png)" in out and r"a \[link\]\(x\)" in out
    for bad in ({"file": "/etc/passwd"}, {"file": "evidence/../x.png"}, {"file": "evidence/1.svg"}):
        with pytest.raises(RenderError):
            image(bad)
    with pytest.raises(RenderError):
        image({"file": "evidence/1.png"}, width="1in; x")
    assert plain("2026-09-23") == "2026-09-23"
    with pytest.raises(RenderError):
        plain("2026: {x}")


def test_every_printed_value_is_escaped_and_includes_are_not(tmp_path):
    (tmp_path / "part.qmd").write_text("## << f.title >>\n")
    (tmp_path / "t.qmd").write_text(
        "<% for f in findings %><% include 'part.qmd' %><% endfor %>"
        "<< note >> << count >> << missing_ok or '' >>\n"
    )
    out = quarto_render.render_source(tmp_path, "t.qmd", {
        "findings": [{"title": "*bold* {{< meta x >}}"}], "note": "[x](javascript:1)",
        "count": 3, "missing_ok": None,
    })
    assert r"## \*bold\* \{\{\< meta x \>\}\}" in out
    assert r"\[x\]\(javascript\:1\)" in out and " 3 " in out


def test_the_template_sandbox_refuses_python_internals(tmp_path):
    (tmp_path / "t.qmd").write_text("<< ''.__class__.__mro__ >>")
    with pytest.raises(RenderError):
        quarto_render.render_source(tmp_path, "t.qmd", {})


def test_includes_cannot_leave_the_template_folder(tmp_path):
    (tmp_path / "t.qmd").write_text("<% include '../secret.txt' %>")
    (tmp_path.parent / "secret.txt").write_text("SECRET")
    with pytest.raises(RenderError):
        quarto_render.render_source(tmp_path, "t.qmd", {})


@needs_template
def test_the_pentest_template_fills_for_both_kinds():
    sample = json.loads((TEMPLATE / "sample-data.json").read_text())
    full = quarto_render.render_source(TEMPLATE, "report.qmd", sample)
    assert full.startswith("---\n")
    # The original template's sections, in its order.
    order = ["# Project information", "**Penetration testers**", "**Distribution list**", "# Executive summary",
             "**Summary of findings**", "**Severity count with remediation timeline (days)**",
             "# System description", "# Findings", "# Appendix", "## Appendix A: informational findings",
             "## Disclaimer"]
    positions = [full.index(h) for h in order]
    assert positions == sorted(positions)
    # A finding heading carries its severity, as in the original.
    assert "(Critical) {#finding-11}" in full

    addendum = _addendum(sample)
    out = quarto_render.render_source(TEMPLATE, "report.qmd", addendum)
    assert "# Changes since report 1" in out
    assert "# New findings" in out and "# Withdrawn" in out and "# Reported findings on further systems" in out


def _addendum(sample: dict) -> dict:
    data = copy.deepcopy(sample)
    data["report"].update({
        "kind": "addendum", "title": "Addendum to report #1", "number": 2,
        "baseline": {"number": 1, "title": sample["report"]["title"], "date": "2026-09-23"},
    })
    new, grown = data["findings"][0], data["findings"][1]
    new["change"], new["ref"] = "new", "F-05"
    grown["change"] = "new_hosts"
    grown["new_affected"] = [{"address": "192.0.2.99", "hostname": None, "name": None, "port": None, "state": None}]
    data["findings"] = [new, grown]
    for i, f in enumerate(data["findings"]):
        f["_path"] = f"findings.{i}"
    data["delta"] = {
        "new_findings": 1, "findings_with_new_endpoints": 1,
        "withdrawn": [{"ref": "F-03", "title": "TLS", "severity_label": "Medium",
                       "reason": "The finding was judged a false positive.", "endpoints": []}],
    }
    return data


# --- Quarto renders ---------------------------------------------------------------

HOSTILE = (
    "{{< env HOME >}} {{< include /etc/passwd >}} <script>alert(1)</script> "
    "[click](javascript:alert(1)) $x^2$ \\input{/etc/passwd} `tick` {#id .cls} | pipe"
)
HOSTILE_MD = (
    "{{< env HOME >}}\n\n"
    "{{< include /etc/passwd >}}\n\n"
    "<script>alert('md')</script>\n\n"
    "![secret](/etc/passwd)\n\n"
    "[bad link](javascript:alert(2)) and [good link](https://example.com/ok)\n\n"
    "```{=html}\n<b id=\"rawhtml\">raw</b>\n```\n\n"
    "# A heading an author typed\n\n"
    "::: {.callout-note}\nfenced div\n:::\n"
)


@needs_template
@needs_quarto
def test_hostile_text_stays_text_in_every_format(tmp_path):
    data = json.loads((TEMPLATE / "sample-data.json").read_text())
    # The title and heading reach the document METADATA — where Quarto expands
    # shortcodes even in escaped text; they must come from data, via the filter.
    data["report"]["title"] = "Report " + HOSTILE
    data["report"]["heading"] = HOSTILE
    data["engagement"]["client_name"] = HOSTILE
    data["findings"][0]["title"] = HOSTILE
    data["findings"][0]["description"] = HOSTILE_MD
    data["executive_summary"] = HOSTILE_MD
    manifest = json.loads((TEMPLATE / "template.json").read_text())

    files = quarto_render.render(
        TEMPLATE, "report.qmd", data, ["html", "docx"], tmp_path,
        postprocess=manifest.get("postprocess"), timeout=240,
    )
    html = files["html"].read_text(encoding="utf-8")
    with zipfile.ZipFile(files["docx"]) as z:
        docx = z.read("word/document.xml").decode("utf-8")
        docx_links = z.read("word/_rels/document.xml.rels").decode("utf-8")

    for text in (html, docx):
        # Shortcodes were not run: printed, not expanded.
        assert "{{&lt; env HOME &gt;}}" in text or "{{< env HOME >}}" in text
        assert "bs-report-" not in text            # HOME inside the work dir was not read
        assert "root:x:0:0" not in text             # /etc/passwd was not included
    assert "<title>Report {{&lt; env HOME &gt;}}" in html
    # No link anywhere points at javascript: — the escaped title prints it as text.
    assert 'href="javascript' not in html
    assert "javascript:" not in docx_links
    assert "https://example.com/ok" in docx_links
    assert "<script>alert" not in html
    assert 'id="rawhtml"' not in html
    assert 'href="https://example.com/ok"' in html      # a web link survives
    assert "<b id" not in docx
    # The typed heading became bold text, not a report section.
    assert "A heading an author typed" in html
    assert 'id="a-heading-an-author-typed"' not in html


@needs_template
@needs_quarto
def test_evidence_images_are_placed_and_missing_ones_skipped(tmp_path):
    data = json.loads((TEMPLATE / "sample-data.json").read_text())
    png = tmp_path / "shot.png"
    # A 1x1 PNG.
    png.write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    ))
    data["findings"][0]["evidence"] = [
        {"attachment_id": 1, "file": "evidence/1.png", "caption": "Listing"},
        {"attachment_id": 2, "file": "evidence/2.png", "caption": "Gone"},
    ]
    out = tmp_path / "out"
    files = quarto_render.render(
        TEMPLATE, "report.qmd", data, ["html"], out,
        resolve_evidence=lambda item: png if item["attachment_id"] == 1 else None, timeout=240,
    )
    html = files["html"].read_text(encoding="utf-8")
    assert "Listing" in html and "Gone" not in html
    assert "data:image/png;base64" in html


@needs_template
@needs_quarto
def test_a_table_that_ends_a_field_keeps_its_last_row(tmp_path):
    """v2.407.0 — reported from a finding: pandoc.read without a trailing
    newline turned a Markdown table's LAST row into a paragraph, in every
    format.  A field's text rarely ends with a newline."""
    table = ("| Month    | Savings |\n| -------- | ------- |\n"
             "| January  | $250    |\n| February | $80     |\n| March    | $420    |")
    data = json.loads((TEMPLATE / "sample-data.json").read_text())
    data["findings"][0]["description"] = table
    data["executive_summary"] = table.replace("\n", "\r\n")   # as a Windows browser may send it
    files = quarto_render.render(TEMPLATE, "report.qmd", data, ["html", "docx"], tmp_path, timeout=240)
    html = files["html"].read_text(encoding="utf-8")
    with zipfile.ZipFile(files["docx"]) as z:
        docx = z.read("word/document.xml").decode("utf-8")
    for text in (html, docx):
        assert "| March" not in text
    assert html.count("<td>March</td>") == 2
    assert docx.count(">March</w:t>") == 2
    tables_with_march = [t for t in docx.split("<w:tbl>") if ">March</w:t>" in t.split("</w:tbl>")[0]]
    assert len(tables_with_march) == 2


# --- v2.382.0: TODO placeholders -------------------------------------------------

def test_an_empty_field_prints_a_searchable_todo(tmp_path):
    (tmp_path / "t.qmd").write_text(
        '<< md("executive_summary", todo="Write the summary") >>|'
        '<< md(findings[0], "impact", todo="State the impact") >>|'
        '<< md(findings[0], "description", todo="Describe it") >>|'
        '<< engagement.client_name or todo("Client name") >>'
    )
    out = quarto_render.render_source(tmp_path, "t.qmd", {
        "executive_summary": "  ", "engagement": {"client_name": None},
        "findings": [{"_path": "findings.0", "impact": None, "description": "Written."}],
    })
    parts = out.split("|")
    assert r"[TODO\: Write the summary]{.bs-todo}" in parts[0]
    assert r"[TODO\: State the impact]{.bs-todo}" in parts[1]
    assert 'key="findings.0.description"' in parts[2]
    assert parts[3] == r"[TODO\: Client name]{.bs-todo}"


@needs_template
@needs_quarto
def test_todos_are_highlighted_in_every_format(tmp_path):
    data = json.loads((TEMPLATE / "sample-data.json").read_text())
    data["executive_summary"] = None
    data["engagement"]["classification"] = None
    files = quarto_render.render(TEMPLATE, "report.qmd", data, ["html", "docx"], tmp_path, timeout=240,
                                 postprocess=json.loads((TEMPLATE / "template.json").read_text()).get("postprocess"))
    html = files["html"].read_text(encoding="utf-8")
    assert '<mark class="bs-todo"><strong>TODO: Classification</strong></mark>' in html
    assert "TODO: Write the executive summary" in html
    with zipfile.ZipFile(files["docx"]) as z:
        docx = z.read("word/document.xml").decode("utf-8")
    assert '<w:highlight w:val="yellow"' in docx and "TODO: Classification" in docx


# --- template assets (the template's own images) ---------------------------------

def _png(width: int, height: int) -> bytes:
    """A valid RGB PNG (every CRC right, so any renderer accepts it)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + b"\x1e\x50\xa0" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


@needs_template
def test_the_pentest_template_declares_its_logo_and_is_unchanged_without_it():
    by_id = {a["id"]: a for a in quarto_render.template_assets(TEMPLATE)}
    assert by_id["logo"]["path"] == "img/logo.png" and by_id["logo"]["required"] is False
    if not by_id["logo"]["present"]:
        src = quarto_render.render_source(
            TEMPLATE, "report.qmd", json.loads((TEMPLATE / "sample-data.json").read_text()),
        )
        assert "bs-logo-wrap" not in src and "logo:" not in src and "top: 3.2cm" not in src


@needs_template
@needs_quarto
def test_an_installed_logo_heads_the_html(tmp_path):
    folder = tmp_path / "pentest"
    shutil.copytree(TEMPLATE, folder, ignore=shutil.ignore_patterns("_output", "*_files", ".quarto"))
    (folder / "img").mkdir(exist_ok=True)
    (folder / "img" / "logo.png").write_bytes(_png(40, 12))
    data = json.loads((folder / "sample-data.json").read_text())
    files = quarto_render.render(folder, "report.qmd", data, ["html"], tmp_path / "out", timeout=240)
    html = files["html"].read_text(encoding="utf-8")
    logo_at = html.index('class="bs-logo-wrap"')
    assert logo_at < html.index('id="title-block-header"')
    assert "data:image/png;base64" in html[logo_at:logo_at + 400]


def _jpeg_header(width: int, height: int) -> bytes:
    """SOI + a baseline frame header + EOI: enough for the size to be read."""
    return (b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1)
            + b"\x01\x11\x00\xff\xd9")


def _word_drawings(docx: Path) -> list:
    """(part, drawing name, media target, cx, cy) for each header/footer picture."""
    import re
    out = []
    with zipfile.ZipFile(docx) as z:
        for part in z.namelist():
            m = re.match(r"^word/((?:header|footer)\d+\.xml)$", part)
            if not m:
                continue
            rels_name = f"word/_rels/{m.group(1)}.rels"
            rels = z.read(rels_name).decode() if rels_name in z.namelist() else ""
            targets = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
            for block in re.findall(r"<w:drawing>.*?</w:drawing>", z.read(part).decode(), re.S):
                name = re.search(r'<wp:docPr [^>]*name="([^"]*)"', block).group(1)
                embed = re.search(r'r:embed="([^"]+)"', block).group(1)
                cx, cy = map(int, re.search(r'<wp:extent cx="(\d+)" cy="(\d+)"', block).groups())
                out.append((part, name, targets.get(embed), cx, cy))
    return out


@needs_template
@needs_quarto
def test_the_word_placeholders_take_the_installed_logo_and_title_page_image(tmp_path):
    """v2.407.0 — the title page image is a template image of its own, and
    the logo reaches the Word report too: each takes the place of its
    placeholder picture in reference.docx, scaled to fit without distortion.
    Without them, the placeholders stay."""
    manifest = json.loads((TEMPLATE / "template.json").read_text())
    data = json.loads((TEMPLATE / "sample-data.json").read_text())
    plain_folder = tmp_path / "plain"
    shutil.copytree(TEMPLATE, plain_folder, ignore=shutil.ignore_patterns("_output", "*_files", ".quarto", "img", "branding"))
    plain = quarto_render.render(plain_folder, "report.qmd", data, ["docx"], tmp_path / "plain-out",
                                 postprocess=manifest.get("postprocess"), timeout=240)["docx"]
    before = _word_drawings(plain)
    assert sorted(d[1] for d in before if d[1].startswith("bluestick-")) == ["bluestick-cover"] + ["bluestick-logo"] * 4
    assert not any("bluestick-" in (d[2] or "") for d in before)

    folder = tmp_path / "pentest"
    shutil.copytree(plain_folder, folder)
    (folder / "img").mkdir()
    (folder / "img" / "logo.png").write_bytes(_png(40, 16))          # 2.5 : 1, narrower than its box
    (folder / "img" / "cover.jpg").write_bytes(_jpeg_header(16, 9))  # 16 : 9, wider than its box
    docx = quarto_render.render(folder, "report.qmd", data, ["docx"], tmp_path / "out",
                                postprocess=manifest.get("postprocess"), timeout=240)["docx"]
    with zipfile.ZipFile(docx) as z:
        names = set(z.namelist())
        assert z.read("word/media/bluestick-logo.png") == (folder / "img" / "logo.png").read_bytes()
        assert "word/media/bluestick-cover.jpeg" in names
        referenced = "".join(z.read(n).decode() for n in names if n.endswith(".rels"))
    # Nothing is left pointing at a placeholder, and no unused picture ships.
    for n in names:
        if n.startswith("word/media/"):
            assert n[len("word/"):] in referenced, n
    # Each drawing against its own placeholder (the logo boxes differ slightly).
    tagged = lambda ds: sorted((d for d in ds if d[1].startswith("bluestick-")), key=lambda d: (d[0], d[1]))
    pairs = list(zip(tagged(before), tagged(_word_drawings(docx))))
    assert len(pairs) == 5
    for (_, _, _, box_w, box_h), (part, name, target, cx, cy) in pairs:
        kind = name[len("bluestick-"):]
        assert target == f"media/bluestick-{kind}.{'png' if kind == 'logo' else 'jpeg'}"
        assert cx <= box_w and cy <= box_h
        assert abs(cx / cy - (40 / 16 if kind == "logo" else 16 / 9)) < 0.01
        # One side fills the box.
        assert cx == box_w or abs(cy - box_h) <= 1


def test_pdf_is_not_a_report_format():
    """v2.407.0 — the Word report carries the design and exports to PDF; a
    template that still lists pdf loses it rather than failing to load."""
    assert "pdf" not in quarto_render.FORMATS
    with pytest.raises(quarto_render.RenderError):
        quarto_render.render(TEMPLATE, "report.qmd", {}, ["pdf"], Path("/nonexistent"))
