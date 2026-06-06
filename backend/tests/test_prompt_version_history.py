"""Guards for the structured prompt-version history.

The ~6.6 KB changelog that used to be a trailing comment on PROMPT_VERSION
now lives as data in agent_prompt_history.  PROMPT_VERSION is DERIVED from
the newest entry, so these tests lock in that the version and its changelog
can't drift, and that the history stays well-formed.
"""
from __future__ import annotations

import pytest

from app.services.agent_prompt_history import PROMPT_VERSION_HISTORY
from app.services.agent_prompt_service import PROMPT_VERSION


def _semver(v: str):
    return tuple(int(part) for part in v.split("."))


def test_prompt_version_is_newest_history_entry():
    assert PROMPT_VERSION == PROMPT_VERSION_HISTORY[0]["version"]


def test_history_is_nonempty_and_well_formed():
    assert PROMPT_VERSION_HISTORY
    for entry in PROMPT_VERSION_HISTORY:
        assert set(entry) >= {"version", "app_version", "summary"}
        assert entry["summary"].strip(), f"empty summary for {entry['version']}"
        # version + app_version are dotted numeric strings
        _semver(entry["version"])
        _semver(entry["app_version"])


def test_history_versions_unique_and_strictly_descending():
    versions = [e["version"] for e in PROMPT_VERSION_HISTORY]
    assert len(set(versions)) == len(versions), "duplicate prompt versions"
    parsed = [_semver(v) for v in versions]
    assert parsed == sorted(parsed, reverse=True), (
        "PROMPT_VERSION_HISTORY must be newest-first (strictly descending)"
    )
