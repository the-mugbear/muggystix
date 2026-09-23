"""A finding's report text (v2.379.0) — the one list of its fields and the
seeding rules.

Kept free of model and session imports so the Alembic revision that seeds
existing findings can use exactly the functions promotion uses.
"""
from __future__ import annotations

import json
from typing import Optional

from app.services.cvss_service import normalize_cvss

# The Markdown fields.  One list, so the endpoint, the report builder and the
# report page's "missing text" count agree.
REPORT_TEXT_FIELDS = ("description", "impact", "recommendation", "references", "steps_to_reproduce")
# Longest accepted value per field — a report section, not an evidence dump.
REPORT_TEXT_MAX = 32768


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
