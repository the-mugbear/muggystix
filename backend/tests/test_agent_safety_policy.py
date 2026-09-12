"""Agent safety policy parity (rewritten for the v2.337.0 unified session).

The mandatory safety rules are authored once in ``app.services.agent_policy``
and rendered into the session prompt and the offline bundle instructions; the
read-back is two-layer (session bounds at start, phase bounds when a
command-running phase opens).  These tests fail if the surfaces diverge or if
AGENTS.md drops a safety theme.
"""
from __future__ import annotations

from pathlib import Path

from app.services.agent_policy import (
    SAFETY_RULES,
    render_safety_rules,
    render_read_back,
    render_phase_read_back,
)
from app.services.agent_prompt_service import build_session_instructions
from app.services.bundle_service import _build_offline_instructions


def _session() -> str:
    return build_session_instructions(
        request=None, session_id=2, project_id=1, project_name="P",
        purpose="everything", raw_api_key="k", user_label="u", user_id=1,
    )


def _offline() -> str:
    return _build_offline_instructions(
        request=None, plan_id=1, plan_title="P", session_id=2,
        bundle_id="b", entry_count=3,
    )


def test_both_surfaces_render_the_canonical_block():
    block = render_safety_rules()
    assert block in _session(), "the session prompt must render the canonical safety block"
    assert block in _offline(), "offline bundle must render the canonical safety block"


def test_every_rule_appears_in_both_surfaces():
    session, offline = _session(), _offline()
    for rule in SAFETY_RULES:
        assert rule in session, f"session prompt missing rule: {rule!r}"
        assert rule in offline, f"offline bundle missing rule: {rule!r}"


def test_agents_md_still_covers_each_safety_theme():
    candidates = [
        Path(__file__).resolve().parents[1] / "AGENTS.md",
        Path(__file__).resolve().parents[2] / "AGENTS.md",
    ]
    agents_md = next((p for p in candidates if p.exists()), None)
    if agents_md is None:
        import pytest
        pytest.skip("AGENTS.md not mounted in this environment")
    text = agents_md.read_text().lower()
    assert "approval" in text
    assert "sanity check" in text
    assert "stop" in text and "ask the user" in text
    assert "audit trail" in text or "recorded" in text


# ---------------------------------------------------------------------------
# Read-back: session bounds at start, phase bounds when a phase opens.
# ---------------------------------------------------------------------------

def test_session_prompt_demands_the_session_read_back():
    prompt = _session()
    assert render_read_back("project") in prompt
    assert "FIRST MESSAGE" in prompt
    assert "mandatory" in prompt.lower()


def test_session_read_back_states_authority_not_a_working_directory():
    """The session read-back is about project/authority; a working directory
    is a phase fact, stated when a command-running phase opens, not at start."""
    block = render_read_back("project")
    assert "project" in block
    assert "run nothing against any host" in block
    # It does not ask the agent to recite a working directory path — that is a
    # phase fact. (It may still name "written outside the working directory" as
    # a stop-condition; that is a rule, not a path recital.)
    assert "run every tool from" not in block


def test_command_running_phases_carry_a_working_directory_read_back():
    for phase in ("recon", "execution"):
        block = render_read_back(phase)
        assert "working directory" in block
        assert "without asking" in block


def test_phase_read_back_names_the_concrete_bounds():
    """render_phase_read_back lists the phase's actual facts so the agent
    restates THESE, not a template."""
    recon = render_phase_read_back("recon", facts=[
        "the CIDRs you will scan: 10.0.0.0/24",
        "in-scope domains: portal.example.com",
    ])
    assert "10.0.0.0/24" in recon
    assert "portal.example.com" in recon
    assert render_read_back("recon") in recon  # the generic items ride along

    execution = render_phase_read_back("execution", facts=[
        "the 3 host(s) this approved plan covers — by IP: 10.0.0.5",
    ])
    assert "10.0.0.5" in execution


def test_recon_phase_read_back_covers_scope_and_domains():
    recon = render_read_back("recon")
    assert "CIDR" in recon or "scope" in recon
    assert "in-scope domains" in recon
    assert "does not put the address it resolves to in subnet scope" in recon


def test_execution_phase_read_back_names_the_plan_hosts():
    assert "hosts this plan covers" in render_read_back("execution")


def test_an_unregistered_phase_gets_the_least_privileged_wording():
    """A phase added without registering here should under-claim: the fallback
    is the project (session) items, the least-privileged set."""
    assert render_read_back("something-new") == render_read_back("project")


def test_read_back_asks_for_restatement_not_recital():
    block = render_read_back("recon")
    assert "in your own words" in block
    assert "not a recital" in block


def test_agents_md_carries_the_read_back_for_every_workflow_slice():
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
