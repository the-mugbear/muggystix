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
        raw_api_key="k", user_label="u", user_id=1,
    )


def test_the_session_prompt_carries_no_stated_purpose(client, test_project):
    """The start dialog asks for no purpose, and one a script still sends
    labels the session row only: it is never put in the agent's prompt."""
    import inspect

    assert "purpose" not in inspect.signature(build_session_instructions).parameters
    assert "purpose" not in _session().lower()
    r = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start",
        json={"purpose": "zebra-label-9431"},
    )
    assert r.status_code in (200, 201), r.text
    assert "zebra-label-9431" not in r.json()["instructions"]


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
    assert render_read_back() in prompt
    assert "FIRST MESSAGE" in prompt
    assert "mandatory" in prompt.lower()


def test_the_mcp_opening_renders_the_same_rules_as_the_prompt():
    """One implementation: an MCP agent never sees the pasted prompt, so the
    opening instructions render the same rules, read-back and key-expiry
    handling from ``agent_policy`` — they were a hand paraphrase that had lost
    rules 2 and 5, "installing software" and the name/address rule."""
    from app.api.v1.endpoints.mcp_assist import _server_instructions
    from app.services.agent_policy import render_key_expiry_guidance

    opening = _server_instructions("https://127.0.0.1/api/v1")
    for rule in SAFETY_RULES:
        assert rule in opening, f"MCP opening missing rule: {rule!r}"
    assert render_safety_rules(over_mcp=True) in opening
    assert render_read_back(over_mcp=True) in opening
    assert render_key_expiry_guidance(over_mcp=True) in opening
    # Named as tools, and plain text: no route, no Markdown.
    assert "agent_identity" in opening and "session_renew" in opening
    assert "**" not in opening and "GET /agent/identity" not in opening
    # Nothing about the tool catalogue or the retired plans.
    for absent in ("list_tools", "test plan"):
        assert absent not in opening


def test_both_doors_read_the_401_where_the_server_puts_it():
    """``recoverable`` is under ``detail``; a revoked key carries no such
    field; a password change is named.  The curl door gets the renewal address
    in full (``renew_path`` is relative to the origin, not to its base URL)."""
    from app.services.agent_policy import render_key_expiry_guidance

    for text in (render_key_expiry_guidance(base_url="https://h/api/v1"),
                 render_key_expiry_guidance(over_mcp=True)):
        assert "detail" in text and "no such field" in text
        assert "operator_credentials_changed" in text
        assert "retry the exact request that failed" in text
    assert "POST https://h/api/v1/agent/session/renew" in render_key_expiry_guidance(
        base_url="https://h/api/v1")
    session = _session()
    assert "/api/v1/agent/session/renew`" in session and "renew_path" not in session


def test_session_read_back_states_project_scope_and_directory():
    """The agent may run commands at once, so the read-back itself names the
    scope and the working directory."""
    block = render_read_back()
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
    block = render_read_back()
    assert "working directory" in block
    assert "outside" in block


def test_there_is_one_read_back_and_no_run_layer():
    """There is one read-back, the session's: it takes no workflow or phase
    (the argument went with the last word of plans and runs), and nothing in
    it speaks of a plan."""
    import inspect

    import app.services.agent_policy as policy

    assert not hasattr(policy, "render_phase_read_back")
    assert list(inspect.signature(render_read_back).parameters) == ["over_mcp"]
    assert "plan" not in render_read_back()


def test_session_read_back_covers_scope_and_domains():
    """Any session may scan, so every read-back states CIDRs and domains.  The
    name/address rule is said once, in safety rule 3, which the read-back
    points at."""
    block = render_read_back()
    assert "CIDRs" in block
    assert "in-scope domains" in block
    assert "rules 3 and 4" in block
    assert "does not put the address it resolves to in scope" in SAFETY_RULES[2]
    assert "does not put the address it resolves to" not in block


def test_the_read_back_reads_the_specifics_first():
    """An agent cannot state CIDRs it has not read: the block names the reads
    and puts the statement before any command or write, not before any call."""
    assert "`GET /agent/identity` and `GET /agent/scopes`" in render_read_back()
    assert "agent_identity and assist_list_scopes" in render_read_back(over_mcp=True)
    for block in (render_read_back(), render_read_back(over_mcp=True)):
        assert "before any command or write" in block
        assert "Before your first tool call" not in block


def test_read_back_asks_for_restatement_not_recital():
    block = render_read_back()
    assert "in your own words" in block
    assert "not a recital" in block


def test_agents_md_carries_the_read_back_in_every_part():
    import pytest
    from app.services.agents_guide_service import read_agent_guide, slice_agents_md

    text = read_agent_guide()
    if text is None:
        pytest.skip("the agent guide is not mounted in this environment")

    for part in ("testing", "reconnaissance", "assist", "remediation"):
        sliced = slice_agents_md(text, part=part)
        assert "Say the rules back before you start" in sliced, (
            f"the {part} part lost the read-back section"
        )


def test_every_report_writing_surface_says_not_enough_to_write_is_an_answer():
    """2.455.0 (the user, 2026-10-03): an agent must know it may say it lacks the
    information to write a section, rather than fill it — proposed report text
    goes into the client report word for word.  The session prompt, the guide
    and both report-text MCP tools say so; none of them may lose it."""
    from app.api.v1.endpoints.mcp_tools import TOOLS
    from app.services.agents_guide_service import read_agent_guide

    session = _session()
    assert "\"Not enough to write this\" is a valid answer" in session
    assert "no guesses, no placeholders" in session

    guide = read_agent_guide() or ""
    assert "Saying \"I don't have enough to write this\" is the right answer" in guide
    assert "do not propose that section" in guide

    assert "NOT ENOUGH TO GO ON IS A VALID ANSWER" in TOOLS["propose_finding_text"]["description"]
    assert "leave out one you cannot support" in TOOLS["propose_finding"]["description"]
