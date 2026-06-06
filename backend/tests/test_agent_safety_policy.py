"""CR5 Refactor #3 — agent safety policy parity.

The mandatory safety rules are authored once in app.services.agent_policy and
rendered into both the live execution prompt and the offline bundle
instructions.  These tests fail if the two surfaces ever diverge again, and if
AGENTS.md (the authoritative detailed guide) drops one of the rule themes.
"""
from __future__ import annotations

from pathlib import Path

from app.services.agent_policy import SAFETY_RULES, render_safety_rules
from app.services.agent_prompt_service import build_execution_instructions
from app.services.bundle_service import _build_offline_instructions


def _live() -> str:
    return build_execution_instructions(
        request=None, plan_id=1, plan_title="P", session_id=2, entry_count=3,
        raw_api_key="k", user_label="u", user_id=1,
    )


def _offline() -> str:
    return _build_offline_instructions(
        request=None, plan_id=1, plan_title="P", session_id=2,
        bundle_id="b", entry_count=3,
    )


def test_both_surfaces_render_the_canonical_block():
    block = render_safety_rules()
    assert block in _live(), "live execution prompt must render the canonical safety block"
    assert block in _offline(), "offline bundle must render the canonical safety block"


def test_every_rule_appears_in_both_surfaces():
    live, offline = _live(), _offline()
    for rule in SAFETY_RULES:
        assert rule in live, f"live prompt missing rule: {rule!r}"
        assert rule in offline, f"offline bundle missing rule: {rule!r}"


def test_agents_md_still_covers_each_safety_theme():
    """AGENTS.md is the detailed guide (prose), not generated — but it must
    still carry every safety layer.  Theme keywords, not verbatim text."""
    candidates = [
        Path(__file__).resolve().parents[1] / "AGENTS.md",   # /app/AGENTS.md (container)
        Path(__file__).resolve().parents[2] / "AGENTS.md",   # repo root (local)
    ]
    agents_md = next((p for p in candidates if p.exists()), None)
    if agents_md is None:
        import pytest
        pytest.skip("AGENTS.md not mounted in this environment")
    text = agents_md.read_text().lower()
    # approval, sanity check, stop-and-ask, and audit/record themes.
    assert "approval" in text
    assert "sanity check" in text
    assert "stop" in text and "ask the user" in text
    assert "audit trail" in text or "recorded" in text
