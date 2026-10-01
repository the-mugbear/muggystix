"""Agent safety policy parity (v2.337.0 unified session; no rails since v2.433.0).

The mandatory safety rules are authored once in ``app.services.agent_policy``
and rendered into the session prompt; the read-back is the session's (its
bounds at start).  These tests fail if the prompt drops a rule or if the
agent guide drops a safety theme.

v2.442.0 — the offline bundle instructions (the second surface the rules were
rendered into) and the execution run's own read-back
(``render_phase_read_back``) went with test plans and execution runs; the
tests that pinned them went with them.
"""
from __future__ import annotations


from app.services.agent_policy import (
    SAFETY_RULES,
    render_safety_rules,
    render_read_back,
)
from app.services.agent_prompt_service import build_session_instructions


def _session() -> str:
    return build_session_instructions(
        request=None, session_id=2, project_id=1, project_name="P",
        purpose="everything", raw_api_key="k", user_label="u", user_id=1,
    )


def test_the_session_prompt_renders_the_canonical_block():
    assert render_safety_rules() in _session(), (
        "the session prompt must render the canonical safety block"
    )


def test_every_rule_appears_in_the_session_prompt():
    session = _session()
    for rule in SAFETY_RULES:
        assert rule in session, f"session prompt missing rule: {rule!r}"


def test_agents_md_still_covers_each_safety_theme():
    from app.services.agents_guide_service import read_agent_guide
    text = read_agent_guide()
    if text is None:
        import pytest
        pytest.skip("the agent guide is not mounted in this environment")
    text = text.lower()
    assert "scope" in text
    assert "working directory" in text
    assert "go-ahead" in text
    assert "audit trail" in text or "recorded" in text


# ---------------------------------------------------------------------------
# Read-back: the session's bounds at start.
# ---------------------------------------------------------------------------

def test_session_prompt_demands_the_session_read_back():
    prompt = _session()
    assert render_read_back("project") in prompt
    assert "FIRST MESSAGE" in prompt
    assert "mandatory" in prompt.lower()


def test_session_read_back_states_project_scope_and_directory():
    """v2.433.0 — the agent may run commands without opening a phase, so the
    session read-back itself names the scope and the working directory."""
    block = render_read_back("project")
    assert "project" in block
    assert "scope" in block
    assert "working directory" in block
    # No rails: nothing about opening a run first, approved tools or approval.
    for retired in ("run nothing against any host", "unapproved", "approve"):
        assert retired not in block


def test_safety_rules_carry_no_rails():
    """v2.433.0 — the approved-tool allowlist, the sanity-check gate and the
    required order are gone; the scope, directory and record rules stay."""
    text = " ".join(SAFETY_RULES).lower()
    for retired in ("approved set", "sanity check", "unapproved", "suggest_tool"):
        assert retired not in text
    assert "every command" in text
    assert "declared scope" in text
    assert "working directory" in text


def test_the_read_back_carries_a_working_directory():
    block = render_read_back("project")
    assert "working directory" in block
    assert "outside" in block


def test_there_is_one_read_back_and_no_run_layer():
    """v2.442.0 — execution runs are gone, and with them the read-back their
    start returned.  The retired ``execution`` key renders the session block,
    never a "hosts this plan covers" recital for a plan that cannot exist."""
    import app.services.agent_policy as policy

    assert not hasattr(policy, "render_phase_read_back")
    assert render_read_back("execution") == render_read_back("project")
    assert "plan" not in render_read_back("execution")


def test_session_read_back_covers_scope_and_domains():
    """Was the recon-run read-back's; any session may scan now (v2.433.1)."""
    block = render_read_back("project")
    assert "CIDRs" in block
    assert "in-scope domains" in block
    assert "does not put the address it resolves to in subnet scope" in block


def test_an_unregistered_phase_gets_the_least_privileged_wording():
    """A phase added without registering here should under-claim: the fallback
    is the project (session) items, the least-privileged set."""
    assert render_read_back("something-new") == render_read_back("project")


def test_read_back_asks_for_restatement_not_recital():
    block = render_read_back("project")
    assert "in your own words" in block
    assert "not a recital" in block


def test_agents_md_carries_the_read_back_for_every_workflow_slice():
    import pytest
    from app.services.agents_guide_service import read_agent_guide, slice_agents_md

    text = read_agent_guide()
    if text is None:
        pytest.skip("the agent guide is not mounted in this environment")

    for workflow in ("testing", "reconnaissance", "assist"):
        sliced = slice_agents_md(text, workflow=workflow)
        assert "Say the rules back before you start" in sliced, (
            f"the {workflow} slice lost the read-back section"
        )
