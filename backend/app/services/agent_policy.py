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
