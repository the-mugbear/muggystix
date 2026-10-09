"""A finding's report text (v2.379.0) — the one list of its fields and the
seeding rules.

Kept free of model and session imports so the Alembic revision that seeds
existing findings can use exactly the functions promotion uses.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

from app.services.cvss_service import normalize_cvss

# The Markdown fields.  One list, so the endpoint, the report builder and the
# report page's "missing text" count agree.
REPORT_TEXT_FIELDS = ("description", "impact", "recommendation", "references", "steps_to_reproduce")
# Longest accepted value per field — a report section, not an evidence dump.
REPORT_TEXT_MAX = 32768


# A BlueStick record named by its number ("Finding #277", "evidence record
# 57", "host test #12"), or the product itself.  The report's reader has never
# seen BlueStick: such a phrase points at nothing they can open.  Only forms
# that cannot be the client's own subject matter — a bare "session ID 4821" or
# "user_id=5" is what a finding may be about, and is left alone.
_RECORD = (
    r"(?:findings?|evidence(?:\s+records?)?|proposals?|host\s+tests?|"
    r"(?:scanner\s+)?observations?|hosts?|agent\s+sessions?|vulnerabilit(?:y|ies))"
)
INTERNAL_REFERENCE = re.compile(
    rf"\b{_RECORD}\s*#\s?\d+"
    r"|\b(?:finding|evidence(?:\s+record)?|proposal|host\s+test|(?:scanner\s+)?observation)\s+ids?\s*[:=#]?\s*\d+"
    r"|\bevidence\s+records?\s+\d+"
    r"|\bBlueStick\b",
    re.IGNORECASE,
)
#: How many phrases a message quotes.
INTERNAL_REFERENCE_QUOTED = 3


def internal_references(text: Optional[str]) -> List[str]:
    """The phrases of ``text`` that name a BlueStick record or BlueStick
    itself, in order, each once.  Code spans and fenced blocks are not read:
    a command's output is shown as it was."""
    if not text or not INTERNAL_REFERENCE.search(text):
        return []
    from app.services.report_images import code_spans

    code = code_spans(text)
    found: List[str] = []
    for match in INTERNAL_REFERENCE.finditer(text):
        if any(a <= match.start() < b for a, b in code):
            continue
        phrase = " ".join(match.group(0).split())
        if phrase.casefold() not in {p.casefold() for p in found}:
            found.append(phrase)
    return found


def internal_reference_message(phrases: List[str]) -> str:
    """Why text holding ``phrases`` is not report text, and what to write."""
    quoted = ", ".join(f"“{p}”" for p in phrases[:INTERNAL_REFERENCE_QUOTED])
    more = len(phrases) - INTERNAL_REFERENCE_QUOTED
    if more > 0:
        quoted += f" and {more} more"
    return (
        f"{quoted} means nothing to the report's reader, who has never seen BlueStick. "
        "Name a finding by its title and a system by its address or hostname, "
        "and leave record numbers out of report text."
    )


def report_text_of(finding) -> dict:
    """A finding's report text as the finding page and the agent's finding
    detail both return it (v2.428.0): the Markdown fields, the CVSS vector and
    score, and whether the vector decides the score (3.x / 2.0)."""
    from app.services.cvss_service import CvssError, score_vector

    from_vector = False
    if finding.cvss_vector:
        try:
            from_vector = score_vector(finding.cvss_vector)[1] is not None
        except CvssError:
            from_vector = False
    return {
        **{f: getattr(finding, f) for f in REPORT_TEXT_FIELDS},
        "cvss_vector": finding.cvss_vector,
        "cvss_score": finding.cvss_score,
        "cvss_score_from_vector": from_vector,
    }


def clip(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:REPORT_TEXT_MAX] or None


def references_markdown(raw: Optional[str]) -> Optional[str]:
    """A scanner's ``references`` (a JSON array of URLs/ids, or plain text) as
    a Markdown list.  Anything unreadable is kept as the text it was."""
    if not raw or not str(raw).strip():
        return None
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        return str(raw).strip()
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        return str(raw).strip()
    lines = [f"- {str(i).strip()}" for i in items if str(i).strip()]
    return "\n".join(lines) or None


def seed_report_text_from_vuln(finding, vuln) -> None:
    """Pre-fill a NEW finding's report text from the scanner row it was
    promoted from.  Only empty fields are filled, and only at creation — after
    that the text is the author's (a corroborating scanner never rewrites it)."""
    if not finding.description:
        finding.description = clip(getattr(vuln, "description", None))
    if not finding.recommendation:
        finding.recommendation = clip(getattr(vuln, "solution", None))
    if not finding.references:
        finding.references = clip(references_markdown(getattr(vuln, "references", None)))
    if finding.cvss_vector is None and finding.cvss_score is None:
        finding.cvss_vector, finding.cvss_score = normalize_cvss(
            getattr(vuln, "cvss_vector", None), getattr(vuln, "cvss_score", None), strict=False,
        )
