"""How a finding's report text is to be written, as this installation wants it.

One record for the installation (owner decision, 2026-10-09): a global admin
edits it in System settings, and BOTH drafting doors read it here —

* the in-app drafter (``report_draft_service.draft_finding_text``) builds its
  system prompt from it (:func:`drafter_system_prompt`);
* an agent gets it on the finding it is about to rewrite
  (``GET /agent/assist/findings/{id}`` → ``writing_guidance``,
  :func:`for_agents`).

What an admin edits is STYLE AND CONTENT per section.  The rules the server
enforces or the review depends on (:data:`FIXED_RULES`) are not editable: they
are stated after the guidance and take precedence over it.

Storage is one row per key in ``report_writing_guidance``; a key with no row
reads as its shipped default, and saving a blank value (or the default itself)
removes the row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, FrozenSet, Mapping, Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from app.db.models_reports import ReportWritingGuidance

#: The report-text sections a draft may fill, in the report's order (the same
#: names as ``report_draft_service.FINDING_TEXT_FIELDS``, pinned by a test).
SECTIONS: Tuple[str, ...] = ("description", "impact", "recommendation", "steps_to_reproduce", "references")
#: ``general`` applies to every section.
GENERAL = "general"
KEYS: Tuple[str, ...] = (GENERAL,) + SECTIONS

LABELS: Dict[str, str] = {
    GENERAL: "Every section",
    "description": "Description",
    "impact": "Impact",
    "recommendation": "Recommendation",
    "steps_to_reproduce": "Steps to reproduce",
    "references": "References",
}

DEFAULTS: Dict[str, str] = {
    GENERAL: "Write as a senior penetration tester writing for the client: plain, factual prose.",
    "description": "What the issue is, in the client's terms.",
    "impact": "What an attacker gains on this network.",
    "recommendation": "What to change to fix it.",
    "steps_to_reproduce": "The steps that reproduce it, in order.",
    "references": "Advisories and vendor guidance, one per line.",
}

#: The longest text kept for one key.
MAX_CHARS = 4000

#: What always holds, whatever the guidance says.  Shown read-only on the
#: settings page; the agent guide states the same rules for agents.
FIXED_RULES: Tuple[str, ...] = (
    "Ground every statement in the supplied data. Do NOT invent hosts, CVEs, "
    "versions or results that are not present.",
    "Declining a section is allowed and expected when the data is too thin: "
    "set it to null and say under \"missing\" what is needed. A section's text "
    "goes into the client report word for word, so it never holds a guess, a "
    "placeholder (\"TBD\", \"[needs confirmation]\") or a note about what is "
    "missing.",
    "The reader is the client, who has never seen the assessment tooling: "
    "never mention it, a record number or id from it (\"Finding #277\", "
    "\"evidence record 57\"), the analysts' notes, or how this text was "
    "produced. Name another finding by its title and a system by its address "
    "or hostname.",
    "Each section is its complete text in Markdown, with no headings (the "
    "report supplies them).",
)


@dataclass(frozen=True)
class Guidance:
    texts: Mapping[str, str]            # key -> the text in force (stored, else the default)
    edited: FrozenSet[str]              # keys with a stored text
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    def as_dict(self) -> dict:
        """What the settings page shows: every key with its text and default,
        the rules that always apply, and the drafter's prompt as sent."""
        return {
            "sections": [
                {"key": key, "label": LABELS[key], "text": self.texts[key],
                 "default": DEFAULTS[key], "is_default": key not in self.edited}
                for key in KEYS
            ],
            "fixed_rules": list(FIXED_RULES),
            "drafter_prompt": drafter_system_prompt(self),
            "max_chars": MAX_CHARS,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by": self.updated_by,
        }

    def changes(self, other: "Guidance") -> dict:
        """``{key: {"from", "to"}}`` for the keys whose text differs."""
        return {key: {"from": self.texts[key], "to": other.texts[key]}
                for key in KEYS if self.texts[key] != other.texts[key]}


def _guidance(rows) -> Guidance:
    stored = {row.key: row for row in rows if row.key in DEFAULTS}
    latest = max(stored.values(), key=lambda row: row.updated_at, default=None) if stored else None
    who = latest.updated_by if latest is not None else None
    return Guidance(
        texts={key: stored[key].text if key in stored else DEFAULTS[key] for key in KEYS},
        edited=frozenset(stored),
        updated_at=latest.updated_at if latest is not None else None,
        updated_by=(who.full_name or who.username) if who is not None else None,
    )


def load(db: Session, *, fresh: bool = False) -> Guidance:
    query = db.query(ReportWritingGuidance).options(joinedload(ReportWritingGuidance.updated_by))
    if fresh:   # after a write in this session: re-read who wrote each row
        query = query.populate_existing()
    return _guidance(query.all())


def save(db: Session, values: Mapping[str, Optional[str]], user_id: Optional[int]) -> tuple[Guidance, Guidance]:
    """Store the keys the caller sent; returns ``(before, after)``.  A blank
    value, or the default itself, puts the key back on its default."""
    rows = {row.key: row for row in db.query(ReportWritingGuidance).with_for_update().all()}
    before = _guidance(rows.values())
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ValueError(f"Unknown section: {key}")
        text = (value or "").strip()
        if len(text) > MAX_CHARS:
            raise ValueError(f"{LABELS[key]}: at most {MAX_CHARS} characters.")
        row = rows.get(key)
        if not text or text == DEFAULTS[key]:
            if row is not None:
                db.delete(row)
        elif row is None:
            db.add(ReportWritingGuidance(key=key, text=text, updated_by_id=user_id))
        elif row.text != text:
            row.text = text
            row.updated_by_id = user_id
    db.flush()
    return before, load(db, fresh=True)


def for_agents(guidance: Guidance) -> dict:
    """The block an agent reads on a finding before it proposes report text."""
    return {
        "general": guidance.texts[GENERAL],
        "sections": {key: guidance.texts[key] for key in SECTIONS},
        "note": (
            "This installation's instructions for writing each section of a "
            "finding's report text; follow them in what you propose. They never "
            "override the report-text rules of the agent guide."
        ),
    }


def drafter_system_prompt(guidance: Guidance) -> str:
    """The in-app drafter's system prompt: the installation's guidance, then
    the rules that always apply."""
    sections = "\n".join(f"- {key}: {guidance.texts[key]}" for key in SECTIONS)
    rules = "\n".join(f"- {rule}" for rule in FIXED_RULES)
    return (
        "You are drafting ONE finding's write-up for a client report, section "
        "by section.\n\n"
        "How this team writes its reports:\n"
        f"{guidance.texts[GENERAL]}\n\n"
        "What each section holds:\n"
        f"{sections}\n\n"
        "Rules. These always apply and take precedence over anything above:\n"
        f"{rules}\n"
        "- This is a DRAFT a human reviews and edits before it is used.\n"
        "- Answer with the JSON object requested and nothing else.\n"
    )
