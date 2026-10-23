"""Release notes — what changed in each version, in a line each (2.525.0).

``app/data/release_notes.json`` is the one place they are written; this module
is the one reader (the Reference page's route, the tests).  The notes are for
the people using BlueStick: short, about what they see and do.  WHY a change
was made stays in the maintainers' changelog, which is not published.

Every release has an entry: ``tests/test_release_notes.py`` fails when the
version in ``platform_version.json`` has none or an empty one, and
``scripts/bump-version.py`` adds the empty entry to fill.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

NOTES_PATH = Path(__file__).resolve().parents[1] / "data" / "release_notes.json"

#: The kinds a line may be, in the order a release shows them.
KINDS = ("new", "changed", "fixed", "before_upgrading")

#: One line is a line: a note longer than this is a paragraph for the changelog.
MAX_TEXT = 300

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ReleaseNotesError(ValueError):
    """The file does not have the shape this module promises."""


def _key(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def check(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The releases, or ReleaseNotesError naming what is wrong.

    Each release names both versions and a date, every line has a known kind
    and some text of at most ``MAX_TEXT`` characters, no version pair appears
    twice, and the releases run newest first by both versions.  An empty
    ``entries`` list is allowed here — the bump script writes one — and
    refused for the CURRENT version by the test.
    """
    releases = data.get("releases")
    if not isinstance(releases, list):
        raise ReleaseNotesError("'releases' must be a list")
    seen = set()
    for position, release in enumerate(releases):
        where = f"release {position}"
        for field in ("backend", "frontend"):
            if not _VERSION.match(str(release.get(field, ""))):
                raise ReleaseNotesError(f"{where}: '{field}' must be a version like 2.525.0")
        if not _DATE.match(str(release.get("date", ""))):
            raise ReleaseNotesError(f"{where}: 'date' must be YYYY-MM-DD")
        pair = (release["backend"], release["frontend"])
        if pair in seen:
            raise ReleaseNotesError(f"{where}: {pair[0]} / {pair[1]} is listed twice")
        seen.add(pair)
        entries = release.get("entries")
        if not isinstance(entries, list):
            raise ReleaseNotesError(f"{where}: 'entries' must be a list")
        for line, entry in enumerate(entries):
            if entry.get("kind") not in KINDS:
                raise ReleaseNotesError(f"{where}, line {line}: 'kind' must be one of {', '.join(KINDS)}")
            text = str(entry.get("text", "")).strip()
            if not text or len(text) > MAX_TEXT:
                raise ReleaseNotesError(f"{where}, line {line}: 'text' must be 1–{MAX_TEXT} characters")
        if position and (
            _key(release["backend"]) > _key(releases[position - 1]["backend"])
            or _key(release["frontend"]) > _key(releases[position - 1]["frontend"])
        ):
            raise ReleaseNotesError(f"{where}: releases run newest first")
    return releases


@lru_cache(maxsize=1)
def _load(mtime: float) -> List[Dict[str, Any]]:
    return check(json.loads(NOTES_PATH.read_text(encoding="utf-8")))


def releases() -> List[Dict[str, Any]]:
    """Every release, newest first, each line ordered by ``KINDS``."""
    out = []
    for release in _load(NOTES_PATH.stat().st_mtime):
        entries = sorted(release["entries"], key=lambda e: KINDS.index(e["kind"]))
        out.append({**release, "entries": [{"kind": e["kind"], "text": e["text"].strip()} for e in entries]})
    return out
