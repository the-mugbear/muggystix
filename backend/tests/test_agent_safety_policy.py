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


# ---------------------------------------------------------------------------
# The read-back (v2.281.0)
#
# BlueStick cannot enforce the guardrails — commands run on the operator's
# machine and the server sees only what the agent reports.  Requiring the agent
# to state the bounds first is the thing it CAN do, so the requirement has to be
# in every workflow's prompt, not just the one that happened to carry the safety
# block.  These tests pin that, and pin the per-workflow wording: a read-back
# that recites a working directory to a workflow that never runs a command is
# how the whole step turns into boilerplate.
# ---------------------------------------------------------------------------

def _prompts() -> dict:
    """One rendered prompt per workflow, built the way the endpoints build them."""
    from app.services.agent_prompt_service import (
        build_assist_instructions,
        build_plan_generation_instructions,
        build_recon_ingest_instructions,
    )

    return {
        "execution": _live(),
        "plan_generation": build_plan_generation_instructions(
            request=None, plan_id=1, plan_title="P", raw_api_key="k",
            user_label="u", user_id=1,
        ),
        "recon": build_recon_ingest_instructions(
            request=None, recon_session_id=1, scope_id=2, scope_name="dmz",
            subnets=["10.0.0.0/24"], raw_api_key="k", user_label="u", user_id=1,
        ),
        "assist": build_assist_instructions(
            request=None, assist_session_id=1, project_id=1, project_name="P",
            raw_api_key="k", user_label="u", user_id=1, purpose="looking at FTP",
        ),
    }


def test_every_workflow_prompt_demands_the_read_back():
    from app.services.agent_policy import render_read_back

    for workflow, prompt in _prompts().items():
        block = render_read_back(workflow)
        assert block in prompt, f"{workflow} prompt is missing its read-back block"
        # And it is stated as a first-message obligation, not a suggestion.
        assert "FIRST MESSAGE" in prompt
        assert "mandatory" in prompt.lower()


def test_the_read_back_is_worded_for_the_work_the_workflow_does():
    """Recon and execution run commands on the machine; plan generation and
    assist only move data. Asking the latter to recite a working directory
    would be reciting something that does not apply."""
    from app.services.agent_policy import render_read_back

    for workflow in ("recon", "execution"):
        block = render_read_back(workflow)
        assert "working directory" in block
        assert "without asking" in block

    for workflow in ("plan_generation", "assist"):
        block = render_read_back(workflow)
        assert "working directory" not in block

    # Recon states the scope it will scan; execution states the hosts in the
    # plan. Neither is interchangeable with the other.
    assert "CIDR" in render_read_back("recon") or "scope" in render_read_back("recon")
    assert "hosts this plan covers" in render_read_back("execution")
    # Plan generation must say out loud that it runs nothing and cannot approve.
    plan_block = render_read_back("plan_generation")
    assert "you run nothing" in plan_block
    assert "cannot approve" in plan_block


def test_an_unregistered_workflow_gets_the_least_privileged_wording():
    """A workflow added without registering here should under-claim, not
    over-claim — reciting "these are the tools I may run" for a surface nobody
    has thought about is the failure that matters."""
    from app.services.agent_policy import render_read_back

    assert render_read_back("something-new") == render_read_back("assist")


def test_read_back_asks_for_restatement_not_recital():
    """A verbatim recital can be produced without having read anything, and
    gives the operator nothing to check against."""
    from app.services.agent_policy import render_read_back

    block = render_read_back("recon")
    assert "in your own words" in block
    assert "not a recital" in block


def test_agents_md_carries_the_read_back_for_every_workflow_slice():
    """The guide is sliced per workflow; a shared rule filed under one
    workflow's tag silently vanishes for the other three."""
    import pytest

    from app.services.agents_guide_service import slice_agents_md

    candidates = [
        Path(__file__).resolve().parents[1] / "AGENTS.md",
        Path(__file__).resolve().parents[2] / "AGENTS.md",
    ]
    agents_md = next((p for p in candidates if p.exists()), None)
    if agents_md is None:
        pytest.skip("AGENTS.md not mounted in this environment")

    text = agents_md.read_text()
    for workflow in ("plan_generation", "execution", "reconnaissance"):
        sliced = slice_agents_md(text, workflow=workflow)
        assert "Say the rules back before you start" in sliced, (
            f"the {workflow} slice lost the read-back section"
        )
