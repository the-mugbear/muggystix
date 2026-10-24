"""Presentation mode (2.530.0): an issued report as bounded, readable slides
(`services/presentation_slides`, measured by `services/presentation_layout`).

Pins the pagination rules on worst-case content: every slide within its
measured budget (header and footer reserved), user text never cut (every
character of the source is on some slide), splits where the rules say
(sentence ends, then words, then inside a token; code between lines; tables by
rows with the header repeated, column groups when too wide), the same list on
every read, and the metrics table matching the shipped fonts' coverage.
The rendered check against a real browser is the Playwright suite
(frontend/e2e/presentation-overflow.spec.ts).
"""
from __future__ import annotations

import re

from app.services import presentation_layout as L
from app.services.presentation_slides import (
    STAGE, _body_room, _split_row, _words_that_fit, block_height, check_filter, parse_blocks, presentation_slides,
)

SENTENCE = "The service accepts weak ciphers and **legacy protocols**, which lets an attacker downgrade the session."
LONG_PARAGRAPH = " ".join([SENTENCE] * 40)
CODE = "```\n" + "\n".join(f"line {i}: " + "x" * (i % 150) for i in range(300)) + "\n```"
WIDE_TABLE = ("| " + " | ".join(f"Col{i}" for i in range(16)) + " |\n|" + "---|" * 16 + "\n"
              + "\n".join("| " + " | ".join(f"value {r}-{c} longer text here" for c in range(16)) + " |"
                          for r in range(30)))
URL = "https://example.com/" + "a" * 400
CJK = "中文测试文本" * 60
BOTH = {"findings": True, "endpoints": True}


def _dataset(**finding_over):
    finding = {
        "ref": "F-01", "title": "Weak TLS", "severity": "high", "cvss_score": 7.5, "status_note": None,
        "description": LONG_PARAGRAPH, "impact": CODE, "recommendation": WIDE_TABLE,
        "steps_to_reproduce": f"See {URL} for details. {CJK}",
        "images": [], "affected_count": 2000,
        "affected": [{"address": f"10.0.{i // 250}.{i % 250}", "hostname": ("very-long-hostname-" * 6) if (i * i) % 11 == 0
                      else f"h{i}", "name": None, "port": "443/tcp", "state": None} for i in range(2000)],
    }
    finding.update(finding_over)
    return {
        "report": {"title": "Internal penetration test", "heading": "Acme", "date": "2026-10-20", "number": 3},
        "counts": {"critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0, "total": 1},
        "executive_summary": LONG_PARAGRAPH,
        "scope": {"subnets": [{"cidr": f"10.{i // 250}.{i % 250}.0/24", "site": "DMZ" if i % 3 == 0 else "HQ",
                               "description": "Servers " * (i % 9)} for i in range(300)], "domains": []},
        "findings": [finding],
    }


def _over_budget(result):
    out = []
    for s in result["slides"]:
        h = sum(block_height(b) for b in s["blocks"])
        if s["kind"] == "image":
            h += s["image"]["height"]
        if h > _body_room(s["header"], s["footer"]) + 0.5:
            out.append(s["key"])
    return out


def _slides_of(result, prefix):
    return [s for s in result["slides"] if s["key"].startswith(prefix)]


def _text(blocks):
    out = []
    for b in blocks:
        if "md" in b:
            out.append(b["md"])
        if b["kind"] == "code":
            out.extend(b["lines"])
        if b["kind"] == "table":
            out.extend(c for r in b["rows"] for c in r)
    return " ".join(out)


def _squash(s):
    return re.sub(r"[\s*`]+", "", s)


def test_every_slide_is_within_its_budget():
    result = presentation_slides(_dataset())
    assert _over_budget(result) == []
    flt = check_filter(_dataset(), "site", "DMZ")
    assert _over_budget(presentation_slides(_dataset(), flt)) == []
    assert _over_budget(presentation_slides(_dataset(), flt, None, BOTH)) == []


def test_the_list_is_the_same_on_every_read():
    assert presentation_slides(_dataset()) == presentation_slides(_dataset())


def test_a_long_paragraph_splits_at_sentence_ends_and_loses_nothing():
    result = presentation_slides(_dataset())
    pages = _slides_of(result, "f:F-01:description:")
    assert len(pages) > 1
    for s in pages[:-1]:
        assert s["blocks"][-1]["md"].rstrip().endswith("session.")
    assert _squash(" ".join(_text(s["blocks"]) for s in pages)) == _squash(LONG_PARAGRAPH)
    assert [s["footer"].rsplit(" — ", 1)[1] for s in pages] == [f"{i} of {len(pages)}" for i in range(1, len(pages) + 1)]


def test_a_code_block_splits_between_lines_and_says_it_continues():
    pages = _slides_of(presentation_slides(_dataset()), "f:F-01:impact:")
    assert len(pages) > 1
    assert pages[0]["blocks"][0]["cont_after"] and pages[1]["blocks"][0]["cont_before"]
    lines = [x for s in pages for b in s["blocks"] for x in b["lines"]]
    assert lines == [f"line {i}: " + "x" * (i % 150) for i in range(300)]


def test_a_table_too_wide_is_shown_in_column_groups_with_the_header_repeated():
    pages = _slides_of(presentation_slides(_dataset()), "f:F-01:recommendation:")
    tables = [b for s in pages for b in s["blocks"] if b["kind"] == "table"]
    assert len(tables) > 1 and all(t.get("columns") for t in tables)
    assert all(t["header"][0] == "Col0" for t in tables)            # the first column repeated
    seen = {h for t in tables for h in t["header"]}
    assert seen == {f"Col{i}" for i in range(16)}
    for t in tables:
        assert abs(sum(t["widths"]) - L.BODY_WIDTH) < 1


def test_a_token_wider_than_a_line_breaks_inside_itself_and_cjk_is_counted_wide():
    pages = _slides_of(presentation_slides(_dataset()), "f:F-01:steps_to_reproduce:")
    assert _over_budget({"slides": pages}) == []
    text = _squash(" ".join(_text(s["blocks"]) for s in pages))
    assert URL in text and CJK in text
    # CJK at 2 em a glyph: 360 glyphs at 28 px over 1440 px lines.
    assert len(L.md_lines(CJK, L.BODY_WIDTH, 28)) >= (360 * 2 * 28) // L.BODY_WIDTH
    assert L._break_token(URL, 400, 28, False)[0].endswith("/")


def test_a_very_long_title_takes_its_slide_alone():
    result = presentation_slides(_dataset(title="Weak TLS configuration " * 20))
    first = _slides_of(result, "f:F-01")[:2]
    assert first[0]["key"] == "f:F-01" and [b["kind"] for b in first[0]["blocks"]] == ["title"]
    assert first[1]["key"] == "f:F-01:more:2" and first[1]["blocks"][-1]["kind"] == "facts"


def test_endpoint_rows_are_paged_by_measured_height_and_counted_once():
    pages = _slides_of(presentation_slides(_dataset()), "f:F-01:hosts:")
    rows = [r for s in pages for b in s["blocks"] if b["kind"] == "table" for r in b["rows"]]
    assert len(rows) == 2000
    spans = [re.search(r"Endpoints ([\d,]+)–([\d,]+) of 2,000", s["footer"]).groups() for s in pages]
    nums = [(int(a.replace(",", "")), int(b.replace(",", ""))) for a, b in spans]
    assert nums[0][0] == 1 and nums[-1][1] == 2000
    assert all(nxt[0] == cur[1] + 1 for cur, nxt in zip(nums, nums[1:]))
    # Rows whose hostname wraps take more room: not a fixed count per slide.
    assert len({len(b["rows"]) for s in pages for b in s["blocks"]}) > 1


def test_status_and_state_are_shown_only_where_the_template_prints_them():
    """Owner 2026-10-24 (option c): the presentation says what the report says."""
    ds = _dataset(status_note="Remediated during the assessment",
                  affected=[{"address": "10.0.0.1", "hostname": "h1", "name": None, "port": "443/tcp",
                             "state": "Remediated"}], affected_count=1)

    def shown(status_notes):
        result = presentation_slides(ds, None, None, status_notes)
        facts = next(b for b in _slides_of(result, "f:F-01")[0]["blocks"] if b["kind"] == "facts")
        table = next(b for b in _slides_of(result, "f:F-01:hosts:")[0]["blocks"] if b["kind"] == "table")
        return facts["status_note"], table["header"], table["rows"][0]

    note, header, row = shown(None)                     # a template that does not say: neither
    assert note is None and header == ["Address", "Name", "Port"] and row == ["10.0.0.1", "h1", "443/tcp"]
    assert shown({"findings": False, "endpoints": False}) == (note, header, row)
    note, header, row = shown(BOTH)
    assert note == "Remediated during the assessment"
    assert header == ["Address", "Name", "Port", "State"] and row[-1] == "Remediated"
    assert shown({"findings": True, "endpoints": False})[1:] == (["Address", "Name", "Port"],
                                                                 ["10.0.0.1", "h1", "443/tcp"])


def test_the_footer_says_what_a_filtered_page_is_part_of():
    ds = _dataset()
    pages = _slides_of(presentation_slides(ds, check_filter(ds, "site", "DMZ")), "f:F-01:hosts:")
    assert pages[0]["footer"].endswith(" of 750 matching DMZ · 2,000 affected")


def test_the_markdown_blocks_the_slides_render():
    blocks = parse_blocks("Para one\ncontinues.\n\n- item\n  more\n1. two\n\n> quote\n\n```\ncode\n```\n\n# Head\n\n---")
    assert [b["kind"] for b in blocks] == ["p", "li", "li", "quote", "code", "h", "rule"]
    assert blocks[1]["md"] == "item\nmore" and blocks[2]["marker"] == "1."


def test_the_metrics_table_covers_the_faces_the_stage_uses():
    faces = L._metrics()["faces"]
    assert set(faces) == {"sans-400-normal", "sans-400-italic", "sans-700-normal", "sans-700-italic",
                          "mono-400-normal", "mono-700-normal"}
    # Mono is monospaced; bold Latin is never narrower than regular.
    assert len({faces["mono-400-normal"][str(ord(c))] for c in "il0WM"}) == 1
    letters = [str(ord(c)) for c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    assert all(faces["sans-700-normal"][k] >= faces["sans-400-normal"][k] for k in letters)


def test_the_summarys_own_images_are_slides_at_their_place():
    """Code review 2026-10-24: images the executive summary places were dropped."""
    ds = _dataset()
    ds["executive_summary"] = ("The network is segmented.\n\n![Network diagram](evidence:41)\n\n"
                               "Two weaknesses stand out. ![Not printed](evidence:42)")
    ds["engagement"] = {"images": [
        {"attachment_id": 41, "caption": "Network diagram", "printed": True, "placed_in": ["executive_summary"]},
        {"attachment_id": 42, "caption": "Not printed", "printed": False, "placed_in": ["executive_summary"]},
    ]}
    result = presentation_slides(ds, None, {41})
    keys = [s["key"] for s in result["slides"]]
    assert keys[1:4] == ["summary:1", "summary:image:41", "summary:2"]
    assert "summary:image:42" not in keys
    image = next(s for s in result["slides"] if s["key"] == "summary:image:41")
    assert image["image"] == {"attachment_id": 41, "available": True, "height": image["image"]["height"]}
    assert image["blocks"][0]["md"] == "Network diagram"
    assert _over_budget(result) == []
    # Without the frozen copy it is a slide that says so.
    missing = presentation_slides(ds, None, set())
    assert next(s for s in missing["slides"] if s["key"] == "summary:image:41")["image"]["available"] is False


def _words_that_fit_one_by_one(words, fits):
    """The re-measure loop `_split_row` had before review 2026-10-24b."""
    best = 0
    for k in range(1, len(words) + 1):
        if fits(k):
            best = k
        else:
            break
    return best


def test_a_cell_taller_than_a_slide_splits_as_before_by_binary_search():
    """Old == new: the binary search finds the same cut as the word-by-word loop."""
    size, pad = STAGE["table_size"], 2 * STAGE["cell_pad_x"]
    cells = [
        SENTENCE * 30,
        "word " * 700,
        URL + " tail words after it",
        "**bold start** " + "plain " * 300 + "`code span` end",
        CJK + " mixed " + CJK,
        "",
        "one",
    ]
    for c in cells:
        words = re.split(r"[ \n]+", c)
        for width in (120, 300, 640):
            for room in (1, 3, 12):
                fits = (lambda k, w=width, r=room:  # noqa: E731
                        len(L.md_lines(" ".join(words[:k]), w - pad, size)) <= r)
                assert _words_that_fit(words, fits) == _words_that_fit_one_by_one(words, fits), (c[:30], width, room)
    # …and the row split itself still loses nothing.
    heads, tails = _split_row(["word " * 2000, "short"], [400.0, 200.0], 300.0)
    assert (heads[0] + " " + tails[0]).split() == ("word " * 2000).split()
