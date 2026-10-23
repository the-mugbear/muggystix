"""Release notes — what changed in each version, in a line each (2.525.0).

``app/data/release_notes.json`` is the one place they are written; this module
is the one reader (the Reference page's route, the tests).  The notes are for
the people using BlueStick: short, about what they see and do.  WHY a change
was made stays in the maintainers' changelog, which is not published.

Every release has an entry: ``tests/test_release_notes.py`` fails when the
version in ``platform_version.json`` has none or an empty one, and
``scripts/bump-version.py`` adds the empty entry to fill — or renumbers it
when the top entry is still empty, so two bumps in a row never leave a version
that was never released (2.525.1).

``check`` is the whole contract, and the gate runs it on the real file: what it
accepts is served as it is written, so nothing is re-sorted or trimmed per
request.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

NOTES_PATH = Path(__file__).resolve().parents[1] / "data" / "release_notes.json"

#: The kinds a line may be, in the order a release lists them.
KINDS = ("new", "changed", "fixed", "before_upgrading")

#: One line is a line: a note longer than this is a paragraph for the changelog.
MAX_TEXT = 300

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ReleaseNotesError(ValueError):
    """The file does not have the shape this module promises."""


def _key(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def _check_entries(where: str, entries: Any) -> None:
    if not isinstance(entries, list):
        raise ReleaseNotesError(f"{where}: 'entries' must be a list")
    last_kind = 0
    for line, entry in enumerate(entries):
        here = f"{where}, line {line}"
        if not isinstance(entry, dict):
            raise ReleaseNotesError(f"{here}: must be an object with 'kind' and 'text'")
        if entry.get("kind") not in KINDS:
            raise ReleaseNotesError(f"{here}: 'kind' must be one of {', '.join(KINDS)}")
        kind = KINDS.index(entry["kind"])
        if kind < last_kind:
            raise ReleaseNotesError(f"{here}: lines run in kind order ({', '.join(KINDS)})")
        last_kind = kind
        text = entry.get("text")
        if not isinstance(text, str) or not text or len(text) > MAX_TEXT:
            raise ReleaseNotesError(f"{here}: 'text' must be 1–{MAX_TEXT} characters of text")
        if text != text.strip():
            raise ReleaseNotesError(f"{here}: 'text' has spaces at its start or end")


def check(data: Any) -> List[Dict[str, Any]]:
    """The releases, or ReleaseNotesError naming what is wrong.

    Each release names both versions and a date; its lines are in ``KINDS``
    order, each a known kind with trimmed text of at most ``MAX_TEXT``
    characters; no version pair appears twice; releases run newest first by
    both versions and by date.  Only the TOP release may have no lines — the
    one the bump script just added — and the test refuses that too when it is
    the running version.
    """
    releases = data.get("releases") if isinstance(data, dict) else None
    if not isinstance(releases, list):
        raise ReleaseNotesError("'releases' must be a list")
    seen = set()
    for position, release in enumerate(releases):
        where = f"release {position}"
        if not isinstance(release, dict):
            raise ReleaseNotesError(f"{where}: must be an object")
        for field in ("backend", "frontend"):
            if not isinstance(release.get(field), str) or not _VERSION.match(release[field]):
                raise ReleaseNotesError(f"{where}: '{field}' must be a version like 2.525.0")
        if not isinstance(release.get("date"), str) or not _DATE.match(release["date"]):
            raise ReleaseNotesError(f"{where}: 'date' must be YYYY-MM-DD")
        where = f"release {release['backend']} / {release['frontend']}"
        pair = (release["backend"], release["frontend"])
        if pair in seen:
            raise ReleaseNotesError(f"{where} is listed twice")
        seen.add(pair)
        _check_entries(where, release.get("entries"))
        if position and not release["entries"]:
            raise ReleaseNotesError(f"{where}: has no lines — only the top release may be empty")
        if position:
            newer = releases[position - 1]
            if (_key(release["backend"]) > _key(newer["backend"])
                    or _key(release["frontend"]) > _key(newer["frontend"])):
                raise ReleaseNotesError(f"{where}: releases run newest first")
            if release["date"] > newer["date"]:
                raise ReleaseNotesError(f"{where}: dated after the release above it")
    return releases


@lru_cache(maxsize=1)
def _load(mtime: float) -> List[Dict[str, Any]]:
    return check(json.loads(NOTES_PATH.read_text(encoding="utf-8")))


def releases() -> List[Dict[str, Any]]:
    """Every release, newest first, as written (``check`` keeps the file in
    order).  Read again only when the file changes."""
    return _load(NOTES_PATH.stat().st_mtime)
