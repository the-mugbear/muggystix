"""The presentation stage's layout budget (presentation mode, 2.530.0).

Every slide is laid out on a fixed 1600 × 900 logical stage that the browser
scales as one unit, so a line is as wide on every screen.  The server decides
what goes on each slide, so it must know how tall a block will be BEFORE any
browser draws it.  It measures text with the advance widths of the fonts the
stage ships (``data/presentation_metrics.json``, generated from the same font
files by ``frontend/scripts/generate-presentation-metrics.mjs``), adds a safety
margin, and breaks lines where a browser may.

This is an ESTIMATE meant to over-count, never a claim of exactness: the
Playwright suite checks it against real layout, and a slide that still
overflows in some browser is fitted there and reported
(``presentation_layout_reports``).  With a system font standing in for the
shipped ones, nothing here is guaranteed.

The constants below are the ONE statement of the stage's sizes: the slides
answer carries them (``STAGE``) and the frontend sets them as CSS variables, so
what is measured here and what is drawn there cannot drift apart.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Sequence

_METRICS_FILE = Path(__file__).resolve().parent.parent / "data" / "presentation_metrics.json"

#: The per-line safety margin for kerning and shaping (a starting value; the
#: Playwright overflow suite sets the value that holds).
SAFETY_MARGIN = 0.04

#: The stage, in logical pixels.  Sent to the browser as-is.
STAGE: Dict[str, float] = {
    "width": 1600, "height": 900,
    "pad_x": 80, "pad_top": 56, "pad_bottom": 32,
    "header_size": 24, "header_line": 1.3, "header_gap": 20,
    "footer_size": 18, "footer_line": 1.35, "footer_gap": 16,
    "title_size": 44, "title_line": 1.2, "title_gap": 24,
    "body_size": 28, "body_line": 1.4,
    "code_size": 22, "code_line": 1.45, "code_pad_y": 16, "code_pad_x": 24,
    "table_size": 22, "table_line": 1.35, "cell_pad_y": 8, "cell_pad_x": 12,
    "caption_size": 22, "caption_line": 1.35,
    "block_gap": 20, "item_gap": 8, "list_indent": 40, "quote_indent": 28, "rule_height": 24,
    # The small label above a code block that continues, or a table's column group.
    "label_size": 18, "label_line": 1.35, "label_gap": 8,
}

LABEL_HEIGHT = STAGE["label_size"] * STAGE["label_line"] + STAGE["label_gap"]

BODY_WIDTH = STAGE["width"] - 2 * STAGE["pad_x"]

# Text is measured with the faces it is drawn in: plain text with the widest of
# regular and italic, bold text (``**…**``, headings, titles, table headers)
# with the widest of bold and bold italic, code with mono.  (Measuring every
# run as bold over-counted so much that narrow table cells wrapped twice as
# often as drawn — Chrome pass 2026-10-24.)
REGULAR_FACES = ("sans-400-normal", "sans-400-italic")
BOLD_FACES = ("sans-700-normal", "sans-700-italic")
MONO_FACES = ("mono-400-normal", "mono-700-normal")


def _faces(face) -> tuple:
    """``face``: "sans", "bold" or "mono" (``True`` / ``False`` read as mono / sans)."""
    if face is True or face == "mono":
        return MONO_FACES
    if face == "bold":
        return BOLD_FACES
    return REGULAR_FACES

# Where a token wider than a line may be broken, best first.
_SOFT_BREAK_AFTER = set("/?&=-_.")


@lru_cache(maxsize=1)
def _metrics() -> dict:
    return json.loads(_METRICS_FILE.read_text())


@lru_cache(maxsize=4)
def _face_table(faces: tuple) -> Dict[int, float]:
    """The widest advance of each code point over ``faces`` (em): an italic
    run is never wider than it was counted."""
    table: Dict[int, float] = {}
    for face in faces:
        for cp, w in _metrics()["faces"][face].items():
            cp = int(cp)
            if table.get(cp, 0.0) < w:
                table[cp] = w
    return table


def _glyph_em(ch: str, table: Dict[int, float]) -> float:
    cp = ord(ch)
    w = table.get(cp)
    if w is not None:
        return w
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0.0
    # Not in the shipped fonts: drawn by a system font.  Count the worst case.
    if unicodedata.east_asian_width(ch) in ("W", "F") or cp >= 0x1F000:
        return 2.0
    return 1.0


def text_width(text: str, size: float, *, mono=False, face=None) -> float:
    table = _face_table(_faces(face if face is not None else mono))
    return sum(_glyph_em(ch, table) for ch in text) * size * (1 + SAFETY_MARGIN)


# ---------------------------------------------------------------------------
# Inline Markdown: what is drawn, and in which family
# ---------------------------------------------------------------------------

_EVIDENCE_REF = re.compile(r"!\[[^\]]*\]\(\s*evidence:(\d+)\s*\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_AUTOLINK = re.compile(r"<((?:https?|mailto):[^>\s]+)>")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")


def evidence_refs(md: str) -> List[int]:
    return [int(m.group(1)) for m in _EVIDENCE_REF.finditer(md or "")]


def strip_evidence_refs(md: str) -> str:
    return _EVIDENCE_REF.sub("", md or "")


_BOLD_MARK = re.compile(r"\*\*|__")


def _bold_runs(text: str, bold: bool) -> List[tuple]:
    """Plain text split at ``**`` / ``__`` into (text, "sans" | "bold") runs;
    the markers stay in the count (an over-count of a few characters)."""
    out: List[tuple] = []
    at, on = 0, bold
    for m in _BOLD_MARK.finditer(text):
        if bold:
            break
        if m.start() > at:
            out.append((text[at:m.start()], "bold" if on else "sans"))
        out.append((m.group(0), "bold"))
        at, on = m.end(), not on
    if at < len(text):
        out.append((text[at:], "bold" if (on or bold) else "sans"))
    return out


def visible_runs(md: str, *, bold: bool = False) -> List[tuple]:
    """``[(text, face)]`` — the text a browser draws for inline Markdown, in
    reading order, with the face it is drawn in ("sans", "bold", "mono"): link
    and image destinations dropped (SafeMarkdown shows a link's text and an
    image's alt), code spans mono, ``**…**`` bold (``bold=True``: everything
    not code — a heading, a title, a table header).  Emphasis markers are KEPT
    in the count — an over-count by a few characters, never an under-count."""
    s = _EVIDENCE_REF.sub("", md)
    s = _IMAGE.sub(lambda m: m.group(1), s)
    s = _LINK.sub(lambda m: m.group(1), s)
    s = _AUTOLINK.sub(lambda m: m.group(1), s)
    runs: List[tuple] = []
    at = 0
    for m in _CODE_SPAN.finditer(s):
        if m.start() > at:
            runs.extend(_bold_runs(s[at:m.start()], bold))
        runs.append((m.group(2), "mono"))
        at = m.end()
    if at < len(s):
        runs.extend(_bold_runs(s[at:], bold))
    return runs


# ---------------------------------------------------------------------------
# Line breaking
# ---------------------------------------------------------------------------

def _break_token(token: str, width: float, size: float, mono) -> List[str]:
    """A token wider than a line, broken inside itself: after one of
    ``/ ? & = - _ .`` when one is in reach, else between any two characters
    (CSS ``overflow-wrap: anywhere`` on the stage does the same)."""
    table = _face_table(_faces(mono))
    scale = size * (1 + SAFETY_MARGIN)
    widths = [_glyph_em(ch, table) * scale for ch in token]
    pieces: List[str] = []
    start = 0
    n = len(token)
    while start < n:
        # One pass: the longest run from `start` that fits (at least one character).
        acc, end = 0.0, start
        while end < n and (end == start or acc + widths[end] <= width):
            acc += widths[end]
            end += 1
        if end >= n:
            pieces.append(token[start:])
            break
        cut = end
        for i in range(end, max(start + 1, end - 24) - 1, -1):
            if token[i - 1] in _SOFT_BREAK_AFTER:
                cut = i
                break
        pieces.append(token[start:cut])
        start = cut
    return pieces or [token]


def wrap_runs(runs: Sequence[tuple], width: float, size: float) -> List[str]:
    """Greedy line breaking over ``(text, face)`` runs at spaces, as a
    browser does; a token wider than a line by the rule above.  Returns the
    lines' texts (for splitting); ``len()`` is the line count.  A hard break
    (a newline in the text) ends a line."""
    words: List[tuple] = []          # (text, face, hard_break_before)
    for text, mono in runs:
        for li, line in enumerate(text.split("\n")):
            parts = line.split(" ")
            for pi, w in enumerate(parts):
                if w == "" and not (li and pi == 0):
                    continue
                words.append((w, mono, li > 0 and pi == 0))
    lines: List[str] = []
    cur, cur_w = "", 0.0
    space = text_width(" ", size)
    for w, mono, hard in words:
        if hard and cur:
            lines.append(cur)
            cur, cur_w = "", 0.0
        if not w:
            continue
        ww = text_width(w, size, face=mono)
        if cur and cur_w + space + ww <= width:
            cur += " " + w
            cur_w += space + ww
            continue
        if cur:
            lines.append(cur)
            cur, cur_w = "", 0.0
        if ww <= width:
            cur, cur_w = w, ww
        else:
            pieces = _break_token(w, width, size, mono)
            lines.extend(pieces[:-1])
            cur, cur_w = pieces[-1], text_width(pieces[-1], size, face=mono)
    if cur:
        lines.append(cur)
    return lines or [""]


def md_lines(md: str, width: float, size: float, *, bold: bool = False) -> List[str]:
    """Lines of inline Markdown at ``size`` in a box ``width`` wide (all of
    it bold when ``bold``).  A single newline is a space (as SafeMarkdown and
    the report read it), a hard break (two trailing spaces or a backslash)
    ends a line."""
    text = re.sub(r"(  |\\)\n", "\x00", md or "")
    text = text.replace("\n", " ").replace("\x00", "\n")
    return wrap_runs(visible_runs(text, bold=bold), width, size)


def md_height(md: str, width: float, size: float, line: float, *, bold: bool = False) -> float:
    return len(md_lines(md, width, size, bold=bold)) * size * line


_RUNS = re.compile(r" +|[^ ]+")


def mono_wrap(code_line: str, width: float, size: float) -> List[str]:
    """One source line of code as the lines a browser draws it in
    (``white-space: pre-wrap`` + ``overflow-wrap: anywhere``): breaks at
    spaces first — a word that does not fit the rest of the line moves to the
    next — and only a word wider than a whole line is broken inside itself.
    Spaces are kept (indentation counts); a line's trailing spaces hang."""
    text = code_line.replace("\t", "    ")
    if not text:
        return [""]
    lines: List[str] = []
    cur, cur_w = "", 0.0
    for tok in _RUNS.findall(text):
        tw = text_width(tok, size, mono=True)
        if tok.startswith(" "):
            # Spaces never wrap a line; at its end they hang.
            cur += tok
            cur_w += tw
            continue
        if cur_w + tw <= width:
            cur += tok
            cur_w += tw
            continue
        if cur:
            # The word moves to the next line (counted so even after a run of
            # indentation only: an over-count, never an under-count).
            lines.append(cur)
            cur, cur_w = "", 0.0
        if tw <= width:
            cur, cur_w = tok, tw
            continue
        pieces = _break_token(tok, width, size, True)
        lines.extend(pieces[:-1])
        cur = pieces[-1]
        cur_w = text_width(cur, size, mono=True)
    lines.append(cur)
    return lines


def mono_lines(code_line: str, width: float, size: float) -> int:
    """How many lines one source line of code takes."""
    return len(mono_wrap(code_line, width, size))
