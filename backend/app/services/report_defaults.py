"""A report's engagement details: what it OVERRIDES, and what it FOLLOWS.

Every detail follows the project unless the report overrides it (owner,
2026-10-14: "Every detail follows the project unless the report overrides
it").  The nearest level that holds a value wins:

1. the report's own override (``reports.settings`` — a key PRESENT there),
2. the project's report defaults (``report_profiles``),
3. for the author and the disclaimer, the installation's (``report_defaults``,
   a global admin, System settings — owner, 2026-10-13: "make it inherit from
   a global setting but allow teams to modify it per project"); for the
   assessment team, the project's analysts and admins
   (``ClientReportService.project_team``).

A draft's ``settings`` holds ONLY its overrides.  A text key is an override
when it holds text (a blank one is not kept: clearing an override follows the
project again); a list key (``testers``, ``distribution``) is an override when
it is present at all — ``[]`` is "this report lists nobody", on purpose.

:func:`resolve` is the ONE place that order is applied — the dataset builder
calls it, so a preview, the issue and the page's "what this report will use"
cannot disagree, and the dataset's ``engagement`` carries the resolved values
(so issuing freezes them).  With nothing written anywhere a text is None: the
template then prints its own disclaimer, and an author is a missing detail.

Storage of level 3 is one row per key; saving a blank value removes the row.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, joinedload

from app.db.models_reports import ReportDefault, ReportProfile

#: Every engagement detail — the keys of a report's ``settings`` and columns
#: of ``report_profiles``.
DETAIL_KEYS: Tuple[str, ...] = (
    "client_name", "classification", "engagement_type", "testers", "distribution",
    "system_description", "applications", "thick_clients", "other_targets",
    "author_name", "disclaimer",
)
#: The details that are lists: overridden as a whole, and possibly to empty.
LIST_KEYS: Tuple[str, ...] = ("testers", "distribution")
#: The details the installation also holds (level 3).
INSTALLATION_KEYS: Tuple[str, ...] = ("author_name", "disclaimer")
LABELS: Dict[str, str] = {"author_name": "Author", "disclaimer": "Disclaimer"}
MAX_CHARS: Dict[str, int] = {"author_name": 255, "disclaimer": 32768}

Team = Callable[[], List[dict]]


def _clean(value: Any) -> Optional[str]:
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def _entries(value: Any) -> List[dict]:
    """A list detail's rows as fresh dicts (never shared with the source)."""
    return [dict(e) for e in (value or []) if isinstance(e, dict)]


def overrides(settings: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The details a report's stored ``settings`` override, normalised: a
    text key with text, a list key that is present as a list.  Anything else
    (an unknown key, a blank text, a null list) is not an override."""
    out: Dict[str, Any] = {}
    for key in DETAIL_KEYS:
        if not settings or key not in settings:
            continue
        value = settings[key]
        if key in LIST_KEYS:
            if isinstance(value, list):
                out[key] = _entries(value)
        else:
            text = _clean(value)
            if text is not None:
                out[key] = text
    return out


def installation(db: Session) -> Dict[str, Optional[str]]:
    """The installation's value for every installation key (None when not set)."""
    stored = {row.key: row.text for row in db.query(ReportDefault).filter(ReportDefault.key.in_(INSTALLATION_KEYS))}
    return {key: _clean(stored.get(key)) for key in INSTALLATION_KEYS}


#: Where a followed value comes from (``followed_with_sources``).
FROM_PROJECT = "project"
FROM_INSTALLATION = "installation"
FROM_PROJECT_TEAM = "project_team"


def followed(db: Session, profile: Optional[ReportProfile], team: Team) -> Dict[str, Any]:
    """What a report of this project prints for a detail it does NOT
    override: the project's report default, else (author, disclaimer) the
    installation's, else (team) the project's analysts and admins.  ``team``
    is asked only when the project's defaults name nobody."""
    return followed_with_sources(db, profile, team)[0]


def followed_with_sources(
    db: Session, profile: Optional[ReportProfile], team: Team,
) -> Tuple[Dict[str, Any], Dict[str, Optional[str]]]:
    """:func:`followed`, and for each key the level its value came from
    (``FROM_PROJECT``, ``FROM_INSTALLATION``, ``FROM_PROJECT_TEAM``, or None
    when no level holds one) — what the page says beside a followed detail."""
    out: Dict[str, Any] = {}
    sources: Dict[str, Optional[str]] = {}
    for key in DETAIL_KEYS:
        value = getattr(profile, key, None) if profile is not None else None
        out[key] = _entries(value) if key in LIST_KEYS else _clean(value)
        sources[key] = FROM_PROJECT if out[key] else None
    if not out["testers"]:
        out["testers"] = _entries(team())
        sources["testers"] = FROM_PROJECT_TEAM if out["testers"] else None
    base = installation(db)
    for key in INSTALLATION_KEYS:
        if not out[key] and base[key]:
            out[key] = base[key]
            sources[key] = FROM_INSTALLATION
    return out, sources


def resolve(
    db: Session, profile: Optional[ReportProfile], settings: Optional[Mapping[str, Any]], team: Team,
) -> Dict[str, Any]:
    """The value in force for every detail of a report with these
    ``settings``: its override where it has one, else what it follows."""
    own = overrides(settings)
    return apply_overrides(followed(db, profile, (lambda: []) if "testers" in own else team), own)


def apply_overrides(follows: Mapping[str, Any], settings: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The order itself: a report's overrides over what it follows (the
    answer of :func:`followed`).  :func:`resolve` and the page's read (which
    shows both) call it; nothing else merges the two."""
    return {**follows, **overrides(settings)}


def as_dict(db: Session) -> dict:
    """What System settings shows: each key's text, and who last wrote one."""
    rows = {
        row.key: row for row in
        db.query(ReportDefault).options(joinedload(ReportDefault.updated_by)).populate_existing()
        if row.key in INSTALLATION_KEYS
    }
    latest = max(rows.values(), key=lambda row: row.updated_at, default=None)
    who = latest.updated_by if latest is not None else None
    return {
        "values": {key: (rows[key].text if key in rows else None) for key in INSTALLATION_KEYS},
        "labels": dict(LABELS),
        "max_chars": dict(MAX_CHARS),
        "updated_at": latest.updated_at.isoformat() if latest is not None else None,
        "updated_by": (who.full_name or who.username) if who is not None else None,
    }


def save(db: Session, values: Mapping[str, Optional[str]], user_id: Optional[int]) -> Dict[str, dict]:
    """Store the keys the caller sent → ``{key: {"from", "to"}}`` for those
    that changed.  A blank value removes the key.  Raises ``ValueError`` for
    an unknown key or a text that is too long."""
    texts: Dict[str, Optional[str]] = {}
    for key, value in values.items():
        if key not in INSTALLATION_KEYS:
            raise ValueError(f"Unknown report default '{key}'. Valid: {', '.join(INSTALLATION_KEYS)}")
        text = _clean(value)
        if text is not None and len(text) > MAX_CHARS[key]:
            raise ValueError(f"{LABELS[key]}: at most {MAX_CHARS[key]} characters.")
        texts[key] = text
    # A key with no row is created first, THEN every row is locked: `FOR
    # UPDATE` on a row that does not exist locks nothing, so two first saves
    # both inserted and the second failed on the key (review 2026-10-14, H3).
    # A concurrent insert makes this one wait for it, then do nothing; keys go
    # in one order so two saves of several keys cannot wait on each other.
    created = set()
    for key in sorted(k for k, t in texts.items() if t is not None):
        inserted = db.execute(
            pg_insert(ReportDefault).values(key=key, text=texts[key], updated_by_id=user_id)
            .on_conflict_do_nothing(index_elements=[ReportDefault.key])
        )
        if inserted.rowcount:
            created.add(key)
    rows = {row.key: row for row in db.query(ReportDefault).with_for_update().populate_existing().all()}
    changes: Dict[str, dict] = {}
    for key, text in texts.items():
        if key in created:
            changes[key] = {"from": None, "to": text}
            continue
        row = rows.get(key)
        before = row.text if row is not None else None
        if text == before:
            continue
        if text is None:
            db.delete(row)
        elif row is None:
            # Deleted by a save that committed between our insert and lock.
            db.add(ReportDefault(key=key, text=text, updated_by_id=user_id))
        else:
            row.text = text
            row.updated_by_id = user_id
        changes[key] = {"from": before, "to": text}
    db.flush()
    return changes
