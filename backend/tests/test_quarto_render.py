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
import re
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
    # Math delimiters are punctuation like any other (review 2026-10-01 S1):
    # a printed value never opens a formula, with dollars or with \( … \).
    assert escape_md("$a$ $$b$$ \\(c\\) \\[d\\]") == r"\$a\$ \$\$b\$\$ \\\(c\\\) \\\[d\\\]"


def test_md_placeholders_only_take_data_paths():
    assert 'key="findings.3.description"' in md({"_path": "findings.3"}, "description")
    assert 'key="executive_summary"' in md("executive_summary")
    for bad in ('findings.3" onclick="x', "../etc", "a b"):
        with pytest.raises(RenderError):
            md(bad)


def test_image_and_plain_refuse_anything_unexpected():
    # A placeholder naming the file: the caption never enters the .qmd (the
    # filter reads it from data.json), so there is nothing to escape.
    out = image({"file": "evidence/12.png", "caption": "a [link](x)"})
    assert out.strip() == '::: {.bs-figure file="evidence/12.png" width="6in"}\n:::'
    assert "link" not in out
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
    order = ["# Project information", "Table 2: Penetration testers", "Table 3: Distribution list", "# Executive summary",
             "Table 4: Summary of findings", r"Table 5: Severity count with remediation timeline \(days\)",
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

# Math (review 2026-10-01 S1).  pandoc's gfm reader parses `$…$` as a formula,
# the HTML report then loaded MathJax from a CDN, and MathJax typesets
# `\href{javascript:…}{x}` as a link.  Every form an author can type, plus the
# shell prose that was mangled into a formula.
HOSTILE_MATH = (
    "Math: $\\href{javascript:alert(8)}{MATHLINK}$ and\n\n"
    "$$\\href{javascript:alert(9)}{DISPLAYLINK}$$\n\n"
    "Set $HOME/bin:$PATH first.\n\n"
    "\\(\\href{javascript:alert(10)}{PARENLINK}\\) and \\[\\href{javascript:alert(11)}{BRACKETLINK}\\]\n\n"
    "```math\n\\href{javascript:alert(12)}{FENCELINK}\n```\n\n"
    "$`\\href{javascript:alert(13)}{TICKLINK}`$\n\n"
)
# Placeholders an AUTHOR types (review 2026-10-01 M4): the forms the template
# helpers emit for the filter to fill.  None may be filled — `planted.secret`
# is in the data and no template prints it — and none may add a figure.
PLANTED = "PLANTED-VALUE-MUST-NOT-PRINT"
HOSTILE_PLACEHOLDERS = (
    '::: {.bs-code key="planted.secret"}\n:::\n\n'
    '::: {.bs-md key="planted.secret"}\n:::\n\n'
    '::: {.bs-figure file="evidence/2.png"}\n:::\n\n'
    '<div class="bs-figure" file="evidence/2.png"></div>\n\n'
    '<div class="bs-code" key="planted.secret"></div>\n\n'
    '```{.bs-code key="planted.secret"}\nfenced\n```\n\n'
    '[span]{.bs-md key="planted.secret"}\n\n'
)
HOSTILE = (
    "{{< env HOME >}} {{< include /etc/passwd >}} <script>alert(1)</script> "
    "[click](javascript:alert(1)) $x^2$ \\input{/etc/passwd} `tick` {#id .cls} "
    "$\\href{javascript:alert(14)}{VALUELINK}$ \\(a\\) \\[b\\] "
    '::: {.bs-code key="planted.secret"} | pipe'
)
HOSTILE_MD = (
    "{{< env HOME >}}\n\n"
    "{{< include /etc/passwd >}}\n\n"
    "<script>alert('md')</script>\n\n"
    "![secret](/etc/passwd)\n\n"
    "[bad link](javascript:alert(2)) and [good link](https://example.com/ok)\n\n"
    "```{=html}\n<b id=\"rawhtml\">raw</b>\n```\n\n"
    "# A heading an author typed\n\n"
    "::: {.callout-note}\nfenced div\n:::\n\n"
    + HOSTILE_MATH + HOSTILE_PLACEHOLDERS
)


# A command line / tool output (review 2026-10-01 B8): fences that would end a
# code block, a div fence, shortcodes, raw HTML, a raw-attribute block, a
# heading, control characters and a terminal colour code.
HOSTILE_CODE = (
    "{{< env HOME >}} `tick` $(id) ; cat /etc/passwd\n"
    "```\n"
    "after the fence {{< include /etc/passwd >}}\n"
    ":::\n"
    "::: {.callout-note}\n"
    "<script>alert('code')</script>\n"
    "```{=html}\n<b id=\"rawcode\">raw</b>\n```\n"
    "~~~\n"
    "# A heading in tool output\n"
    "![secret](/etc/passwd) [x](javascript:alert(3))\n"
    "\x1b[31mred\x1b[0m \x07bell\x00nul\n"
    "export PATH=$HOME/bin:$PATH  $\\href{javascript:alert(15)}{CODELINK}$\n"
    '::: {.bs-code key="planted.secret"}\n:::\n'
    '::: {.bs-figure file="evidence/2.png"}\n:::\n'
    '::: {.bs-md key="planted.secret"}\n:::\n'
    "END-OF-HOSTILE-CODE"
)


# Images in written text.  Exactly one form may print: a reference to an
# image the dataset's `placed` map lists for THAT field of THAT finding (here
# attachment 1).  Everything else — an id the map does not list, another
# scheme, a path, a data: URI, a web image, an <img> in raw HTML — is alt text
# or plain text, as before; attributes and a title after a valid reference
# are text / dropped.
HOSTILE_IMAGES = (
    "\n\n![foreign-alt](evidence:999)\n\n"
    "![alt {{< env HOME >}} <b id=\"altraw\">x</b>](evidence:1){.class onerror=alert(4)}\n\n"
    "![js-alt](javascript:alert(5))\n\n"
    '![titled](evidence:1 "title with {{< include /etc/passwd >}}")\n\n'
    '<img src="evidence:1" onerror="alert(6)" id="rawimg">\n\n'
    "![data-alt](data:image/png;base64,SE9TVElMRQ==) ![path-alt](../../etc/passwd) "
    "![web-alt](https://example.com/x.png) ![unlisted-alt](evidence:2)\n\n"
    # An example written in code is not a placement (review 2026-10-02 H5): it
    # prints as the characters typed, and the figure counts below do not move.
    "Write `![CODESPAN-ALT](evidence:1)` to place it.\n\n"
    "```\n![CODEFENCE-ALT](evidence:1)\n```\n"
)
# A caption is typed by a person: it is printed as one plain string.
HOSTILE_CAPTION = (
    "{{< env HOME >}} {{< include /etc/passwd >}} <script>alert('cap')</script> `tick` "
    "\n::: {.callout-note}\n:::\n![x](/etc/passwd) [l](javascript:alert(7)) <b id=\"capraw\">x</b> "
    "$\\href{javascript:alert(16)}{CAPLINK}$ $HOME/bin:$PATH "
    '\n::: {.bs-code key="planted.secret"}\n:::\n::: {.bs-figure file="evidence/2.png"}\n:::\n'
    '::: {.bs-md key="planted.secret"}\n:::\n CAPTION-END'
)
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def _img_elements(html: str) -> list:
    """The attributes of every <img> element of an HTML document."""
    from html.parser import HTMLParser

    found = []

    class _Images(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "img":
                found.append(dict(attrs))

    _Images().feed(html)
    return found


def test_code_placeholders_only_take_data_paths_and_print_nothing_when_empty(tmp_path):
    (tmp_path / "t.qmd").write_text(
        '<% for c in findings[0].confirmations %>[<< code(c, "command") >>|<< code(c, "output") >>]<% endfor %>'
    )
    data = {"findings": [{"confirmations": [
        {"_path": "findings.0.confirmations.0", "command": "id; `x` {{< env HOME >}}", "output": "  "},
    ]}]}
    out = quarto_render.render_source(tmp_path, "t.qmd", data)
    # The text itself is nowhere in the source: only where to find it.
    assert '::: {.bs-code key="findings.0.confirmations.0.command"}\n:::' in out
    assert "env HOME" not in out and "`x`" not in out
    assert out.count("bs-code") == 1           # the empty output prints nothing
    for bad in ({"_path": 'findings.0" onclick="x'}, {"_path": "../etc"}, {}):
        (tmp_path / "bad.qmd").write_text('<< code(item, "command") >>')
        with pytest.raises(RenderError):
            quarto_render.render_source(tmp_path, "bad.qmd", {"item": bad})


# --- review 2026-10-01 R14: a timeout ends the whole process group ------------------

def _alive(pid: int) -> bool:
    """Running — a zombie nobody reaped yet is dead."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _fake_quarto(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "quarto"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return script


def _tiny_template(tmp_path: Path) -> Path:
    folder = tmp_path / "tpl"
    (folder / "scripts").mkdir(parents=True)
    (folder / "report.qmd").write_text("---\ntitle: x\n---\nbody\n")
    return folder


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
def test_a_render_timeout_kills_what_the_launcher_started(tmp_path):
    """The real ``quarto`` is a bash script that runs deno WITHOUT exec.
    ``subprocess.run(timeout=…)`` killed bash only: deno carried on as an
    orphan after every timed-out render.  The fake launcher does the same
    with a ``sleep``."""
    import time
    pidfile = tmp_path / "child.pid"
    quarto = _fake_quarto(tmp_path, f"sleep 40 &\necho $! > {pidfile}\nwait\n")
    started = time.monotonic()
    with pytest.raises(RenderError) as exc:
        quarto_render.render(_tiny_template(tmp_path), "report.qmd", {}, ["html"], tmp_path / "out",
                             timeout=1, quarto=str(quarto))
    elapsed = time.monotonic() - started
    assert "took longer than 1s" in str(exc.value)
    assert elapsed < 15, f"the render blocked for {elapsed:.0f}s on the launcher's child"
    child = int(pidfile.read_text())
    assert not _alive(child), "the launcher's child survived the timeout"


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
def test_a_child_that_ignores_term_is_killed(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(quarto_render, "KILL_GRACE_SECONDS", 0.5)
    pidfile = tmp_path / "child.pid"
    quarto = _fake_quarto(tmp_path, f"trap '' TERM\n(trap '' TERM; sleep 40) &\necho $! > {pidfile}\nwait\n")
    started = time.monotonic()
    with pytest.raises(RenderError):
        quarto_render.render(_tiny_template(tmp_path), "report.qmd", {}, ["html"], tmp_path / "out",
                             timeout=1, quarto=str(quarto))
    assert time.monotonic() - started < 15
    assert not _alive(int(pidfile.read_text()))


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
def test_asking_the_version_kills_what_the_launcher_started_too(tmp_path):
    """Review 2026-10-01 M3 — ``quarto_version`` was the one call still on
    ``subprocess.run(timeout=…)``: a launcher that hung left its child running
    and the call waiting on the child's pipes."""
    import time
    pidfile = tmp_path / "child.pid"
    hung = _fake_quarto(tmp_path, f"sleep 40 &\necho $! > {pidfile}\nwait\n")
    started = time.monotonic()
    assert quarto_render.quarto_version(str(hung), timeout=1) is None
    assert time.monotonic() - started < 15
    assert not _alive(int(pidfile.read_text())), "the launcher's child survived the timeout"
    # … and an answer is still an answer; a missing binary is None.
    assert quarto_render.quarto_version(str(_fake_quarto(tmp_path, "echo ' 1.10.18 '\n"))) == "1.10.18"
    assert quarto_render.quarto_version(str(tmp_path / "no-such-quarto")) is None


def test_a_post_processor_timeout_is_a_render_error_without_paths(tmp_path):
    """It was an uncaught TimeoutExpired: the job's error was the whole
    command line, temporary work directory included."""
    folder = _tiny_template(tmp_path)
    (folder / "scripts" / "slow.py").write_text("import time\ntime.sleep(40)\n")
    quarto = _fake_quarto(tmp_path, "echo '<html></html>' > report.html\n")
    with pytest.raises(RenderError) as exc:
        quarto_render.render(folder, "report.qmd", {}, ["html"], tmp_path / "out", timeout=1,
                             quarto=str(quarto), postprocess={"html": "scripts/slow.py"})
    message = str(exc.value)
    assert message == "The html post-processor took longer than 1s."
    assert "bs-report-" not in message and "/tmp" not in message


def test_a_render_that_finishes_is_unchanged(tmp_path):
    folder = _tiny_template(tmp_path)
    (folder / "scripts" / "ok.py").write_text(
        "import sys\nopen(sys.argv[1], 'a').write('<!-- post -->')\n"
    )
    quarto = _fake_quarto(tmp_path, "echo '<html>ok</html>' > report.html\necho noise >&2\n")
    files = quarto_render.render(folder, "report.qmd", {}, ["html"], tmp_path / "out", timeout=30,
                                 quarto=str(quarto), postprocess={"html": "scripts/ok.py"})
    assert files["html"].read_text() == "<html>ok</html>\n<!-- post -->"
    failing = _fake_quarto(tmp_path, "echo 'ERROR: bad yaml' >&2\nexit 3\n")
    with pytest.raises(RenderError) as exc:
        quarto_render.render(folder, "report.qmd", {}, ["html"], tmp_path / "out2", timeout=30, quarto=str(failing))
    assert "exit 3" in str(exc.value) and "ERROR: bad yaml" in str(exc.value)


# v2.409.0 — every template shipped in report-templates/ is held to the same
# contract, not only the first one.
SHIPPED_TEMPLATES = sorted(
    p for p in (TEMPLATE.parent.iterdir() if TEMPLATE else []) if (p / "template.json").is_file()
)


@needs_template
@needs_quarto
@pytest.mark.parametrize("template", SHIPPED_TEMPLATES, ids=lambda p: p.name)
def test_hostile_text_stays_text_in_every_format(tmp_path, template):
    data = json.loads((template / "sample-data.json").read_text())
    # The title and heading reach the document METADATA — where Quarto expands
    # shortcodes even in escaped text; they must come from data, via the filter.
    data["report"]["title"] = "Report " + HOSTILE
    data["report"]["heading"] = HOSTILE
    data["engagement"]["client_name"] = HOSTILE
    data["findings"][0]["title"] = HOSTILE
    data["findings"][0]["description"] = HOSTILE_MD
    # Every template prints a recommendation; not every one the description
    # or the executive summary.
    data["findings"][0]["recommendation"] = HOSTILE_MD
    data["executive_summary"] = HOSTILE_MD
    # v2.441.0 — a scope over the cutoff prints a per-site table and a file
    # name: a site is typed by a person, so it is hostile text too.
    from app.services import report_scope
    subnets = [{"cidr": f"10.9.{i}.0/24", "site": HOSTILE, "description": HOSTILE} for i in range(30)]
    block = {"subnets": subnets, "domains": []}
    block.update(report_scope.summarise(subnets, []))
    data["scope"] = report_scope.attach_file(block, project_slug="hostile", number=1, report_id=1)
    manifest = json.loads((template / "template.json").read_text())
    # Review 2026-10-01 B8 — how the finding was confirmed: a command line and
    # a tool's output are the most hostile text a report carries.  Every field
    # of the entry is hostile here, whether or not this template prints it.
    data["findings"][0]["confirmations"] = [{
        "_path": "findings.0.confirmations.0", "id": 1, "outcome": "finding",
        "tool": HOSTILE, "host": HOSTILE, "by": HOSTILE, "date": "2026-10-01",
        "executed_at": "2026-10-01T00:00:00+00:00", "by_agent": False,
        "summary": HOSTILE_MD, "command": "CONFIRM-COMMAND " + HOSTILE_CODE,
        "output": "CONFIRM-OUTPUT\n" + HOSTILE_CODE, "output_truncated": True,
    }]
    data["findings"][0]["confirmations_omitted"] = 2
    # B17 — a re-rated finding's earlier severity is a label; hostile all the same.
    data["findings"][0]["previous_severity"] = "medium"
    data["findings"][0]["previous_severity_label"] = HOSTILE
    # Images placed in text.  Attachment 1 is placed in the description and
    # the recommendation; 2 is ticked but placed nowhere (the trailing
    # block); 999 is nobody's.  The `placed` map itself is hostile too: an
    # entry whose file is not an evidence file must never be read.  Every
    # field that prints, in any template, carries the hostile references —
    # the executive summary and another finding included, where NO image may
    # print (the map is per finding and per field).
    for f in data["findings"]:
        f["images"], f["placed"], f["evidence"] = [], {}, []
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG_1X1)
    one = {"attachment_id": 1, "file": "evidence/1.png", "caption": HOSTILE_CAPTION}
    two = {"attachment_id": 2, "file": "evidence/2.png", "caption": HOSTILE_CAPTION}
    first = data["findings"][0]
    first["description"] += HOSTILE_IMAGES
    first["recommendation"] += HOSTILE_IMAGES + "\n\n![](evidence:1)\n"
    first["images"] = [{**one, "placed_in": ["description", "recommendation"]}, {**two, "placed_in": []}]
    first["evidence"] = [{**two, "placed_in": []}]
    first["placed"] = {
        "description": {"1": one, "7": {"attachment_id": 7, "file": "/etc/passwd", "caption": "x"},
                        "8": {"attachment_id": 8, "file": "evidence/../../etc/passwd", "caption": "x"}},
        "recommendation": {"1": one},
    }
    data["executive_summary"] += HOSTILE_IMAGES
    data["findings"][1]["description"] = "Another finding. " + HOSTILE_IMAGES
    data["findings"][1]["recommendation"] = "Fix it. " + HOSTILE_IMAGES
    # M4 — what an author-typed placeholder asks for.  No template prints it.
    data["planted"] = {"secret": PLANTED}

    files = quarto_render.render(
        template, manifest.get("entry", "report.qmd"), data, ["html", "docx"], tmp_path,
        postprocess=manifest.get("postprocess"), timeout=240,
        resolve_evidence=lambda item: shot if item.get("attachment_id") in (1, 2) else None,
    )
    html = files["html"].read_text(encoding="utf-8")
    with zipfile.ZipFile(files["docx"]) as z:
        docx = z.read("word/document.xml").decode("utf-8")
        docx_links = z.read("word/_rels/document.xml.rels").decode("utf-8")
        docx_media = [n for n in z.namelist() if n.startswith("word/media/")]
        docx_parts = "".join(
            z.read(n).decode("utf-8", errors="replace") for n in z.namelist() if n.endswith((".xml", ".rels"))
        )

    # --- math (S1) -----------------------------------------------------------
    # Nothing became a formula, and nothing loads a math library: checked over
    # the WHOLE document, head included (the loader was a <script> there).
    lowered = html.lower()
    for needle in ('class="math', "mathjax", "katex", "cdn.jsdelivr", "<math"):
        assert needle not in lowered, needle
    assert "m:oMath" not in docx_parts and "<m:r>" not in docx_parts
    # The formulas are the characters that were typed — every form of them.
    for marker in ("MATHLINK", "DISPLAYLINK", "PARENLINK", "BRACKETLINK", "FENCELINK", "TICKLINK", "VALUELINK"):
        assert marker in html and marker in docx, marker
    assert "$\\href{javascript:alert(8)}{MATHLINK}$" in html
    assert "$$\\href{javascript:alert(9)}{DISPLAYLINK}$$" in html
    assert "$\\href{javascript:alert(14)}{VALUELINK}$ \\(a\\) \\[b\\]" in html
    # Shell prose is not mangled into a formula.
    for text in (html, docx):
        assert "Set $HOME/bin:$PATH first." in text

    # --- author-typed placeholders (M4) --------------------------------------
    # Not one was filled: the value they name is nowhere, and (below) the
    # figure count is what the template's own placeholders print.
    for text in (html, docx_parts):
        assert PLANTED not in text
    assert "planted.secret" in html and "planted.secret" in docx      # … printed as the text it is

    # --- images in written text ------------------------------------------
    # The only <img> elements are the report's own figures (and the logo):
    # none took its source, or any attribute, from the text.
    # (Parsed, not searched: a caption's text is — safely — inside alt="…".)
    for attrs in _img_elements(html):
        assert set(attrs) <= {"role", "aria-label", "src", "style", "alt", "class"}, sorted(attrs)
        assert attrs["src"].startswith("data:image/png;base64,") or "bs-logo" in attrs.get("class", "")
        assert attrs.get("style", "") in ("", "width:6in") and "rawimg" not in attrs.get("class", "")
    assert "SE9TVElMRQ==" not in html                       # the data: URI was not embedded
    assert '<b id="altraw"' not in html and '<b id="capraw"' not in html
    assert "<script>alert('cap')" not in html
    assert "evidence:" not in docx_links and "passwd" not in docx_links and "example.com/x.png" not in docx_links
    assert "<b id" not in docx
    # Where a figure prints: per template, never in the executive summary or
    # in the other finding.  pentest prints the description (2 references to
    # image 1), the recommendation (3) and the trailing block (image 2);
    # the worklist prints the recommendation; the brief prints no evidence.
    figures = re.findall(r'<figure class="bs-figure[^>]*>.*?</figure>', html, re.S)
    # An operator's own template is held to everything else here; the count
    # is pinned for the shipped ones.
    expected = {"pentest": 6, "remediation-worklist": 3, "executive-brief": 0}.get(template.name, len(figures))
    assert len(figures) == expected
    assert docx.count("<w:drawing>") == expected
    assert not [n for n in docx_media if n.endswith((".svg", ".html")) or "passwd" in n]
    captions = re.findall(r"<figcaption[^>]*>(.*?)</figcaption>", html, re.S)
    assert [c.split(":")[0] for c in captions] == [f"Figure {n}" for n in range(1, expected + 1)]
    if expected:
        # The stored caption and an alt text are printed, whole, as text.
        assert any("CAPTION-END" in c and "{{&lt; env HOME &gt;}}" in c for c in captions)
        assert "CAPTION-END" in docx
        assert any(c.startswith("Figure") and "alt {{&lt; env HOME &gt;}}" in c for c in captions)
    else:
        assert "CAPTION-END" not in html and "CAPTION-END" not in docx
    for text in (html, docx):
        # A reference that places nothing leaves its alt text, as any image does.
        assert "foreign-alt" in text and "js-alt" in text and "unlisted-alt" in text
        assert "CODESPAN-ALT" in text and "CODEFENCE-ALT" in text
    assert "![CODESPAN-ALT](evidence:1)" in html

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
    # The command and the output never became markup, in any template.
    assert '<b id="rawcode"' not in html and "<b id" not in docx
    assert "<script>alert('code')" not in html
    assert 'id="a-heading-in-tool-output"' not in html
    if manifest.get("evidence_records") is True:
        # … and where the template prints them, they are there, whole and
        # verbatim: the fence and the div in the text closed nothing.
        for text in (html, docx):
            assert "CONFIRM-COMMAND" in text and "CONFIRM-OUTPUT" in text
            assert "after the fence" in text and "END-OF-HOSTILE-CODE" in text
        block = html[html.index("CONFIRM-OUTPUT"):]
        block = block[:block.index("</pre>")]
        assert "END-OF-HOSTILE-CODE" in block                      # one block, not split by its own fences
        assert "&lt;script&gt;alert(" in block
        assert "{{&lt; include /etc/passwd &gt;}}" in block
        assert "\x1b" not in html and "\x00" not in docx
        # Math and placeholders in tool text are tool text.
        assert "export PATH=$HOME/bin:$PATH  $\\href{javascript:alert(15)}{CODELINK}$" in block
        assert '::: {.bs-code key="planted.secret"}' in block.replace("&quot;", '"')
        assert '::: {.bs-figure file="evidence/2.png"}' in block.replace("&quot;", '"')


@needs_quarto
def test_no_template_can_make_the_html_report_load_a_math_library(tmp_path):
    """Review 2026-10-01 S1, the layer under the filter: a template that does
    not set ``html-math-method`` and writes a formula of its OWN still renders
    HTML with no MathJax / KaTeX loader — the renderer passes the option on
    every HTML render.  (Quarto's default is MathJax from cdn.jsdelivr.net.)"""
    folder = tmp_path / "tpl"
    folder.mkdir()
    (folder / "report.qmd").write_text(
        "---\ntitle: x\nengine: markdown\nfilters:\n  - quarto\n  - _bluestick/fields.lua\n"
        "format:\n  html:\n    embed-resources: true\n---\n\n"
        "The template's own formula $a^2 + b^2$ here.\n\n<< md(findings[0], \"description\") >>\n"
    )
    data = {"findings": [{"_path": "findings.0", "description": HOSTILE_MATH}]}
    html = quarto_render.render(folder, "report.qmd", data, ["html"], tmp_path / "out", timeout=240)["html"]
    lowered = html.read_text(encoding="utf-8").lower()
    for needle in ("mathjax", "katex", "cdn.jsdelivr"):
        assert needle not in lowered, needle
    assert "set $home/bin:$path first." in lowered and "mathlink" in lowered
    assert 'href="javascript' not in lowered


def test_the_reader_and_the_filter_both_refuse_math():
    """The filter's two layers, pinned in its source: the reader's math
    extensions are off (the names are pandoc's — ``--list-extensions=gfm``),
    and ``clean()`` has a Math handler."""
    source = quarto_render.FIELDS_FILTER.read_text(encoding="utf-8")
    assert 'local READER = "gfm-raw_html-tex_math_dollars-tex_math_gfm"' in source
    assert "pandoc.read(text, READER)" in source and source.count("pandoc.read(") == 1
    assert "Math = math_source," in source
    assert quarto_render._FORMAT_ARGS["html"] == ("-M", "html-math-method:plain")


@needs_template
def test_every_shipped_template_sets_the_html_math_method():
    """So the Quarto source bundle, rendered by hand, loads no math library
    either.  Printed in the front matter as a constant, never from data."""
    for template in SHIPPED_TEMPLATES:
        manifest = json.loads((template / "template.json").read_text())
        front = (template / manifest.get("entry", "report.qmd")).read_text(encoding="utf-8").split("\n---\n")[1]
        assert "\n    html-math-method: plain\n" in front, template.name


@needs_template
@needs_quarto
@pytest.mark.parametrize("template", SHIPPED_TEMPLATES, ids=lambda p: p.name)
def test_a_template_prints_the_images_it_declares_and_the_fill_measures_them(tmp_path, template, monkeypatch):
    """Branch review 2026-10-01 S2.  The report page said "N placed in text,
    M under Evidence" whatever the template: the worklist prints only images
    placed in the recommendation, the brief none.  Three things must agree,
    for every shipped template, on a finding with an image placed in EVERY
    written field and one placed nowhere:

    * the figures Quarto actually prints (HTML and Word),
    * what ``template.json`` declares under ``images``,
    * what ``printed_parts`` measures from a Jinja fill — which is what the
      report's summary counts and what an issue copies."""
    from app.core.config import settings
    from app.services import report_template_service as templates

    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(template.parent))
    loaded = templates.get_template(template.name)
    data = templates.image_probe_dataset(json.loads((template / "sample-data.json").read_text()))
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG_1X1)
    files = quarto_render.render(
        template, loaded.entry, data, ["html", "docx"], tmp_path / "out",
        postprocess=loaded.postprocess, timeout=240, resolve_evidence=lambda item: shot,
    )
    html = files["html"].read_text(encoding="utf-8")
    with zipfile.ZipFile(files["docx"]) as z:
        docx = z.read("word/document.xml").decode("utf-8")
    printed = sorted(re.sub(r"^Figure \d+: ", "", c) for c in _figcaptions(html))

    declared = sorted(f"in {field}" for field in loaded.image_fields) + (["unplaced"] if loaded.image_trailing else [])
    assert printed == declared
    assert docx.count("<w:drawing>") == len(declared)
    for caption in ("in description", "in impact", "in recommendation", "in references", "in steps_to_reproduce",
                    "unplaced"):
        assert (caption in docx) == (caption in declared), caption

    parts = templates.printed_parts(loaded, data)
    measured = sorted(f"in {field}" for field, images in parts["fields"].get(0, {}).items() if images)
    assert measured + (["unplaced"] if parts["figures"] else []) == printed
    assert parts["figures"] in ([], ["evidence/6.png"])
    assert templates.image_declaration_problem(loaded, data) is None


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
    # The sample's first finding has images of its own; a dataset frozen
    # before images could be placed has `evidence` alone, as here.
    for f in data["findings"]:
        f.pop("images", None)
        f.pop("placed", None)
        f["evidence"] = []
    data["findings"][1]["evidence"] = [
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

    # The floating title page image stays centred in its box: the 16:9
    # picture is shorter than the 3:2 box, so it moves down by half the gap.
    import re

    def cover_offsets(path):
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if re.match(r"^word/header\d+\.xml$", n):
                    xml = z.read(n).decode()
                    if 'name="bluestick-cover"' in xml:
                        return [int(v) for v in re.findall(r"<wp:position[HV]\b[^>]*>\s*<wp:posOffset>(-?\d+)", xml)]
    (_, _, _, box_w, box_h), (_, _, _, cx, cy) = next(p for p in pairs if p[0][1] == "bluestick-cover")
    (h0, v0), (h1, v1) = cover_offsets(plain), cover_offsets(docx)
    assert (h1, v1) == (h0 + (box_w - cx) // 2, v0 + (box_h - cy) // 2)
    assert v1 > v0


@needs_template
def test_every_table_label_keeps_with_its_table_in_word():
    """v2.407.2 — "Severity count with remediation timeline (days)" ended one
    page and its table began the next: a bold label was a plain paragraph.
    v2.410.0 — tables carry the original template's numbered, centred
    captions (Table Caption style); the labels of written text that may be a
    list keep Table Label.  Both styles keep with next."""
    import re
    with zipfile.ZipFile(TEMPLATE / "reference.docx") as z:
        styles = z.read("word/styles.xml").decode()
    label = re.search(r'<w:style [^>]*w:styleId="TableLabel".*?</w:style>', styles, re.S)
    assert label and "<w:keepNext/>" in label.group(0)
    caption = re.search(r'<w:style [^>]*w:styleId="TableCaption".*?</w:style>', styles, re.S)
    assert caption and re.search(r'<w:keepNext( w:val="true")?/>', caption.group(0))
    assert '<w:jc w:val="center"/>' in caption.group(0)
    # The captions are centred, so the tables under them are too (a narrow
    # key/value table otherwise sits at the margin under a centred caption).
    table = re.search(r'<w:style [^>]*w:styleId="Table".*?</w:style>', styles, re.S).group(0)
    assert '<w:jc w:val="center"/>' in table.split("<w:tblStylePr")[0]
    partial = (TEMPLATE / "partials" / "_table_caption.qmd").read_text(encoding="utf-8")
    assert '::: {custom-style="Table Caption"}\nTable << counter.table >>: << caption >>\n:::' in partial
    source = (TEMPLATE / "report.qmd").read_text(encoding="utf-8")
    lines = source.splitlines()
    labels = [i for i, line in enumerate(lines) if re.fullmatch(r"\*\*[^*]+\*\*", line.strip())]
    assert len(labels) == 3
    for i in labels:
        assert lines[i - 1] == '::: {custom-style="Table Label"}' and lines[i + 1] == ":::", lines[i]


@needs_template
def test_tables_figures_and_finding_sections_are_numbered_like_the_original():
    """v2.410.0 — the original template's formatting, reclaimed without its
    Quarto cross-references (and the wrapper tables a script had to flatten):
    "Table N: …" captions counted through the report, "Figure N: …" under
    every evidence image, numbered finding sub-sections, and key/value
    project details without an empty header row."""
    import re
    sample = json.loads((TEMPLATE / "sample-data.json").read_text())
    sample["findings"][0]["evidence"] = [
        {"attachment_id": 1, "file": "evidence/1.png", "caption": "SMB banner"},
        {"attachment_id": 2, "file": "evidence/2.png", "caption": ""},
    ]
    sample["findings"][1]["evidence"] = [{"attachment_id": 3, "file": "evidence/3.png", "caption": "Bind"}]
    out = quarto_render.render_source(TEMPLATE, "report.qmd", sample)
    tables = re.findall(r"^Table (\d+): (.+)$", out, re.M)
    assert [int(n) for n, _ in tables] == list(range(1, len(tables) + 1))
    assert [t for _, t in tables][:3] == ["Project details", "Penetration testers", "Distribution list"]
    assert "Summary of findings" in [t for _, t in tables]
    # Figures are placeholders in the source — the filter captions and numbers
    # them in document order (test_figures_are_numbered_in_document_order).
    assert re.findall(r'\.bs-figure file="(evidence/\d\.png)"', out) == [
        "evidence/1.png", "evidence/2.png", "evidence/3.png",
    ]
    assert "Figure" not in "".join(re.findall(r"^::: \{\.bs-figure.*$", out, re.M))
    assert "{.unnumbered}" not in (TEMPLATE / "partials" / "_finding.qmd").read_text(encoding="utf-8")
    project = out.split("Table 1: Project details")[1]
    assert project.split("\n:::\n", 1)[1].lstrip().startswith("| | |")


def _post_processor():
    import importlib.util
    spec = importlib.util.spec_from_file_location("fix_docx_report", TEMPLATE / "scripts" / "fix-docx-report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@needs_template
def test_tables_take_the_alignment_their_style_declares(tmp_path):
    """v2.410.1 — the Table style centred tables in reference.docx, but a
    report opened with every table still at the margin: a style-level table
    alignment was ignored.  The post-processor copies it onto each table —
    only where the table has none, and only when its style declares one."""
    import xml.etree.ElementTree as ET
    fix = _post_processor()
    w = fix.WML
    (tmp_path / "styles.xml").write_text(
        f'<w:styles xmlns:w="{w}">'
        '<w:style w:type="table" w:styleId="Table"><w:tblPr><w:tblStyleRowBandSize w:val="1"/>'
        '<w:jc w:val="center"/></w:tblPr><w:tblStylePr w:type="firstRow"><w:tblPr><w:jc w:val="left"/></w:tblPr></w:tblStylePr></w:style>'
        '<w:style w:type="table" w:styleId="Plain"><w:tblPr/></w:style></w:styles>'
    )
    body = ET.fromstring(
        f'<w:body xmlns:w="{w}">'
        '<w:tbl><w:tblPr><w:tblStyle w:val="Table"/><w:tblW w:type="auto" w:w="0"/><w:tblLook w:val="0000"/></w:tblPr></w:tbl>'
        '<w:tbl><w:tblPr><w:tblStyle w:val="Table"/><w:jc w:val="right"/></w:tblPr></w:tbl>'
        '<w:tbl><w:tblPr><w:tblStyle w:val="Plain"/></w:tblPr></w:tbl></w:body>'
    )
    assert fix.align_tables_like_their_style(body, tmp_path / "styles.xml") == 1
    first, own, plain = body.findall(f"{{{w}}}tbl")
    children = [c.tag.split("}")[1] for c in first.find(f"{{{w}}}tblPr")]
    assert children == ["tblStyle", "tblW", "jc", "tblLook"]        # schema order
    assert first.find(f"{{{w}}}tblPr/{{{w}}}jc").get(f"{{{w}}}val") == "center"
    assert own.find(f"{{{w}}}tblPr/{{{w}}}jc").get(f"{{{w}}}val") == "right"
    assert plain.find(f"{{{w}}}tblPr/{{{w}}}jc") is None


@needs_template
@needs_quarto
def test_every_table_in_the_word_report_is_centred(tmp_path):
    import re
    data = json.loads((TEMPLATE / "sample-data.json").read_text())
    manifest = json.loads((TEMPLATE / "template.json").read_text())
    files = quarto_render.render(TEMPLATE, "report.qmd", data, ["docx"], tmp_path,
                                 postprocess=manifest.get("postprocess"), timeout=240)
    with zipfile.ZipFile(files["docx"]) as z:
        doc = z.read("word/document.xml").decode("utf-8")
    props = re.findall(r"<w:tblPr>(.*?)</w:tblPr>", doc, re.S)
    assert len(props) >= 7
    assert all('<w:jc w:val="center"' in p for p in props), props[0]


def test_image_accepts_and_ignores_a_number_and_refuses_anything_but_a_count():
    """A template written when Jinja counted the figures (v2.410.0,
    ``image(e, number=counter.figure)``) keeps rendering: the argument is
    accepted and ignored — the filter numbers every figure in document order."""
    item = {"file": "evidence/1.png", "caption": "a *b*"}
    assert image(item, number=4) == image(item)
    assert "Figure" not in image(item, number=4) and "a *b*" not in image(item, number=4)
    for bad in (0, -1, "3", 2.5, True):
        with pytest.raises(RenderError):
            image(item, number=bad)


def test_pdf_is_not_a_report_format():
    """v2.407.0 — the Word report carries the design and exports to PDF; a
    template that still lists pdf loses it rather than failing to load."""
    assert "pdf" not in quarto_render.FORMATS
    with pytest.raises(quarto_render.RenderError):
        quarto_render.render(TEMPLATE, "report.qmd", {}, ["pdf"], Path("/nonexistent"))


# --- images placed in a finding's written sections ---------------------------------

def _img(att_id: int, caption: str, placed_in=()) -> dict:
    return {"attachment_id": att_id, "file": f"evidence/{att_id}.png", "caption": caption,
            "placed_in": list(placed_in)}


def _placed(*images: dict) -> dict:
    return {str(i["attachment_id"]): {k: i[k] for k in ("attachment_id", "file", "caption")} for i in images}


def _figcaptions(html: str) -> list:
    return [re.sub(r"\s+", " ", c).strip() for c in re.findall(r"<figcaption[^>]*>(.*?)</figcaption>", html, re.S)]


def _word_paragraphs(docx: Path) -> list:
    """``(style, text, framed)`` for each paragraph of the Word document, in
    order; ``framed`` is True for a paragraph holding a picture inside the
    one-cell table the post-processor frames screenshots with."""
    with zipfile.ZipFile(docx) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    out = []
    depth = 0
    for m in re.finditer(r"<w:tbl>|</w:tbl>|<w:p[ >].*?</w:p>", xml, re.S):
        token = m.group(0)
        if token == "<w:tbl>":
            depth += 1
        elif token == "</w:tbl>":
            depth -= 1
        else:
            style = re.search(r'<w:pStyle w:val="([^"]+)"', token)
            text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", token))
            out.append((style.group(1) if style else "", text, "<w:drawing>" in token and depth > 0))
    return out


@needs_template
@needs_quarto
def test_placed_images_print_in_their_section_and_figures_count_in_document_order(tmp_path):
    """The worst cases together, in HTML and Word: an image on its own line,
    one in the middle of a paragraph, the same image twice in one section and
    again in another, one in a table cell, one in a list item, a section that
    is only an image, a reference inside a code block, a 2,000-character
    caption, thirty images on one finding — and a second finding whose
    figures carry on the count."""
    long_caption = "L" * 1990 + " LONG-END"
    images = {n: _img(n, f"Stored caption {n}") for n in range(1, 31)}
    images[2] = _img(2, long_caption)
    data = json.loads((TEMPLATE / "sample-data.json").read_text())
    for f in data["findings"]:
        f["images"], f["placed"], f["evidence"] = [], {}, []
    first, second = data["findings"][0], data["findings"][1]
    first["description"] = (
        "Intro text.\n\n"
        "![Own line, alt wins](evidence:1)\n\n"
        "Before ![](evidence:2) after, and ![Same again](evidence:1) too.\n\n"
        "| Step | Shot |\n|---|---|\n| relay | ![In a cell](evidence:3) |\n\n"
        "- first item\n- second item ![In a list](evidence:4)\n\n"
        "```\n![](evidence:5)\n```\n"
    )
    first["impact"] = "![](evidence:1)"                          # a section that is only an image
    first["steps_to_reproduce"] = "1. Run it\n\n![](evidence:6)\n\n2. Read ![not placed here](evidence:3) the result"
    placed_ids = {"description": [1, 2, 3, 4, 5], "impact": [1], "steps_to_reproduce": [6]}
    first["placed"] = {field: _placed(*[images[n] for n in ids]) for field, ids in placed_ids.items()}
    placed_anywhere = {n for ids in placed_ids.values() for n in ids}
    for n, entry in images.items():
        entry["placed_in"] = [field for field, ids in placed_ids.items() if n in ids]
    first["images"] = list(images.values())
    first["evidence"] = [images[n] for n in sorted(images) if n not in placed_anywhere]   # 24 trailing
    second["evidence"] = second["images"] = [_img(40, "On the other finding")]

    shot = tmp_path / "shot.png"
    shot.write_bytes(_png(40, 20))
    manifest = json.loads((TEMPLATE / "template.json").read_text())
    files = quarto_render.render(
        TEMPLATE, "report.qmd", data, ["html", "docx"], tmp_path / "out",
        resolve_evidence=lambda item: shot, postprocess=manifest.get("postprocess"), timeout=240,
    )
    html = files["html"].read_text(encoding="utf-8")

    expected = [
        "Own line, alt wins",          # description: on its own line — the alt text overrides the caption
        long_caption,                  # in the middle of a paragraph: the stored caption (empty alt)
        "Same again",                  # the same image again, its own number
        "In a cell",                   # from a table cell: the figure follows the table
        "In a list",
        "Stored caption 5",            # referenced only inside a code block: printed after the text
        "Stored caption 1",            # impact: the section that is only an image
        "Stored caption 6",            # steps to reproduce
    ] + [f"Stored caption {n}" for n in range(7, 31)] + ["On the other finding"]
    numbered = [f"Figure {i}: {text}" for i, text in enumerate(expected, start=1)]
    assert _figcaptions(html) == numbered
    assert len(numbered) == 33

    # Each placed figure sits INSIDE its section; the trailing block holds
    # only the images no section places.
    def between(start: str, end: str) -> str:
        section = html[html.index(start):]
        return section[:section.index(end)]

    first_html = between('id="finding-11"', 'id="finding-12"')
    h_impact, h_steps, h_evidence, h_fix = (
        " Impact</h4>", " Steps to reproduce</h4>", " Evidence / proof of concept</h4>", " Recommendations</h3>",
    )
    description = first_html[first_html.index("Intro text."):first_html.index(h_impact)]
    assert _figcaptions(description) == numbered[:6]
    assert description.index("Before") < description.index("Figure 2:") < description.index("after, and")
    assert description.index("</table>") < description.index("Figure 4:")
    assert re.search(r"<td>In a cell</td>", description)          # the cell keeps the alt text
    assert re.search(r"<li>second item\s*<figure", description)   # the list item holds its figure
    assert "![](evidence:5)" in description                        # the code block is untouched
    impact = first_html[first_html.index(h_impact):first_html.index(h_steps)]
    assert _figcaptions(impact) == numbered[6:7]
    steps = first_html[first_html.index(h_steps):first_html.index(h_evidence)]
    assert _figcaptions(steps) == numbered[7:8] and "not placed here" in steps
    evidence = first_html[first_html.index(h_evidence):first_html.index(h_fix)]
    assert _figcaptions(evidence) == numbered[8:32]
    assert "evidence:" not in re.sub(r"<pre>.*?</pre>", "", html, flags=re.S)   # no reference left as a target or text

    # Word: the same captions in the same order, each under a framed picture.
    paragraphs = _word_paragraphs(files["docx"])
    captions = [text for style, text, _ in paragraphs if style == "ImageCaption"]
    assert captions == numbered
    framed = [i for i, (_, _, is_framed) in enumerate(paragraphs) if is_framed]
    assert len(framed) == 33
    for i in framed:                                              # picture, then its caption
        assert paragraphs[i + 1][0] == "ImageCaption", paragraphs[i + 1]
    texts = [text for _, text, _ in paragraphs]
    assert texts.index("Intro text.") < texts.index(numbered[0]) < texts.index(numbered[5]) < texts.index(numbered[6])


@needs_quarto
def test_a_template_written_for_jinja_figure_numbers_keeps_rendering_and_one_may_drop_images(tmp_path):
    """``image(e, number=counter.figure)`` (v2.410.0) is accepted and the
    number ignored — the filter counts.  ``md(…, images=False)`` leaves placed
    images out of a field (a brief that prints no evidence), caption and all;
    ``image_width`` sets the placed figures' width."""
    folder = tmp_path / "tpl"
    folder.mkdir()
    (folder / "report.qmd").write_text(
        "---\ntitle: x\nengine: markdown\nfilters:\n  - quarto\n  - _bluestick/fields.lua\n"
        "format:\n  html:\n    embed-resources: true\n---\n\n"
        "<% set counter = namespace(figure=40) %>\n"
        "# Kept\n\n<< md(findings[0], \"description\", image_width=\"3in\") >>\n\n"
        "# Dropped\n\n<< md(findings[0], \"impact\", images=False) >>\n\n"
        "# Trailing\n\n<% for e in findings[0].evidence %>\n"
        "<% set counter.figure = counter.figure + 1 %>\n<< image(e, number=counter.figure) >>\n<% endfor %>\n"
    )
    one, two = _img(1, "Placed one"), _img(2, "Trailing two")
    data = {"findings": [{
        "_path": "findings.0",
        "description": "Text ![](evidence:1)",
        "impact": "Words before.\n\n![Dropped alt](evidence:1)\n\nWords after.",
        "images": [one, two], "evidence": [two],
        "placed": {"description": _placed(one), "impact": _placed(one)},
    }]}
    shot = tmp_path / "shot.png"
    shot.write_bytes(_png(20, 10))
    with pytest.raises(RenderError):     # a width is a width, nothing else
        quarto_render.jinja_environment(folder, data).from_string(
            '<< md(findings[0], "description", image_width="3in; x") >>').render(**data)
    files = quarto_render.render(folder, "report.qmd", data, ["html"], tmp_path / "out",
                                 resolve_evidence=lambda item: shot, timeout=240)
    html = files["html"].read_text(encoding="utf-8")
    assert _figcaptions(html) == ["Figure 1: Placed one", "Figure 2: Trailing two"]     # not 41
    assert 'style="width:3in"' in html and 'style="width:6in"' in html
    dropped = html[html.index(">Dropped<"):html.index(">Trailing<")]
    assert "Words before." in dropped and "Words after." in dropped
    assert "<figure" not in dropped and "Dropped alt" not in dropped
