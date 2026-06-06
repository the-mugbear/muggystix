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
"""
from __future__ import annotations

from typing import List

# Mandatory, ordered.  Editing these is a material prompt change — prepend a
# PROMPT_VERSION_HISTORY entry in agent_prompt_history when you do.
SAFETY_RULES: List[str] = [
    "NEVER run a command without showing it to the user and getting explicit approval.",
    "Before testing each host, perform a sanity check (source IP, reverse DNS, "
    "banner grab) to verify you are reaching the intended target.",
    "If a sanity check fails or looks suspicious, STOP and ask the user for "
    "guidance — do not proceed.",
    "Record every command and its outcome (executed, skipped, or failed) as you go.",
]

_SAFETY_HEADER = "**SAFETY RULES (mandatory — do not skip):**"


def render_safety_rules() -> str:
    """Render the canonical safety rules as a numbered Markdown block.

    Trailing single newline; callers add their own paragraph break.
    """
    lines = [_SAFETY_HEADER]
    lines.extend(f"{i}. {rule}" for i, rule in enumerate(SAFETY_RULES, start=1))
    return "\n".join(lines) + "\n"
