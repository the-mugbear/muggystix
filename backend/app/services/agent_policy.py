"""Single source of truth for the agent execution safety policy (terse form).

The mandatory approval / sanity-check / result-recording rules are handed to
agents two ways in code — the live execution prompt
(:func:`agent_prompt_service.build_execution_instructions`) and the offline
bundle instructions (:func:`bundle_service._build_offline_instructions`).
Before this module each builder carried its own copy of the rule block, and
they had already drifted (rule 2 said "verify you are reaching the intended
target" live vs "source IP, reverse DNS, banner grab" offline).

Author the rules once here; both builders render :func:`render_safety_rules`.
``test_agent_safety_policy`` is a golden parity test asserting both surfaces
emit these exact rules, so they can't diverge again.

This is the *terse skeleton* the prompt carries.  The authoritative, detailed
protocol (the three safety layers) lives in AGENTS.md — the guide — by design
(see the prompt-vs-guide split).  The parity test also checks AGENTS.md still
covers each rule's theme so a safety layer can't silently drop out of the
guide either.

**What these rules are and are not.**  They are instructions to an agent, which
BlueStick cannot enforce: the commands run on the operator's machine, and the
server only ever sees what the agent *reports*.  The working-directory boundary
is real only where it is enforced — the client's sandbox (Codex
``--sandbox workspace-write``, Claude Code's permission prompts), which the
session-start dialog now hands the operator alongside the key.  What BlueStick
adds is the record: the approved-tool set is a table it owns, the inventory is
its own data, and every reported command lands in an audit trail a human reads.
"""
from __future__ import annotations

from typing import List

# Mandatory, ordered.  Editing these is a material prompt change — prepend a
# PROMPT_VERSION_HISTORY entry in agent_prompt_history when you do.
#
# v2.279.0 replaced "approve every command" with a bounded exception.  Blanket
# approval was the safe-sounding rule that trained operators to click through:
# fifty prompts for fifty in-policy nmap runs, and the one command that was
# genuinely out of bounds arrived looking exactly like the other forty-nine.
# Naming the bounds — approved tool, host already in inventory, output in the
# working directory — is what makes the *remaining* prompts mean something.
# Everything outside them still stops and asks, and the command is shown either
# way.
SAFETY_RULES: List[str] = [
    "Show the user every command before you run it — including the one you are "
    "about to run without asking.",
    "You may run a command WITHOUT waiting for approval only when all three hold: "
    "the tool is in BlueStick's approved set, the target is a host already in this "
    "project's inventory, and every file it writes lands in the working directory "
    "you were started in.",
    "Outside those bounds — an unapproved tool, a target not in the inventory, "
    "reading or writing outside the working directory, or changing machine "
    "settings, installed software, or credentials — STOP and get explicit "
    "approval first.",
    "If you need a tool that is not approved, ask for it (suggest_tool, or tell "
    "the user) rather than substituting one that is.",
    "Before testing each host, perform a sanity check (source IP, reverse DNS, "
    "banner grab) to verify you are reaching the intended target.",
    "If a sanity check fails or looks suspicious, STOP and ask the user for "
    "guidance — do not proceed.",
    "Record every command and its outcome (executed, skipped, or failed) as you "
    "go, verbatim and including where its output was written.",
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
# CAN do is require the agent to say the rules out loud, to the operator, before
# it starts — which is worth more than it sounds:
#
#   * It is the one moment a human sees the agent's *understanding* of the
#     bounds rather than its output, and a misunderstanding is cheap to correct
#     there and expensive to correct after a scan has run against the wrong
#     range.
#   * It makes the agent's own words the record.  An agent that stated "I will
#     write output to ./bluestick-acme-recon-12 and ask before anything else"
#     and then wrote to /etc has visibly contradicted itself, which is a far
#     easier thing for an operator to notice than an unexpected file.
#   * It surfaces a stale or wrong session: if the agent reads back the wrong
#     scope, wrong project, or a tool list that does not match what the operator
#     expects, they find out in the first message instead of the last.
#
# Deliberately "in your own words": a verbatim recital is something a model can
# produce without having read it, and it also gives the operator nothing to
# check against. Restating requires resolving the rules against *this* session's
# directory, scope and tool set — which is exactly the part that can be wrong.

_READ_BACK_HEADER = (
    "**FIRST MESSAGE — state the ground rules back to the operator (mandatory):**"
)

# Per-workflow: the four things the operator needs to hear before work starts.
# Recon and execution run commands on the machine; plan generation and assist
# only read and write data, so asking them to recite a working directory would
# be reciting something that does not apply — and a rule that obviously does not
# apply is how a read-back turns into boilerplate nobody reads.
_READ_BACK_ITEMS = {
    "execution": [
        "the working directory every command will run from and write into",
        "which tools you may run without asking, and that anything else stops for approval",
        "which hosts this plan covers — by IP, not by count",
        "what you will always stop and ask about (unapproved tool, host not in the "
        "inventory, anything written outside that directory, changes to their machine)",
    ],
    "recon": [
        "the working directory every command will run from and write into",
        "which tools you may run without asking, and that anything else stops for approval",
        "the scope you will scan — the actual CIDRs, and that you will not touch "
        "anything outside them",
        "what you will always stop and ask about (unapproved tool, target outside the "
        "scope, anything written outside that directory, changes to their machine)",
    ],
    "plan_generation": [
        "that you will read this project's hosts and findings and write a DRAFT plan — "
        "you run nothing",
        "which tools you may propose tests with, and that a tool outside that set has "
        "to be requested, not substituted",
        "which hosts you are planning against",
        "that the plan goes to them for approval, and that you cannot approve it",
    ],
    "assist": [
        "which project you are reading, and that you will not touch another",
        "what you may write, if anything (notes, review status, hostname/OS), and "
        "which hosts that is limited to",
        "that you record observations and never run anything against a host",
        "what you will stop and ask about before writing",
    ],
}


def render_read_back(workflow: str) -> str:
    """The mandatory "say the rules back" block for *workflow*.

    Falls back to the assist items for an unknown workflow — the least-privileged
    set, so a new workflow that forgets to register here under-claims rather than
    over-claims.
    """
    items = _READ_BACK_ITEMS.get(workflow, _READ_BACK_ITEMS["assist"])
    lines = [
        _READ_BACK_HEADER,
        "Before your first tool call or command, tell the operator — in your own "
        "words, specific to this session, not a recital of this text:",
    ]
    lines.extend(f"- {item}" for item in items)
    lines.append(
        "Keep it to a few lines, then start. You are not asking permission to begin; "
        "you are giving them the chance to say \"that's the wrong scope\" before you "
        "act, which is the only chance either of you gets."
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
