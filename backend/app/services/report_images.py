"""Images in a finding's report text — the one place the rules live.

An image attached to a finding's comments (or its source-note thread) goes
into the client report when it is ticked "In report".  Its author gives it a
**caption**, and may **place** it inside one of that finding's written
sections with an ordinary Markdown image whose target is ``evidence:<id>``::

    ![The relayed session](evidence:57)

A ticked image no section places prints in the trailing evidence block.

This module decides, for every caller:

* what counts as a reference (``REFERENCE`` — the frontend's twin is
  ``utils/reportImages.ts``; change both together).  ONE grammar: other
  Markdown spellings of the same image place nothing, and
  ``normalise_references`` rewrites them to it where a section is saved;
* which of a finding's images a section may show (``placements``) — the
  finding's OWN attachments, ticked, in a format the report can print.  The
  report's dataset builder passes that map to the renderer, whose Lua filter
  shows nothing else: a reference to another finding's image, to an image
  nobody ticked or to an id that does not exist prints as its alt text;
* that an image a section places cannot be deleted or un-ticked
  (``refuse_if_placed``) — the text would be left pointing at nothing;
* that an agent's proposed section only references images of that finding
  (``check_references``).

The host inventory downloads (``report_generator``) carry no images and do not
use any of this.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, load_only

from app.db.models import Annotation, NoteAttachment
from app.db.models_findings import Finding
from app.services.report_text import REPORT_TEXT_FIELDS

#: Longest caption accepted.  A caption is a sentence or two under a figure.
CAPTION_MAX = 2000

#: Formats every renderer can place (Word and HTML alike).
REPORT_IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}

#: A placement — THE grammar, on every side (review 2026-10-01 M1): a Markdown
#: image on one line whose target is ``evidence:<attachment id>``, with an
#: optional (ignored) title in double quotes.  The alt text may hold escaped
#: brackets.  This pattern, and nothing wider, decides "placed" for the
#: dataset, for ``refuse_if_placed``, for an agent's proposal and — through
#: its twin ``EVIDENCE_REFERENCE`` in the frontend's ``utils/reportImages.ts``
#: — for the Preview.  The report's Lua filter shows an image only where the
#: dataset's ``placed`` map lists it, so what this does not match never prints
#: as a figure, whatever pandoc would make of it.
#:
#: Markdown has other spellings of the same image (``(<evidence:57>)``, a
#: title in single quotes or parentheses, an alt text over two lines or with
#: brackets in it, a reference-style image).  They are NOT placements; they
#: are rewritten to this form when a section is saved
#: (``normalise_references``), so an author who typed one gets what they
#: meant instead of an image that silently prints elsewhere.
REFERENCE = re.compile(r'!\[(?P<alt>(?:[^\]\\\n]|\\.)*)\]\(\s*evidence:(?P<id>\d{1,12})(?:\s+"[^"\n]*")?\s*\)')

# --- the tolerated spellings, for normalise_references only -----------------
# Alt text over several lines and with one level of balanced brackets.
_LOOSE_ALT = r'(?P<alt>(?:[^\[\]\\]|\\.|\[(?:[^\[\]\\]|\\.)*\])*)'
_LOOSE_TITLE = r'''(?:\s+(?P<title>"[^"\n]*"|'[^'\n]*'|\([^()\n]*\)))?'''
_LOOSE = re.compile(
    r'!\[' + _LOOSE_ALT + r'\]\(\s*(?:<\s*evidence:(?P<angled>\d{1,12})\s*>|evidence:(?P<bare>\d{1,12}))'
    + _LOOSE_TITLE + r'\s*\)'
)
# `[label]: evidence:57` — a reference definition whose target is an image id.
_DEFINITION = re.compile(
    r'''^ {0,3}\[(?P<label>(?:[^\[\]\\\n]|\\.)+)\]:[ \t]*<?evidence:(?P<id>\d{1,12})>?'''
    r'''(?:[ \t]+(?:"[^"\n]*"|'[^'\n]*'|\([^()\n]*\)))?[ \t]*$''',
    re.M,
)
# `![alt][label]`, `![label][]` and `![label]` (not followed by a target).
_BY_LABEL = re.compile(
    r'!\[' + _LOOSE_ALT + r'\](?:\[(?P<label>(?:[^\[\]\\\n]|\\.)*)\]|(?![\[(]))'
)
_FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
_TICKS = re.compile(r'`+')
_BLANK_LINE = re.compile(r'\n[ \t]*\n')
_UNESCAPED_BRACKET = re.compile(r'(\\.)|([\[\]])', re.S)


def _code_spans(text: str) -> List[Tuple[int, int]]:
    """``(start, end)`` of every fenced code block and inline code span:
    what is written there is shown as typed, so it is never rewritten."""
    spans: List[Tuple[int, int]] = []
    prose: List[Tuple[int, int]] = []
    offset = 0
    fence: Optional[str] = None
    start = 0
    for line in text.splitlines(keepends=True):
        opened = _FENCE.match(line)
        if fence is None:
            if opened:
                fence, start = opened.group(1), offset
            else:
                prose.append((offset, offset + len(line)))
        elif opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence) \
                and not line[opened.end():].strip():
            spans.append((start, offset + len(line)))
            fence = None
        offset += len(line)
    if fence is not None:                     # an unclosed fence runs to the end
        spans.append((start, len(text)))
    # Inline code: a run of backticks up to the next run of the same length.
    # Runs of prose lines are joined first (a span may cross a line break).
    merged: List[Tuple[int, int]] = []
    for a, b in prose:
        if merged and merged[-1][1] == a:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    for a, b in merged:
        runs = [m for m in _TICKS.finditer(text, a, b)]
        i = 0
        while i < len(runs):
            width = runs[i].end() - runs[i].start()
            close = next((j for j in range(i + 1, len(runs))
                          if runs[j].end() - runs[j].start() == width), None)
            if close is None:
                i += 1
                continue
            spans.append((runs[i].start(), runs[close].end()))
            i = close + 1
    return sorted(spans)


def _canonical(alt: str, att_id: str, title: Optional[str] = None) -> str:
    """``![alt](evidence:id "title")`` — the alt text on one line with its
    brackets escaped, the title in double quotes (dropped when it holds one)."""
    alt = " ".join(alt.split()) if "\n" in alt else alt
    alt = _UNESCAPED_BRACKET.sub(lambda m: m.group(1) or "\\" + m.group(2), alt)
    inner = (title or "")[1:-1]
    suffix = f' "{inner}"' if title and '"' not in inner else ""
    return f"![{alt}](evidence:{att_id}{suffix})"


def normalise_references(text: Optional[str]) -> Optional[str]:
    """``text`` with every tolerated spelling of an image placement rewritten
    to the one form ``REFERENCE`` reads — call it where a report-text section
    is SAVED, before anything else looks at the text.

    Rewritten: a target in angle brackets (``(<evidence:57>)``), a title in
    single quotes or parentheses, an alt text that runs over a line break or
    holds brackets (they are escaped), and a reference-style image
    (``![alt][shot]`` / ``![shot][]`` / ``![shot]`` with
    ``[shot]: evidence:57`` — the definition line stays, it prints nothing).
    Nothing else changes: other images and links, text in a code block or a
    code span, and a reference already in the one form are returned as they
    are, so the function is idempotent.  ``None`` and ``""`` come back as
    given."""
    if not text or "evidence:" not in text:
        return text
    code = _code_spans(text)

    def in_code(pos: int) -> bool:
        return any(a <= pos < b for a, b in code)

    labels = {
        " ".join(m.group("label").split()).casefold(): m.group("id")
        for m in reversed(list(_DEFINITION.finditer(text))) if not in_code(m.start())
    }   # reversed: the FIRST definition of a label wins, as in Markdown

    def inline(match: "re.Match[str]") -> str:
        if in_code(match.start()) or _BLANK_LINE.search(match.group("alt")) or REFERENCE.fullmatch(match.group(0)):
            return match.group(0)
        return _canonical(match.group("alt"), match.group("angled") or match.group("bare"), match.group("title"))

    def by_label(match: "re.Match[str]") -> str:
        alt = match.group("alt")
        if in_code(match.start()) or _BLANK_LINE.search(alt):
            return match.group(0)
        label = match.group("label") or alt
        att_id = labels.get(" ".join(label.split()).casefold())
        return _canonical(alt, att_id) if att_id else match.group(0)

    out = _LOOSE.sub(inline, text)
    if labels:
        code = _code_spans(out)           # offsets moved with the rewrite above
        out = _BY_LABEL.sub(by_label, out)
    return out

FIELD_LABELS = {
    "description": "Description", "impact": "Impact", "recommendation": "Recommendation",
    "references": "References", "steps_to_reproduce": "Steps to reproduce",
}


def clean_caption(value: Optional[str]) -> Optional[str]:
    """A caption as stored: one line of plain text, or None when empty.
    Raises ``ValueError`` when it is longer than ``CAPTION_MAX``."""
    if value is None:
        return None
    text = " ".join(str(value).replace("\x00", " ").split())
    if len(text) > CAPTION_MAX:
        raise ValueError(f"A caption is at most {CAPTION_MAX} characters (this one is {len(text)}).")
    return text or None


def iter_placements(text: Optional[str]) -> Iterator["re.Match[str]"]:
    """Every REAL placement in a section's Markdown, in order: a ``REFERENCE``
    match that is not inside a fenced code block or an inline code span.

    Code is printed as typed — the renderer makes no figure of
    ``![Example](evidence:57)`` written in backticks — so it places nothing
    (review 2026-10-02 H5: such an example took the image out of the trailing
    evidence block and blocked its deletion, while the report printed no
    figure at all).  This is the ONE reader of ``REFERENCE`` for "placed":
    ``referenced_ids`` and, through it, the dataset, ``refuse_if_placed`` and
    ``check_references`` all come here.  The frontend's twin skips the same
    ranges."""
    if not text or "evidence:" not in text:
        return
    code = _code_spans(text) if ("`" in text or "~~~" in text) else []
    for match in REFERENCE.finditer(text):
        if any(a <= match.start() < b for a, b in code):
            continue
        yield match


def referenced_ids(text: Optional[str]) -> List[int]:
    """The attachment ids a section's Markdown PLACES, in order of first
    appearance (an id referenced twice is listed once).  A reference written
    inside code is not one (``iter_placements``)."""
    seen: List[int] = []
    for match in iter_placements(text):
        att_id = int(match.group("id"))
        if att_id not in seen:
            seen.append(att_id)
    return seen


def references_by_field(finding) -> Dict[str, List[int]]:
    """Field → the attachment ids its text references (any id, valid or not)."""
    out = {}
    for field in REPORT_TEXT_FIELDS:
        ids = referenced_ids(getattr(finding, field, None))
        if ids:
            out[field] = ids
    return out


def fields_referencing(finding, attachment_id: int) -> List[str]:
    return [field for field, ids in references_by_field(finding).items() if attachment_id in ids]


def field_names(fields: Sequence[str]) -> str:
    return ", ".join(FIELD_LABELS.get(f, f) for f in fields)


def printable(attachment) -> bool:
    return attachment.content_type in REPORT_IMAGE_TYPES


def evidence_file(attachment) -> Optional[str]:
    ext = REPORT_IMAGE_TYPES.get(attachment.content_type)
    return f"evidence/{attachment.id}.{ext}" if ext else None


def caption_or_filename(attachment) -> str:
    return (getattr(attachment, "caption", None) or "").strip() or attachment.filename


def attachments_of(
    db: Session, findings: Iterable[Tuple[int, Optional[int]]], *, marked_only: bool,
) -> Dict[int, List[Tuple[NoteAttachment, Optional[str]]]]:
    """Finding id → its attachments (the report service's one lookup)."""
    from app.services.client_report_service import finding_image_attachments

    return finding_image_attachments(db, findings, marked_only=marked_only)


def finding_images(db: Session, finding) -> List[dict]:
    """Every image attached to the finding, as the finding page and the
    agents' finding read both show them: id, caption, whether it is ticked
    for the report, whether the report can print its format, and the
    sections that place it."""
    rows = attachments_of(db, [(finding.id, finding.evidence_annotation_id)], marked_only=False)
    references = references_by_field(finding)
    out = []
    for att, actor_type in rows.get(finding.id, []):
        out.append({
            "id": att.id,
            "note_id": att.annotation_id,
            "filename": att.filename,
            "caption": att.caption,
            "content_type": att.content_type,
            "size_bytes": att.size_bytes,
            "in_report": bool(att.include_in_report),
            "printable": printable(att),
            "placed_in": [field for field, ids in references.items() if att.id in ids],
            "uploaded_by_id": att.uploaded_by_id,
            "by_agent": actor_type == "agent",
            "created_at": att.created_at,
        })
    return out


def findings_of_attachments(db: Session, attachment_ids: Iterable[int]) -> Dict[int, List[Finding]]:
    """Attachment id → the findings it belongs to: the finding whose comment
    it hangs on, and every finding whose source-note thread holds it."""
    ids = sorted({int(i) for i in attachment_ids})
    if not ids:
        return {}
    rows = (
        db.query(NoteAttachment.id, Annotation.id, Annotation.finding_id, Annotation.thread_root_id)
        .join(Annotation, Annotation.id == NoteAttachment.annotation_id)
        .filter(NoteAttachment.id.in_(ids))
        .all()
    )
    finding_ids = {fid for _a, _n, fid, _r in rows if fid}
    roots = {n for _a, n, _f, _r in rows} | {r for _a, _n, _f, r in rows if r}
    conds = []
    if finding_ids:
        conds.append(Finding.id.in_(finding_ids))
    if roots:
        conds.append(Finding.evidence_annotation_id.in_(roots))
    if not conds:
        return {}
    findings = (
        db.query(Finding)
        .options(load_only(
            Finding.id, Finding.title, Finding.evidence_annotation_id,
            *[getattr(Finding, f) for f in REPORT_TEXT_FIELDS],
        ))
        .filter(or_(*conds))
        .all()
    )
    by_id = {f.id: f for f in findings}
    by_root: Dict[int, List[Finding]] = defaultdict(list)
    for f in findings:
        if f.evidence_annotation_id:
            by_root[f.evidence_annotation_id].append(f)
    out: Dict[int, List[Finding]] = {}
    for att_id, note_id, finding_id, root_id in rows:
        owners = {}
        if finding_id in by_id:
            owners[finding_id] = by_id[finding_id]
        for f in by_root.get(note_id, []) + by_root.get(root_id, []):
            owners[f.id] = f
        out[att_id] = list(owners.values())
    return out


def placements_of(db: Session, attachment_ids: Iterable[int]) -> List[Tuple[int, Finding, List[str]]]:
    """``(attachment id, finding, fields)`` for every finding whose CURRENT
    text places one of the attachments."""
    out = []
    for att_id, findings in findings_of_attachments(db, attachment_ids).items():
        for finding in findings:
            fields = fields_referencing(finding, att_id)
            if fields:
                out.append((att_id, finding, fields))
    return out


def refuse_if_placed(db: Session, attachment_ids: Iterable[int], *, action: str, then: str) -> None:
    """409 when a finding's text places one of the images — naming the
    section(s) and what to do.  ``action`` is what was asked ("delete this
    image"); ``then`` what to do afterwards ("delete it")."""
    placed = placements_of(db, attachment_ids)
    if not placed:
        return
    parts = []
    for att_id, finding, fields in placed[:5]:
        parts.append(
            f"image {att_id} is placed in the {field_names(fields)} of finding "
            f"#{finding.id} \"{(finding.title or '')[:80]}\""
        )
    more = f" (and {len(placed) - 5} more)" if len(placed) > 5 else ""
    first_id = placed[0][0]
    raise HTTPException(
        status_code=409,
        detail=(
            f"Cannot {action}: {'; '.join(parts)}{more}. The report would be left with a "
            f"reference to nothing. Remove the reference — ![…](evidence:{first_id}) — from "
            f"that section first, then {then}."
        ),
    )


def check_references(
    db: Session, finding, texts: Dict[str, Optional[str]], *, require_marked: bool,
) -> None:
    """An agent's proposed section(s) for ``finding`` may reference only that
    finding's images.

    422 names every id that is not an image of this finding (another
    finding's, a deleted one, a format the report cannot print).  With
    ``require_marked`` (accepting the proposal), an image that is the
    finding's but not ticked "In report" is a 409: ticking is a person's
    decision about what a client may see, so accepting never does it."""
    wanted: Dict[int, List[str]] = defaultdict(list)
    for field, text in texts.items():
        for att_id in referenced_ids(text):
            wanted[att_id].append(field)
    if not wanted:
        return
    images = {img["id"]: img for img in finding_images(db, finding)} if finding is not None else {}
    unknown = sorted(i for i in wanted if i not in images or not images[i]["printable"])
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"evidence:{', evidence:'.join(str(i) for i in unknown)} "
                + ("is not an image" if len(unknown) == 1 else "are not images")
                + (f" of finding #{finding.id}" if finding is not None else " of this finding (a new finding has none yet)")
                + ". A section may only place the finding's own PNG, JPEG or GIF images — "
                "its `images` list has their ids."
            ),
        )
    if require_marked:
        unmarked = sorted(i for i in wanted if not images[i]["in_report"])
        if unmarked:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This text places image{'s' if len(unmarked) > 1 else ''} "
                    f"{', '.join(str(i) for i in unmarked)}, which "
                    f"{'are' if len(unmarked) > 1 else 'is'} not ticked \"In report\". Tick "
                    "it on the finding's page if the client may see it, or edit the text to "
                    "remove the reference, then accept."
                ),
            )


def report_placement(
    finding, ticked: Sequence[NoteAttachment],
) -> Tuple[List[dict], Dict[str, Dict[str, dict]], List[dict]]:
    """``(images, placed, unplaced)`` for the report's dataset.

    ``ticked`` is the finding's attachments marked "In report" in a format
    the report prints, in attachment order.  ``images`` is all of them;
    ``placed`` maps each section to the images its text references
    (``{field: {"<id>": {attachment_id, file, caption}}}`` — the ONLY images
    the renderer shows in that section); ``unplaced`` is what is left for the
    trailing evidence block."""
    by_id = {att.id: att for att in ticked}
    references = references_by_field(finding)
    placed: Dict[str, Dict[str, dict]] = {}
    placed_in: Dict[int, List[str]] = defaultdict(list)
    for field, ids in references.items():
        for att_id in ids:
            att = by_id.get(att_id)
            if att is None:
                continue
            placed.setdefault(field, {})[str(att_id)] = {
                "attachment_id": att_id, "file": evidence_file(att), "caption": caption_or_filename(att),
            }
            placed_in[att_id].append(field)
    images = [
        {
            "attachment_id": att.id, "file": evidence_file(att), "caption": caption_or_filename(att),
            "placed_in": placed_in.get(att.id, []),
        }
        for att in ticked
    ]
    return images, placed, [img for img in images if not img["placed_in"]]
