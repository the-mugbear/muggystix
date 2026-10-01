"""Single source of truth for the agent safety rules (terse form).

The rules are handed to agents in the session prompt
(:func:`agent_prompt_service.build_session_instructions`), which renders
:func:`render_safety_rules`; ``test_agent_safety_policy`` asserts the prompt
emits these exact rules.  (The second surface, the offline bundle
instructions, went with test plans in v2.442.0.)

This is the *terse skeleton* the prompt carries.  The detailed how-to lives in
the agent guide by design (see the prompt-vs-guide split).

**What these rules are and are not.**  They are instructions to an agent,
which BlueStick cannot enforce: the commands run on the operator's machine,
and the server only ever sees what the agent *reports*.  The working-directory
boundary is real only where the client's sandbox enforces it (Codex
``--sandbox workspace-write``, Claude Code's permission prompts), which the
session-start dialog hands the operator alongside the key.  What BlueStick
adds is the record: every reported command lands in an audit trail a human
reads.

**v2.433.0 — no rails.**  The rules used to carry an approved-tool allowlist
(run without asking only an "approved" tool against an inventory host), a
mandatory per-host sanity check, and a fixed recon → plan → human approval →
execution order.  The operator drives their agent now, so those are gone —
and since v2.442.0 so are plans and execution runs themselves: the agent
proposes tests on hosts and records what it ran as evidence.  What stays protects the client and the
operator: the declared scope, the working directory, the operator's machine,
and a record of every command.
"""
from __future__ import annotations

from typing import List

# Mandatory, ordered.  Editing these is a material prompt change — prepend a
# PROMPT_VERSION_HISTORY entry in agent_prompt_history when you do.
SAFETY_RULES: List[str] = [
    "Show the operator every command before you run it.",
    "The operator drives: do what they ask within their project role, and "
    "propose next steps rather than taking them unasked.",
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

_SAFETY_HEADER = "**SAFETY RULES (mandatory — do not skip):**"


def render_safety_rules() -> str:
    """Render the canonical safety rules as a numbered Markdown block.

    Trailing single newline; callers add their own paragraph break.
    """
    lines = [_SAFETY_HEADER]
    lines.extend(f"{i}. {rule}" for i, rule in enumerate(SAFETY_RULES, start=1))
    return "\n".join(lines) + "\n"


# --- The read-back (v2.281.0) ------------------------------------------------
# BlueStick cannot enforce any of the rules above: the commands run on the
# operator's machine and the server sees only what the agent reports.  What it
# CAN do is ask the agent to say its bounds out loud, to the operator, before
# it starts — the one moment a human sees the agent's *understanding* of the
# scope and working directory rather than its output, when a misunderstanding
# is cheap to correct.  Deliberately "in your own words": restating requires
# resolving the rules against *this* session's project, scope and directory,
# which is exactly the part that can be wrong.

_READ_BACK_HEADER = (
    "**FIRST MESSAGE — state your bounds back to the operator (mandatory):**"
)

# One layer since v2.442.0: the session's bounds at start.  (An execution
# run's start carried a second read-back — that plan's hosts — until runs
# were removed.)
_READ_BACK_ITEMS = {
    "project": [
        "which project you are working in, and as whom — the operator whose "
        "permissions this session carries",
        # v2.433.1 — folded in from the retired recon-run read-back: any
        # session may scan, so the domain rule is stated for every session.
        "the scope you will work within — the actual CIDRs and, when any are "
        "declared, the in-scope domains (saying which are exact names and which "
        "include subdomains); a name being in scope does not put the address it "
        "resolves to in subnet scope. Ask the operator if the project declares "
        "none, or if their task reaches beyond it",
        "the working directory your commands will run from and write into",
        "what you will ask about before acting (a target outside the scope — an "
        "address outside the CIDRs or a name no declared domain covers — "
        "anything outside the working directory, changes to their machine)",
    ],
}


def render_read_back(workflow: str = "project") -> str:
    """The mandatory "say your bounds back" block of the session prompt.

    ``workflow`` is kept for callers that pass the session's; every value
    renders the ``project`` items (the recon-run block went in v2.433.1 and the
    execution-run block in v2.442.0, each with its runs).
    """
    items = _READ_BACK_ITEMS.get(workflow, _READ_BACK_ITEMS["project"])
    lines = [
        _READ_BACK_HEADER,
        "Before your first tool call or command, tell the operator — in your own "
        "words, specific to this session, not a recital of this text:",
    ]
    lines.extend(f"- {item}" for item in items)
    lines.append(
        "Keep it to a few lines, then start. You are not asking permission to begin; "
        "you are giving them the chance to say \"that's the wrong scope\" before you "
        "act."
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Key expiry (v2.304.0)
# ---------------------------------------------------------------------------

def render_key_expiry_guidance() -> str:
    """How to survive your own credential expiring mid-job.

    This exists for one specific, expensive failure: the agent launches a
    long-running scanner, blocks for hours, its key lapses while it waits, and
    it only finds out when it tries to upload — with the scanning already done.
    An agent that treats a 401 as terminal there throws that work away.

    Kept short and imperative. The two things that matter are *don't discard
    output* and *retry the same request*; everything else is detail the agent
    can read off the 401 body.
    """
    return (
        "### If your key expires\n\n"
        "Agent keys are short-lived, and a long scan can outlast one. Two rules:\n\n"
        "1. **Before starting anything that will run for hours**, check "
        "`key_expires_at` from `GET <base>/agent/identity`. If your key would "
        "lapse during it, POST to `renew_path` (also on that response) first. "
        "This is the cheap path.\n"
        "2. **If you get a 401 anyway** — which is the normal outcome when a "
        "scan runs long, because you cannot make requests while blocked — read "
        "the response body. `recoverable: true` means your session is still "
        "alive: POST to `renew_path` with the **same key**, then **retry the "
        "exact request that failed**.\n\n"
        "**Never re-run a scan or command because of a 401, and never discard "
        "output you are holding.** Renewal keeps the same key, so nothing needs "
        "re-bootstrapping — the request that failed will simply work. If the "
        "body says `recoverable: false`, save what you have to a file in your "
        "working directory and tell the operator you need a new session.\n"
    )
