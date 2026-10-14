"""The report details a report INHERITS: who produced it, and its disclaimer.

Three levels, the nearest one that is written wins (owner, 2026-10-13: "make
it inherit from a global setting but allow teams to modify it per project"):

1. the report's own (``reports.settings``),
2. the project's report defaults (``report_profiles``),
3. the installation's (``report_defaults``, a global admin, System settings).

:func:`resolve` is the ONE place that order is applied — the dataset builder
calls it, so a preview, the issue and the page's "what this report will use"
cannot disagree.  With nothing written anywhere the value is None: the
template then prints its own disclaimer, and the author is a missing detail.

Storage of level 3 is one row per key; saving a blank value removes the row.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from app.db.models_reports import ReportDefault, ReportProfile

#: The inherited keys — also keys of a report's ``settings`` and columns of
#: ``report_profiles``.
KEYS: Tuple[str, ...] = ("author_name", "disclaimer")
LABELS: Dict[str, str] = {"author_name": "Author", "disclaimer": "Disclaimer"}
MAX_CHARS: Dict[str, int] = {"author_name": 255, "disclaimer": 32768}


def _clean(value: Any) -> Optional[str]:
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def installation(db: Session) -> Dict[str, Optional[str]]:
    """The installation's value for every key (None when not set)."""
    stored = {row.key: row.text for row in db.query(ReportDefault).filter(ReportDefault.key.in_(KEYS))}
    return {key: _clean(stored.get(key)) for key in KEYS}


def for_project(db: Session, profile: Optional[ReportProfile]) -> Dict[str, Optional[str]]:
    """What a report of this project uses when it writes none of its own:
    the project's, else the installation's."""
    base = installation(db)
    return {key: _clean(getattr(profile, key, None)) or base[key] for key in KEYS}


def resolve(db: Session, profile: Optional[ReportProfile], settings: Mapping[str, Any]) -> Dict[str, Optional[str]]:
    """The value in force for a report with these ``settings``."""
    inherited = for_project(db, profile)
    return {key: _clean(settings.get(key)) or inherited[key] for key in KEYS}


def as_dict(db: Session) -> dict:
    """What System settings shows: each key's text, and who last wrote one."""
    rows = {
        row.key: row for row in
        db.query(ReportDefault).options(joinedload(ReportDefault.updated_by)).populate_existing()
        if row.key in KEYS
    }
    latest = max(rows.values(), key=lambda row: row.updated_at, default=None)
    who = latest.updated_by if latest is not None else None
    return {
        "values": {key: (rows[key].text if key in rows else None) for key in KEYS},
        "labels": dict(LABELS),
        "max_chars": dict(MAX_CHARS),
        "updated_at": latest.updated_at.isoformat() if latest is not None else None,
        "updated_by": (who.full_name or who.username) if who is not None else None,
    }


def save(db: Session, values: Mapping[str, Optional[str]], user_id: Optional[int]) -> Dict[str, dict]:
    """Store the keys the caller sent → ``{key: {"from", "to"}}`` for those
    that changed.  A blank value removes the key.  Raises ``ValueError`` for
    an unknown key or a text that is too long."""
    rows = {row.key: row for row in db.query(ReportDefault).with_for_update().all()}
    changes: Dict[str, dict] = {}
    for key, value in values.items():
        if key not in KEYS:
            raise ValueError(f"Unknown report default '{key}'. Valid: {', '.join(KEYS)}")
        text = _clean(value)
        if text is not None and len(text) > MAX_CHARS[key]:
            raise ValueError(f"{LABELS[key]}: at most {MAX_CHARS[key]} characters.")
        row = rows.get(key)
        before = row.text if row is not None else None
        if text == before:
            continue
        if text is None:
            db.delete(row)
        elif row is None:
            db.add(ReportDefault(key=key, text=text, updated_by_id=user_id))
        else:
            row.text = text
            row.updated_by_id = user_id
        changes[key] = {"from": before, "to": text}
    db.flush()
    return changes
