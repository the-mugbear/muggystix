"""An issued report as a list of slides (presentation mode, 2.530.0).

``presentation_slides(dataset, host_filter, frozen_images)`` turns a report's
FROZEN dataset into one ordered slide list: a pure function, the same list
for the same report and filter on every read, in every browser.  The shared
position of a presentation is a slide's ``key`` plus the host filter; the
server accepts a key only if this list holds it, so nothing outside the report
can be put on the shared screen.

Slides, in order:

* ``overview:<n>`` — title, the report's line, its date and number, the counts
  by severity (``n`` > 1 only when a long title pushes the counts on);
* ``summary:<n>`` — the executive summary;
* ``scope:<n>`` — the scope's subnets and domains;
* per finding, in report order: ``f:<ref>`` (title, severity, CVSS, how many
  endpoints; ``f:<ref>:more:<n>`` if a very long title needs its own slide),
  each written section's pages ``f:<ref>:<section>:<n>``, one slide per
  PRINTED evidence image ``f:<ref>:image:<attachment id>`` (where a section
  places it, else after the sections; a long caption continues on
  ``…:image:<id>:<n>``), and the affected endpoints ``f:<ref>:hosts:<n>`` —
  for the host filter, with one "no match" slide when nothing matches.

Pagination is by measured height (``presentation_layout``): a block that fits
is placed whole; one that does not is split deterministically — a paragraph at
sentence ends, then between words, a token wider than a line inside itself; a
code block between lines; a table by rows with its header repeated (and one
column group per slide when its columns cannot fit the width); a row taller
than a slide across slides.  User text is never cut.

Every slide carries its header line, its body blocks and its footer (the
report's number and date, the finding's reference, the section page — on
endpoint slides "Endpoints 13–24 of 57 matching DMZ · 240 affected").  The
footer's height is reserved from the measured footer text, so the context
labels never take room the body was promised.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

from app.services import presentation_layout as L
from app.services.presentation_layout import BODY_WIDTH, STAGE

SECTIONS = (
    ("description", "Description"),
    ("impact", "Impact"),
    ("recommendation", "Recommendation"),
    ("steps_to_reproduce", "Steps to reproduce"),
)
SEVERITY_LABEL = {
    "critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "info": "Informational",
}
SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")

#: The endpoint table's columns: share of the body width.
HOST_COLUMNS = (("Address", 0.24), ("Name", 0.46), ("Port", 0.16), ("State", 0.14))
SCOPE_SUBNET_COLUMNS = (("Subnet", 0.24), ("Site", 0.26), ("Description", 0.50))
SCOPE_DOMAIN_COLUMNS = (("Domain", 0.70), ("Subdomains", 0.30))

#: Fixed-height blocks.
COUNTS_HEIGHT = 150
FACTS_HEIGHT = 120
IMAGE_MIN_HEIGHT = 320          # an image slide keeps at least this much for the picture
CAPTION_SHARE = 0.4             # a caption longer than this share of the body continues on

_REF = re.compile(r"^[A-Za-z0-9-]{1,20}$")


class FilterError(ValueError):
    """A host filter this report cannot have (a 422)."""


# ---------------------------------------------------------------------------
# The host filter
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HostFilter:
    kind: str
    value: str

    def label(self) -> str:
        return self.value


def scope_sites(dataset: dict) -> List[str]:
    return sorted({s["site"] for s in (dataset.get("scope") or {}).get("subnets") or [] if s.get("site")})


def scope_segments(dataset: dict) -> List[str]:
    return [s["cidr"] for s in (dataset.get("scope") or {}).get("subnets") or [] if s.get("cidr")]


def check_filter(dataset: dict, kind: Optional[str], value: Optional[str]) -> Optional[HostFilter]:
    """The filter, or None; FilterError when the report cannot have it."""
    if kind is None and (value is None or value == ""):
        return None
    if kind not in ("segment", "site", "text"):
        raise FilterError("The host filter is a segment, a site or text.")
    value = (value or "").strip()
    if not value or len(value) > 200:
        raise FilterError("The host filter needs a value of at most 200 characters.")
    if kind == "segment" and value not in scope_segments(dataset):
        raise FilterError("That segment is not in this report's scope.")
    if kind == "site" and value not in scope_sites(dataset):
        raise FilterError("That site is not in this report's scope.")
    return HostFilter(kind, value)


def _matcher(dataset: dict, flt: Optional[HostFilter]) -> Callable[[dict], bool]:
    if flt is None:
        return lambda ep: True
    if flt.kind == "text":
        needle = flt.value.lower()
        return lambda ep: any(
            needle in str(ep.get(k) or "").lower() for k in ("address", "hostname", "name", "port")
        )
    if flt.kind == "segment":
        nets = [ipaddress.ip_network(flt.value, strict=False)]
    else:
        nets = [
            ipaddress.ip_network(s["cidr"], strict=False)
            for s in (dataset.get("scope") or {}).get("subnets") or []
            if s.get("site") == flt.value and s.get("cidr")
        ]

    def in_nets(ep: dict) -> bool:
        try:
            addr = ipaddress.ip_address(str(ep.get("address") or "").strip())
        except ValueError:
            return False
        return any(addr.version == n.version and addr in n for n in nets)

    return in_nets


# ---------------------------------------------------------------------------
# Markdown into blocks (the subset SafeMarkdown renders)
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


def _cells(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def parse_blocks(md: str) -> List[dict]:
    lines = (md or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: List[dict] = []
    i, n = 0, len(lines)
    para: List[str] = []

    def flush() -> None:
        if para:
            blocks.append({"kind": "p", "md": "\n".join(para)})
            para.clear()

    while i < n:
        line = lines[i]
        if not line.strip():
            flush()
            i += 1
            continue
        m = _FENCE.match(line)
        if m:
            flush()
            fence = m.group(1)[0] * 3
            body: List[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            i += 1
            blocks.append({"kind": "code", "lines": body, "cont_before": False, "cont_after": False})
            continue
        if not para and line.startswith("    "):
            body = []
            while i < n and (lines[i].startswith("    ") or not lines[i].strip()):
                body.append(lines[i][4:])
                i += 1
            while body and not body[-1].strip():
                body.pop()
            blocks.append({"kind": "code", "lines": body, "cont_before": False, "cont_after": False})
            continue
        if "|" in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            flush()
            header = _cells(line)
            aligns = []
            for c in _cells(lines[i + 1]):
                c = c.strip()
                aligns.append("center" if c.startswith(":") and c.endswith(":")
                              else "right" if c.endswith(":") else "left")
            rows = []
            i += 2
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_cells(lines[i]))
                i += 1
            width = len(header)
            rows = [(r + [""] * width)[:width] for r in rows]
            blocks.append({"kind": "table", "header": header, "align": (aligns + ["left"] * width)[:width],
                           "rows": rows})
            continue
        m = _HEADING.match(line)
        if m:
            flush()
            blocks.append({"kind": "h", "md": m.group(1)})
            i += 1
            continue
        if _RULE.match(line) and not para:
            blocks.append({"kind": "rule"})
            i += 1
            continue
        m = _ITEM.match(line)
        if m:
            flush()
            indent = len(m.group(1).replace("\t", "    "))
            marker = m.group(2)
            text = [m.group(3)]
            i += 1
            while i < n and lines[i].strip() and not _ITEM.match(lines[i]) and not _FENCE.match(lines[i]):
                text.append(lines[i].strip())
                i += 1
            blocks.append({"kind": "li", "md": "\n".join(text), "level": min(indent // 2, 4),
                           "marker": "•" if marker in "-*+" else marker, "cont": False})
            continue
        m = _QUOTE.match(line)
        if m:
            flush()
            text = []
            while i < n and _QUOTE.match(lines[i]):
                text.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            blocks.append({"kind": "quote", "md": "\n".join(text)})
            continue
        para.append(line)
        i += 1
    flush()
    return blocks


# ---------------------------------------------------------------------------
# Measuring and splitting blocks
# ---------------------------------------------------------------------------

S = STAGE


def _text_width_for(block: dict) -> float:
    if block["kind"] == "li":
        return BODY_WIDTH - S["list_indent"] * (block["level"] + 1)
    if block["kind"] == "quote":
        return BODY_WIDTH - S["quote_indent"]
    return BODY_WIDTH


def _col_widths(header: Sequence[str], rows: Sequence[Sequence[str]], width: float) -> Tuple[List[float], List[List[int]]]:
    pad = 2 * S["cell_pad_x"]
    size = S["table_size"]
    cols = len(header)
    natural, minimum = [], []
    for c in range(cols):
        cells = [header[c]] + [r[c] for r in rows]
        # The header is drawn bold; measure every cell of the column that way (an over-count only).
        nat = max((L.text_width("".join(t for t, _ in L.visible_runs(x)), size, face="bold") for x in cells), default=0)
        longest = max((L.text_width(w, size, face="bold") for x in cells for w in re.split(r"\s+", x) if w), default=0)
        natural.append(nat + pad)
        minimum.append(min(max(longest, 96) + pad, 360 + pad))
    # Whole table at its natural width, or squeezed down to the minimums.
    if sum(natural) <= width:
        scale = width / sum(natural) if sum(natural) else 1
        return [w * scale for w in natural], [list(range(cols))]
    if sum(minimum) <= width:
        spare = width - sum(minimum)
        give = [max(0.0, a - b) for a, b in zip(natural, minimum)]
        total = sum(give) or 1
        return [m + spare * g / total for m, g in zip(minimum, give)], [list(range(cols))]
    # Column groups: the first column repeated, then as many as fit.
    groups: List[List[int]] = []
    c = 1
    while c < cols:
        used = minimum[0]
        group = [0]
        while c < cols and (len(group) == 1 or used + minimum[c] <= width):
            group.append(c)
            used += minimum[c]
            c += 1
        groups.append(group)
    if cols == 1:
        groups = [[0]]
    widths = list(minimum)
    return widths, groups


def _row_height(cells: Sequence[str], widths: Sequence[float], *, bold: bool = False) -> float:
    size, line = S["table_size"], S["table_line"]
    pad = 2 * S["cell_pad_x"]
    tallest = max((len(L.md_lines(c, w - pad, size, bold=bold)) for c, w in zip(cells, widths)), default=1)
    return tallest * size * line + 2 * S["cell_pad_y"] + 1


def _row_heights(block: dict) -> List[float]:
    """Each row's height, measured once and carried with the block (a
    2,000-row table is not measured again for every slide it spans)."""
    rh = block.get("_rh")
    if rh is None or len(rh) != len(block["rows"]):
        rh = [_row_height(r, block["widths"]) for r in block["rows"]]
        block["_rh"] = rh
    return rh


def block_height(block: dict) -> float:
    k = block["kind"]
    gap = S["block_gap"]
    if k in ("p", "h", "quote"):
        return L.md_height(block["md"], _text_width_for(block), S["body_size"], S["body_line"], bold=k == "h") + gap
    if k == "li":
        return L.md_height(block["md"], _text_width_for(block), S["body_size"], S["body_line"]) + S["item_gap"]
    if k == "code":
        w = BODY_WIDTH - 2 * S["code_pad_x"]
        count = sum(L.mono_lines(x, w, S["code_size"]) for x in block["lines"]) or 1
        label = L.LABEL_HEIGHT if block.get("cont_before") else 0
        return count * S["code_size"] * S["code_line"] + 2 * S["code_pad_y"] + gap + label
    if k == "table":
        h = _row_height(block["header"], block["widths"], bold=True)
        label = L.LABEL_HEIGHT if block.get("columns") else 0
        return h + sum(_row_heights(block)) + gap + label
    if k == "rule":
        return S["rule_height"]
    if k == "title":
        return (len(L.md_lines(block["md"], BODY_WIDTH, S["title_size"], bold=True)) * S["title_size"] * S["title_line"]
                + S["title_gap"])
    if k == "meta":
        return L.md_height(block["md"], BODY_WIDTH, S["body_size"], S["body_line"]) + gap
    if k == "counts":
        return COUNTS_HEIGHT
    if k == "facts":
        return FACTS_HEIGHT
    raise ValueError(k)


_SENTENCE = re.compile(r"(?<=[.!?:;])\s+")
_MARKERS = ("**", "__", "~~", "`")


def _balance(head: str, tail: str) -> Tuple[str, str]:
    """Close an emphasis or code span the cut left open, and reopen it."""
    for mk in _MARKERS:
        count = head.count(mk) if mk != "`" else len(re.findall(r"(?<!`)`(?!`)", head))
        if count % 2:
            head, tail = head + mk, mk + tail
    return head, tail


def _split_text(block: dict, avail: float) -> Optional[Tuple[dict, dict]]:
    """Split a text block so its head fits ``avail``: at sentence ends, else
    between words, else inside a token.  None when not even one line fits."""
    md = block["md"]

    def fits(text: str) -> bool:
        return block_height({**block, "md": text}) <= avail

    for pieces, joiner in ((_SENTENCE.split(md), " "), (re.split(r"[ \n]+", md), " ")):
        if len(pieces) < 2:
            continue
        lo, hi, best = 1, len(pieces) - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if fits(joiner.join(pieces[:mid])):
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        if best:
            head, tail = _balance(joiner.join(pieces[:best]), joiner.join(pieces[best:]))
            return {**block, "md": head}, _continued(block, tail)
    # One token wider than the space left: inside itself.
    width = _text_width_for(block)
    size = S["body_size"]
    line_h = size * S["body_line"]
    lines_room = int((avail - S["block_gap"]) // line_h)
    if lines_room < 1:
        return None
    token_pieces = L._break_token(md.strip(), width, size, False)
    if len(token_pieces) <= lines_room:
        return None
    head = "".join(token_pieces[:lines_room])
    return {**block, "md": head}, _continued(block, "".join(token_pieces[lines_room:]))


def _continued(block: dict, md: str) -> dict:
    out = {**block, "md": md}
    if block["kind"] == "li":
        out["cont"] = True
    if block["kind"] == "h":
        out["kind"] = "p"
    return out


def _split_code(block: dict, avail: float) -> Optional[Tuple[dict, dict]]:
    w = BODY_WIDTH - 2 * S["code_pad_x"]
    room = avail - 2 * S["code_pad_y"] - S["block_gap"] - (L.LABEL_HEIGHT if block.get("cont_before") else 0)
    line_h = S["code_size"] * S["code_line"]
    used, k = 0.0, 0
    for x in block["lines"]:
        h = L.mono_lines(x, w, S["code_size"]) * line_h
        if used + h > room:
            break
        used += h
        k += 1
    if k == 0:
        # One source line taller than the space: break it across slides.
        pieces = L.mono_wrap(block["lines"][0], w, S["code_size"])
        fit = int(room // line_h)
        if fit < 1 or len(pieces) <= fit:
            return None
        head = {**block, "lines": ["".join(pieces[:fit])], "cont_after": True}
        tail = {**block, "lines": ["".join(pieces[fit:])] + block["lines"][1:], "cont_before": True}
        return head, tail
    if k >= len(block["lines"]):
        return None
    return ({**block, "lines": block["lines"][:k], "cont_after": True},
            {**block, "lines": block["lines"][k:], "cont_before": True})


def _split_row(cells: Sequence[str], widths: Sequence[float], room: float) -> Optional[Tuple[List[str], List[str]]]:
    """A row taller than a slide: each cell's text split so the head fits."""
    size, line = S["table_size"], S["table_line"]
    pad = 2 * S["cell_pad_x"]
    lines_room = int((room - 2 * S["cell_pad_y"] - 1) // (size * line))
    if lines_room < 1:
        return None
    heads, tails = [], []
    for c, w in zip(cells, widths):
        words = re.split(r"[ \n]+", c)
        best = 0
        for k in range(1, len(words) + 1):
            if len(L.md_lines(" ".join(words[:k]), w - pad, size)) <= lines_room:
                best = k
            else:
                break
        if best == 0 and words and words[0]:
            pieces = L._break_token(words[0], w - pad, size, False)
            head = "".join(pieces[:lines_room])
            tail = " ".join(["".join(pieces[lines_room:])] + words[1:]).strip()
        else:
            head, tail = " ".join(words[:best]), " ".join(words[best:])
        head, tail = _balance(head, tail)
        heads.append(head)
        tails.append(tail)
    return heads, tails


def _split_table(block: dict, avail: float) -> Optional[Tuple[dict, dict]]:
    widths = block["widths"]
    used = _row_height(block["header"], widths, bold=True) + S["block_gap"] + (L.LABEL_HEIGHT if block.get("columns") else 0)
    room = avail - used
    heights = _row_heights(block)
    k = 0
    for h in heights:
        if h > room:
            break
        room -= h
        k += 1
    base = {key: v for key, v in block.items() if key != "_rh"}
    if k == 0:
        if not block["rows"]:
            return None
        split = _split_row(block["rows"][0], widths, room)
        if split is None:
            return None
        head_cells, tail_cells = split
        # `row_cont`: the first row CONTINUES a row split on the slide before.
        return ({**base, "rows": [head_cells], "cont_after": True, "row_cont": block.get("row_cont", False)},
                {**base, "rows": [tail_cells] + block["rows"][1:], "cont_before": True, "row_cont": True})
    if k >= len(block["rows"]):
        return None
    return ({**base, "rows": block["rows"][:k], "cont_after": True, "_rh": heights[:k]},
            {**base, "rows": block["rows"][k:], "cont_before": True, "row_cont": False, "_rh": heights[k:]})


def split_block(block: dict, avail: float) -> Optional[Tuple[dict, dict]]:
    k = block["kind"]
    if k in ("p", "h", "li", "quote", "meta"):
        return _split_text(block, avail)
    if k == "title":
        out = _split_text({**block, "kind": "p"}, avail)
        if out is None:
            return None
        head, tail = out
        return {**head, "kind": "title"}, {**tail, "kind": "title"}
    if k == "code":
        return _split_code(block, avail)
    if k == "table":
        return _split_table(block, avail)
    return None


def paginate(blocks: Iterable[dict], avail: float) -> List[List[dict]]:
    """Blocks into pages of at most ``avail`` height each, splitting by the
    rules above.  A block that cannot be split and does not fit an empty page
    goes on a page of its own (the fit fallback then shows it whole)."""
    pages: List[List[dict]] = [[]]
    room = avail
    queue = list(blocks)
    while queue:
        b = queue.pop(0)
        h = block_height(b)
        if h <= room:
            pages[-1].append(b)
            room -= h
            continue
        out = split_block(b, room) if room > 0 else None
        if out is not None:
            head, tail = out
            pages[-1].append(head)
            queue.insert(0, tail)
            pages.append([])
            room = avail
            continue
        if pages[-1]:
            pages.append([])
            room = avail
            queue.insert(0, b)
            continue
        # An empty page and still too tall, with no split: alone.
        pages[-1].append(b)
        pages.append([])
        room = avail
    return [p for p in pages if p] or [[]]


# ---------------------------------------------------------------------------
# The slides
# ---------------------------------------------------------------------------

def _line_height(size_key: str, line_key: str, text: str, *, bold: bool = False) -> float:
    size = S[size_key]
    return len(L.md_lines(text, BODY_WIDTH, size, bold=bold)) * size * S[line_key]


def _body_room(header: str, footer_probe: str) -> float:
    head = (_line_height("header_size", "header_line", header, bold=True) + S["header_gap"]) if header else 0
    foot = _line_height("footer_size", "footer_line", footer_probe) + S["footer_gap"]
    return S["height"] - S["pad_top"] - S["pad_bottom"] - head - foot


def _report_line(dataset: dict) -> str:
    rep = dataset.get("report") or {}
    number = rep.get("number")
    bits = [f"Report #{number}" if number is not None else "Report"]
    if rep.get("date"):
        bits.append(f"issued {rep['date']}")
    return " · ".join(bits)


def _table_block(columns: Sequence[Tuple[str, float]], rows: List[List[str]]) -> dict:
    widths = [BODY_WIDTH * share for _, share in columns]
    return {"kind": "table", "header": [name for name, _ in columns], "align": ["left"] * len(columns),
            "rows": rows, "widths": widths}


def _tables_with_widths(blocks: List[dict]) -> List[dict]:
    """A written table gets its pixel widths, and a table too wide for the
    stage becomes one block per column group."""
    out: List[dict] = []
    for b in blocks:
        if b["kind"] != "table":
            out.append(b)
            continue
        widths, groups = _col_widths(b["header"], b["rows"], BODY_WIDTH)
        if len(groups) == 1:
            out.append({**b, "widths": widths})
            continue
        total = len(b["header"])
        for g in groups:
            out.append({
                "kind": "table",
                "header": [b["header"][c] for c in g],
                "align": [b["align"][c] for c in g],
                "rows": [[r[c] for c in g] for r in b["rows"]],
                "widths": [widths[c] * (BODY_WIDTH / sum(widths[x] for x in g)) for c in g],
                # Its columns, counted from 1 — the first is repeated on every group.
                "columns": (f"Columns 1–{g[-1] + 1} of {total}" if g[1] == 1
                            else f"Columns 1, {g[1] + 1}–{g[-1] + 1} of {total}") if len(g) > 1 else None,
            })
    return out


def _numbered(pages: List[List[dict]], key_of: Callable[[int], str], header: str, footer_of: Callable[[int, int], str],
              kind: str, extra: Optional[dict] = None) -> List[dict]:
    total = len(pages)
    slides = []
    for i, blocks in enumerate(pages, start=1):
        slides.append({
            "key": key_of(i), "kind": kind, "header": header, "blocks": blocks,
            "footer": footer_of(i, total), "page": i, "pages": total, **(extra or {}),
        })
    return slides


def presentation_slides(
    dataset: dict, host_filter: Optional[HostFilter] = None, frozen_images: Optional[Set[int]] = None,
) -> dict:
    """``{"stage", "slides", "filter"}`` for a frozen dataset.  ``frozen_images``
    are the attachment ids the report holds a copy of (``report_images``);
    a printed image outside it is a slide saying it is not available."""
    frozen_images = frozen_images or set()
    rep_line = _report_line(dataset)
    rep = dataset.get("report") or {}
    slides: List[dict] = []
    probe = "999 of 999"

    # Overview.
    head = ""
    room = _body_room(head, f"{rep_line} · Overview — {probe}")
    meta_bits = [b for b in (rep.get("heading"), rep_line) if b]
    counts = dataset.get("counts") or {}
    overview_blocks = [
        {"kind": "title", "md": L.strip_evidence_refs(rep.get("title") or "Report")},
        {"kind": "meta", "md": " · ".join(meta_bits)},
        {"kind": "counts", "counts": [
            {"severity": s, "label": SEVERITY_LABEL[s], "count": int(counts.get(s) or 0)} for s in SEVERITY_ORDER
        ], "total": int(counts.get("total") or 0)},
    ]
    slides += _numbered(
        paginate(overview_blocks, room), lambda i: f"overview:{i}", head,
        lambda i, t: f"{rep_line} · Overview" + (f" — {i} of {t}" if t > 1 else ""), "overview",
    )

    # Executive summary.
    summary = dataset.get("executive_summary") or ""
    if summary.strip():
        head = "Executive summary"
        room = _body_room(head, f"{rep_line} · Executive summary — {probe}")
        # The report's own images it places (``engagement.images``, the printed
        # ones), each a slide at its place — read from the frozen copies like a
        # finding's (code review 2026-10-24: they were dropped).
        own = {img["attachment_id"]: img for img in (dataset.get("engagement") or {}).get("images") or []
               if img.get("printed", True)}
        paged = _sequence(summary, room)
        total = sum(1 for what, _ in paged if what == "page")
        n = 0
        shown_own: Set[int] = set()
        for what, item in paged:
            if what == "image":
                if item in own and item not in shown_own:
                    shown_own.add(item)
                    slides += _image_slides(item, own[item], f"summary:image:{item}", "Executive summary · Image",
                                            f"{rep_line} · Executive summary · Image", frozen_images, probe)
                continue
            n += 1
            slides.append({
                "key": f"summary:{n}", "kind": "summary", "header": head, "blocks": item,
                "footer": f"{rep_line} · Executive summary" + (f" — {n} of {total}" if total > 1 else ""),
                "page": n, "pages": total,
            })

    # Scope.
    scope = dataset.get("scope") or {}
    subnets = scope.get("subnets") or []
    domains = scope.get("domains") or []
    if subnets or domains:
        head = "Scope"
        room = _body_room(head, f"{rep_line} · Scope — {probe}")
        blocks: List[dict] = []
        if subnets:
            blocks.append(_table_block(SCOPE_SUBNET_COLUMNS, [
                [s.get("cidr") or "", s.get("site") or "", s.get("description") or ""] for s in subnets
            ]))
        if domains:
            blocks.append(_table_block(SCOPE_DOMAIN_COLUMNS, [
                [d.get("domain") or "", "Included" if d.get("include_subdomains") else "—"] for d in domains
            ]))
        slides += _numbered(
            paginate(blocks, room), lambda i: f"scope:{i}", head,
            lambda i, t: f"{rep_line} · Scope" + (f" — {i} of {t}" if t > 1 else ""), "scope",
        )

    match = _matcher(dataset, host_filter)
    for finding in dataset.get("findings") or []:
        slides += _finding_slides(finding, rep_line, match, host_filter, frozen_images, probe)

    for slide in slides:
        for b in slide["blocks"]:
            b.pop("_rh", None)
    return {
        "stage": dict(STAGE),
        "slides": slides,
        "filter": None if host_filter is None else {"kind": host_filter.kind, "value": host_filter.value},
    }


def _image_slides(att_id: int, img: dict, key: str, header: str, footer: str, frozen_images: Set[int],
                  probe: str, extra: Optional[dict] = None) -> List[dict]:
    """One printed image as a slide (and, for a long caption, its continuation
    slides).  ``available`` only when the issue froze a copy that is still in
    the report's storage — never the live attachment."""
    caption = img.get("caption") or ""
    room_i = _body_room(header, f"{footer} — {probe}")
    cap_pages = paginate([{"kind": "p", "md": caption}], room_i * CAPTION_SHARE) if caption else [[]]
    first_cap, rest = cap_pages[0], cap_pages[1:]
    cap_h = sum(block_height(b) for b in first_cap)
    out = [{
        "key": key, "kind": "image", "header": header, "blocks": first_cap,
        "image": {"attachment_id": att_id, "available": att_id in frozen_images,
                  "height": max(IMAGE_MIN_HEIGHT, room_i - cap_h)},
        "footer": footer + (f" — 1 of {len(cap_pages)}" if rest else ""),
        "page": 1, "pages": len(cap_pages), **(extra or {}),
    }]
    if rest:
        more = paginate([b for page in rest for b in page], room_i)
        for i, blocks in enumerate(more, start=2):
            out.append({"key": f"{key}:{i}", "kind": "caption", "header": header, "blocks": blocks,
                        "footer": f"{footer} — {i} of {len(more) + 1}", "page": i, "pages": len(more) + 1,
                        **(extra or {})})
    return out


def _sequence(md: str, room: float) -> List[Tuple[str, object]]:
    """Written Markdown as ``[("page", blocks) | ("image", attachment id)]`` in
    reading order: the text paged, each image a placement names at its place."""
    sequence: List[Tuple[str, object]] = []
    run: List[dict] = []
    for b in parse_blocks(md):
        refs = L.evidence_refs(b["md"]) if "md" in b else []
        if refs:
            b = {**b, "md": L.strip_evidence_refs(b["md"])}
        if "md" not in b or b["md"].strip():
            run.append(b)
        if refs:
            if run:
                sequence.append(("blocks", run))
                run = []
            sequence.extend(("image", att) for att in refs)
    if run:
        sequence.append(("blocks", run))
    paged: List[Tuple[str, object]] = []
    for what, item in sequence:
        if what == "blocks":
            paged.extend(("page", p) for p in paginate(_tables_with_widths(item), room))
        else:
            paged.append(("image", item))
    return paged


def _finding_slides(finding: dict, rep_line: str, match, host_filter: Optional[HostFilter],
                    frozen_images: Set[int], probe: str) -> List[dict]:
    ref = str(finding.get("ref") or "")
    if not _REF.match(ref):
        return []
    sev = SEVERITY_LABEL.get(finding.get("severity"), finding.get("severity") or "")
    slides: List[dict] = []
    base = f"{rep_line} · {ref}"

    # The finding's own slide.
    head = f"{ref} · {sev}"
    room = _body_room(head, f"{base} — {probe}")
    title = {"kind": "title", "md": L.strip_evidence_refs(finding.get("title") or "")}
    facts = {"kind": "facts", "severity": finding.get("severity"), "severity_label": sev,
             "cvss_score": finding.get("cvss_score"), "status_note": finding.get("status_note"),
             "affected_count": int(finding.get("affected_count") or 0)}
    title_lines = len(L.md_lines(title["md"], BODY_WIDTH, S["title_size"], bold=True))
    if title_lines > 3:
        pages = paginate([title], room) + [[facts]]
    else:
        pages = paginate([title, facts], room)
    slides += _numbered(
        pages, lambda i: f"f:{ref}" if i == 1 else f"f:{ref}:more:{i}", head,
        lambda i, t: base + (f" — {i} of {t}" if t > 1 else ""), "finding", {"ref": ref},
    )

    images = {img["attachment_id"]: img for img in finding.get("images") or [] if img.get("printed", True)}
    shown: Set[int] = set()

    def image_slides(att_id: int) -> List[dict]:
        if att_id in shown or att_id not in images:
            return []
        shown.add(att_id)
        return _image_slides(att_id, images[att_id], f"f:{ref}:image:{att_id}", f"{ref} · Evidence",
                             f"{base} · Evidence", frozen_images, probe, {"ref": ref})

    for field, label in SECTIONS:
        md = finding.get(field) or ""
        if not md.strip():
            continue
        head = f"{ref} · {label}"
        room = _body_room(head, f"{base} · {label} — {probe}")
        paged = _sequence(md, room)
        total = sum(1 for what, _ in paged if what == "page")
        n = 0
        for what, item in paged:
            if what == "image":
                slides += image_slides(item)
                continue
            n += 1
            slides.append({
                "key": f"f:{ref}:{field}:{n}", "kind": "section", "header": head, "blocks": item,
                "footer": f"{base} · {label}" + (f" — {n} of {total}" if total > 1 else ""),
                "page": n, "pages": total, "ref": ref, "section": field,
            })

    # Printed images no section placed.
    for att_id in images:
        slides += image_slides(att_id)

    # Affected endpoints, for the filter.
    affected = finding.get("affected") or []
    matched = [ep for ep in affected if match(ep)]
    total_affected = int(finding.get("affected_count") or len(affected))
    head = f"{ref} · Affected endpoints"
    flabel = f" matching {host_filter.label()}" if host_filter else ""
    room = _body_room(head, f"{base} · Endpoints 999,999–999,999 of 999,999{flabel} · 999,999 affected")
    if not matched:
        message = (f"No affected endpoints match {host_filter.label()}." if host_filter
                   else "No affected endpoints are listed.")
        slides.append({
            "key": f"f:{ref}:hosts:1", "kind": "hosts", "header": head, "ref": ref,
            "blocks": [{"kind": "p", "md": message}], "empty": True,
            "footer": f"{base} · 0 of {total_affected:,} endpoints{flabel}",
            "page": 1, "pages": 1,
        })
        return slides
    rows = [[
        str(ep.get("address") or ""), str(ep.get("name") or ep.get("hostname") or ""),
        str(ep.get("port") or ""), str(ep.get("state") or ""),
    ] for ep in matched]
    table = _table_block(HOST_COLUMNS, rows)
    pages = paginate([table], room)
    of = f"{len(matched):,}{flabel} · {total_affected:,} affected" if host_filter else f"{total_affected:,}"
    done = 0                       # endpoints whose row has STARTED on an earlier slide
    for i, blocks in enumerate(pages, start=1):
        rows_here = sum(len(b.get("rows") or []) for b in blocks if b["kind"] == "table")
        continuing = any(b.get("row_cont") for b in blocks)
        # A row split across slides is counted on the slide it starts on.
        first = done if continuing else done + 1
        last = done + rows_here - (1 if continuing else 0)
        if last < first:
            span = f"Endpoint {done:,} (continued)"
        else:
            span = f"Endpoints {first:,}–{last:,}"
            done = last
        footer = f"{base} · {span} of {of}"
        slides.append({
            "key": f"f:{ref}:hosts:{i}", "kind": "hosts", "header": head, "ref": ref, "blocks": blocks,
            "footer": footer, "page": i, "pages": len(pages),
        })
    return slides


def slide_keys(result: dict) -> List[str]:
    return [s["key"] for s in result["slides"]]


def first_hosts_key(result: dict, ref: str) -> Optional[str]:
    for s in result["slides"]:
        if s.get("ref") == ref and s["kind"] == "hosts":
            return s["key"]
    return None
