"""The rules an agent is given, written once and rendered to both doors.

An agent starts through one of two doors and sees only that one: the pasted
session prompt (:func:`agent_prompt_service.build_session_instructions`, an
agent that drives the API with curl) or the MCP opening instructions
(``mcp_assist._server_instructions``).  Both render the same three blocks from
this module — the safety rules, the read-back and the key-expiry handling — so
neither can drift from the other.  ``over_mcp`` changes only how a call is
named (a tool instead of a route) and drops the Markdown.
``test_agent_safety_policy`` asserts both doors carry these exact rules.

This is the terse skeleton.  The how-to lives in the agent guide.

**What these rules are and are not.**  They are instructions to an agent,
which BlueStick cannot enforce: the commands run on the operator's machine,
and the server only ever sees what the agent *reports*.  The working-directory
boundary is real only where the client's sandbox enforces it (Codex
``--sandbox workspace-write``, Claude Code's permission prompts), which the
session-start dialog hands the operator alongside the key.  What BlueStick
adds is the record: every reported command lands in an audit trail a human
reads.  What the rules protect is the client and the operator: the declared
scope, the working directory, the operator's machine, and a record of every
command.
"""
from __future__ import annotations

from typing import List

# Mandatory, ordered.  Editing these is a material prompt change — bump
# PROMPT_VERSION and rewrite PROMPT_CHANGES in agent_prompt_service when you do.
SAFETY_RULES: List[str] = [
    "Show the operator every command before you run it.",
    "The operator drives: do what they ask, and propose next steps rather than "
    "taking them unasked.",
    "Stay inside the project's declared scope. A target outside it — an address "
    "outside the scope's ranges, or a name no declared domain covers — needs the "
    "operator's explicit go-ahead first. A name being in scope does not put the "
    "address it resolves to in scope.",
    "Write output into the working directory you were started in. Reading or "
    "writing outside it, installing software, or changing machine settings or "
    "credentials needs the operator's explicit go-ahead first.",
    "Record every command and its outcome (executed, skipped, or failed) as you "
    "go, verbatim and including where its output was written, and upload "
    "scanner output so it lands in the inventory.",
]

_SAFETY_TITLE = "SAFETY RULES (mandatory):"


def _title(text: str, over_mcp: bool) -> str:
    return text if over_mcp else f"**{text}**"


def _code(text: str, over_mcp: bool) -> str:
    return text if over_mcp else f"`{text}`"


def render_safety_rules(*, over_mcp: bool = False) -> str:
    """The safety rules as a numbered block.

    Trailing single newline; callers add their own paragraph break.
    """
    lines = [_title(_SAFETY_TITLE, over_mcp)]
    lines.extend(f"{i}. {rule}" for i, rule in enumerate(SAFETY_RULES, start=1))
    return "\n".join(lines) + "\n"


# --- The read-back -----------------------------------------------------------
# BlueStick cannot enforce any of the rules above: the commands run on the
# operator's machine and the server sees only what the agent reports.  What it
# CAN do is ask the agent to say its bounds out loud, to the operator, before
# it starts — the one moment a human sees the agent's *understanding* of the
# scope and working directory rather than its output, when a misunderstanding
# is cheap to correct.  Deliberately "in your own words": restating requires
# resolving the rules against *this* session's project, scope and directory,
# which is exactly the part that can be wrong.  The agent is told to read the
# specifics first: it cannot state CIDRs it has not read.

_READ_BACK_TITLE = "FIRST MESSAGE — state your bounds back to the operator (mandatory)."

_READ_BACK_ITEMS: List[str] = [
    "which project you are working in, and as whom — the operator whose "
    "permissions this session carries",
    "the scope you will work within — the actual CIDRs and, when any are "
    "declared, the in-scope domains (saying which are exact names and which "
    "include subdomains). Ask the operator if the project declares none, or if "
    "their task reaches beyond it",
    "the working directory your commands will run from and write into",
    "what you will ask about before acting: anything outside the scope or "
    "outside the working directory, and any change to their machine (rules 3 "
    "and 4)",
]


def render_read_back(*, over_mcp: bool = False) -> str:
    """The mandatory "say your bounds back" block."""
    if over_mcp:
        reads = (
            "Call agent_identity and assist_list_scopes (scope_list_subnets and "
            "scope_list_domains for each scope)."
        )
    else:
        reads = (
            "Read `GET /agent/identity` and `GET /agent/scopes` (with each "
            "scope's `/subnets` and `/domains`)."
        )
    lines = [
        f"{_title(_READ_BACK_TITLE, over_mcp)} {reads} Then, before any command "
        "or write, tell the operator — in your own words, specific to this "
        "session, not a recital of this text:",
    ]
    lines.extend(f"- {item}" for item in _READ_BACK_ITEMS)
    lines.append(
        "Keep it to a few lines, then start. You are not asking permission to begin; "
        "you are giving them the chance to say \"that's the wrong scope\" before you "
        "act."
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Key expiry
# ---------------------------------------------------------------------------

def render_key_expiry_guidance(*, base_url: str = "<base>", over_mcp: bool = False) -> str:
    """How to survive your own credential expiring mid-job.

    This exists for one specific, expensive failure: the agent launches a
    long-running scanner, blocks for hours, its key lapses while it waits, and
    it only finds out when it tries to upload — with the scanning already done.
    An agent that treats a 401 as terminal there throws that work away.

    The two things that matter are *don't discard output* and *retry the same
    request*.  The 401's fields are under ``detail`` (``deps._expired_key_detail``,
    ``deps._CREDENTIALS_CHANGED_DETAIL``); a revoked or unknown key answers a
    plain message with no ``recoverable`` at all.

    ``base_url`` is the ``…/api/v1`` the curl agent was given; the MCP agent
    names the tools instead.
    """
    if over_mcp:
        identity, renew = "agent_identity", "session_renew"
    else:
        identity = "`GET /agent/identity`"
        renew = f"`POST {base_url}/agent/session/renew`"
    c = lambda text: _code(text, over_mcp)  # noqa: E731 - a two-line formatter
    return (
        f"{identity} gives {c('key_expires_at')}. Before anything that will run "
        f"for hours, renew: {renew} (same key, later expiry; it also works after "
        f"the key has expired). On a 401 read {c('detail')} in the body. "
        f"{c('recoverable: true')}: renew with the same key, then retry the exact "
        f"request that failed. {c('recoverable: false')} (the session ended or "
        f"passed its lifetime, or {c('operator_credentials_changed')}), or no such "
        "field (the key was revoked): save what you are holding to a file in the "
        "working directory and tell the operator, who resumes the session or "
        "starts a new one. Never re-run a scan or a command, or discard output "
        "you are holding, because of a 401.\n"
    )
