"""Images in a finding's report text — the one place the rules live.

An image attached to a finding's comments (or its source-note thread) goes
into the client report when it is ticked "In report".  Its author gives it a
**caption**, and may **place** it inside one of that finding's written
sections with an ordinary Markdown image whose target is ``evidence:<id>``::

    ![The relayed session](evidence:57)

A ticked image no section places prints in the trailing evidence block.

This module decides, for every caller:

* what counts as a reference (``REFERENCE`` — the frontend's twin is
  ``utils/reportImages.ts``; change both together);
* which of a finding's images a section may show (``placements``) — the
  finding's OWN attachments, ticked, in a format the report can print.  The
  report's dataset builder passes that map to the renderer, whose Lua filter
  shows nothing else: a reference to another finding's image, to an image
  nobody ticked or to an id that does not exist prints as its alt text;
* that an image a section places cannot be deleted or un-ticked
  (``refuse_if_placed``) — the text would be left pointing at nothing;
* that an agent's proposed section only references images of that finding
  (``check_references``).

The host report (``report_generator``) does not use any of this.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

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

#: A placement: a Markdown image whose target is ``evidence:<attachment id>``,
#: with an optional (ignored) title.  The alt text may hold escaped brackets.
REFERENCE = re.compile(r'!\[(?P<alt>(?:[^\]\\\n]|\\.)*)\]\(\s*evidence:(?P<id>\d{1,12})(?:\s+"[^"\n]*")?\s*\)')

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


def referenced_ids(text: Optional[str]) -> List[int]:
    """The attachment ids a section's Markdown references, in order of first
    appearance (an id referenced twice is listed once)."""
    seen: List[int] = []
    for match in REFERENCE.finditer(text or ""):
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
