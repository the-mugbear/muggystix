"""The rules an agent is given, written once and rendered to both doors.

An agent starts through one of two doors and sees only that one: the pasted
session prompt (:func:`agent_prompt_service.build_session_instructions`, an
agent that drives the API with curl) or the MCP opening instructions
(``mcp_assist._server_instructions``).  Both render the same blocks from this
module — the safety rules, the read-back, where a curl call's key comes from,
the key-expiry handling and the ask-first rule for configured scanners — so
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
# PROMPT_VERSION in agent_prompt_service when you do.
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
            "Call agent_identity and assist_get_scope (scope_list_subnets and "
            "scope_list_domains when it says a list is truncated)."
        )
    else:
        reads = (
            "Read `GET /agent/identity` and `GET /agent/assist/scope` (with "
            "`GET /agent/scope/subnets` and `/agent/scope/domains` when it says "
            "a list is truncated)."
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

#: What a client is told when it goes looking for OAuth.  The MCP endpoint
#: answers a refused key with a bare 401; some clients (Claude Code) then try
#: OAuth discovery and dynamic client registration, and show their model the
#: LAST answer — so that answer has to say what actually happened.  It cannot
#: say WHY the key was refused (nothing in an OAuth probe identifies the key);
#: the session's page tells the operator.
OAUTH_NOT_USED = (
    "BlueStick does not use OAuth: its MCP endpoint takes an agent key in the "
    "X-API-Key header. Your client asked for OAuth because that endpoint answered "
    "401 — the key it sent was refused (the session was ended, the key expired, or "
    "the operator's password changed). Tell the operator: open this session under "
    "Agents in BlueStick and Resume it (or start a new one) to get a new key, "
    "replace the key in the client's MCP configuration, then restart the client — "
    "a running client keeps the old key."
)


# --- Where a curl call's key comes from ----------------------------------------
# Agent shells start fresh for each command (an `export` is gone by the next
# call) and some client sandboxes refuse a command that expands a variable, so
# agents told to send `X-API-Key: $KEY` asked the operator to quit, export the
# key and restart them.  A curl config file in the working directory survives
# every fresh shell, expands nothing, and works with any curl (`-K` with a
# `header =` line, not the 8.3+ `variable` directive).

KEY_FILE = ".bluestick.curl"


def render_key_file(*, raw_api_key: str = "<your key>", over_mcp: bool = False) -> str:
    """How every curl call carries the key: ``curl -sS -K .bluestick.curl``.

    The curl door is handed the key and writes the file itself.  The MCP door's
    key sits in its client's server entry, which the agent may never see, so it
    asks the operator for the file only when it needs a file-shaped read.
    """
    c = lambda text: _code(text, over_mcp)  # noqa: E731 - a two-line formatter
    line = f'header = "X-API-Key: {raw_api_key}"'
    never = (
        "Never ask the operator to set an environment variable or to restart you "
        "for the key: the file is all curl needs. Never print the file, quote the "
        "key, or put the file in evidence or an upload."
    )
    if over_mcp:
        return (
            f"Every curl call reads the key from {c(KEY_FILE)} in the working "
            f"directory, a curl config file holding one line, {c(line)}: run "
            f"{c(f'curl -sS -K {KEY_FILE} <url>')}. If the file is not there, ask "
            f"the operator to create it (the key is in your client's BlueStick "
            f"server entry). {never}\n"
        )
    return (
        f"Write the key once, now, to {c(KEY_FILE)} in the working directory "
        f"(with your file-write tool; `chmod 600` it where that exists), holding "
        f"exactly this line:\n\n"
        f"```\n{line}\n```\n\n"
        f"Then run every request as {c(f'curl -sS -K {KEY_FILE} <url>')}, adding "
        f"the method, body and {c('-o')} as needed. Do not keep the key in a shell "
        f"variable: your shell may forget it between commands. {never} If you are "
        f"handed a new key (a resumed session), overwrite the file; after "
        f"{c('session/end')}, delete it.\n"
    )


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


# ---------------------------------------------------------------------------
# Configured scanners — ask first
# ---------------------------------------------------------------------------
# Owner decision of 2026-10-10.  The installation's scanner integrations are
# in nothing an agent reads by default: it can see THAT a scanner is
# configured, and is handed its credentials only on a request that names the
# one integration and states the operator agreed.
#
# What the server backs: credentials are absent from every default read, the
# request needs an operator who can write to the project, and every request
# is recorded.  What it cannot: that the operator really said yes — that
# happens in the agent's chat.  So this is an instruction, like the safety
# rules, and nothing here (or anywhere) calls it enforced, approved or gated.
#
# It says nothing about which scanner to use, when, or with what options:
# that is the operator's call and the agent's judgment.

#: What the operator must be told before they answer.  One sentence, used by
#: the rule, by the refusal of a request that does not state agreement, and
#: by nothing else.
SCANNER_CONSENT_MEANING = (
    "saying yes means BlueStick shares that scanner's credentials with you, "
    "the agent, so you can talk to it"
)

#: ``note`` on the list of configured scanners.
SCANNER_LIST_NOTE = (
    "Credentials are not included. Ask the operator before requesting a "
    "scanner's credentials."
)

#: ``note`` on the answer that carries one scanner's credentials.
SCANNER_CREDENTIALS_NOTE = (
    "These credentials are for this scanner only. Do not write them into "
    "notes, evidence, feedback or proposals. This request was recorded."
)

#: The 422 for a request that does not state the operator agreed.
SCANNER_ASK_FIRST_REFUSAL = (
    "Ask the operator first. Ask whether they want you to use this scanner, "
    f"and tell them that {SCANNER_CONSENT_MEANING}. Only after they say yes, "
    'send this request again with {"operator_agreed": true}.'
)


def render_scanner_integrations_rule(*, over_mcp: bool = False) -> str:
    """The ask-first rule for the installation's configured scanners."""
    if over_mcp:
        listing = "list_scanner_integrations"
        request = "request_scanner_credentials"
    else:
        listing = "`GET /agent/assist/scanner-integrations`"
        request = (
            "`POST /agent/scanner-integrations/{integration_id}/credentials` "
            'with `{"operator_agreed": true}`'
        )
    return (
        f"BlueStick may have scanners configured ({listing}: each one's name, "
        "type and address, never its credentials). Before using one, ask the "
        "operator whether they want you to, and tell them that "
        f"{SCANNER_CONSENT_MEANING}. Only after they say yes, request the "
        f"credentials ({request}), for that one scanner; every request is "
        "recorded. Keep the credentials out of notes, evidence, feedback and "
        "proposals.\n"
    )


# ---------------------------------------------------------------------------
# An upload that was cut short — the agent asks first
# ---------------------------------------------------------------------------
# Owner, 2026-10-11: "If an agent attempts to upload a partial, can't it ask
# the agent operator?"  A file that ends before it is complete fails as
# ``truncated_file`` and nothing from it is imported.  Keeping what it holds
# is the operator's answer; the agent asks, then states that they agreed.
# The same shape as the scanner rule above, with the same limit: the server
# never sees the conversation, so nothing here says BlueStick confirmed the
# answer.  What it does is refuse a request that does not state agreement,
# require an operator who can write to the project, and record the request.

#: What the operator must be told before they answer.  One sentence, used by
#: the rule and by the refusal of a request that does not state agreement.
TRUNCATED_UPLOAD_CONSENT_MEANING = (
    "saying yes means BlueStick imports the complete records the file holds "
    "and marks the import partial; saying no means nothing from it is "
    "imported and they can supply the complete file"
)

#: The 422 for a request that does not state the operator agreed.
TRUNCATED_UPLOAD_ASK_FIRST = (
    "Ask the operator first. Tell them the file was cut short and how many "
    "complete records it holds (the upload's error_message says), and ask "
    "whether to import what it holds as a partial import or to supply the "
    f"complete file: {TRUNCATED_UPLOAD_CONSENT_MEANING}. Only after they say "
    'to import it, send this request again with {"operator_agreed": true}.'
)


def render_truncated_upload_rule(*, over_mcp: bool = False) -> str:
    """The ask-first rule for an upload whose file was cut short."""
    if over_mcp:
        status = "get_upload_job"
        request = "keep_truncated_upload"
    else:
        status = "`GET /agent/uploads/{job_id}`"
        request = (
            "`POST /agent/uploads/{job_id}/keep-truncated` "
            'with `{"operator_agreed": true}`'
        )
    return (
        f"An upload can fail with failure_kind truncated_file ({status}): "
        "the file was cut short and nothing from it was imported, though it "
        "holds complete records. Say so to the operator, with the count the "
        "error message gives, and ask whether to import what the file holds "
        f"or to supply the complete file; {TRUNCATED_UPLOAD_CONSENT_MEANING}. "
        f"Only after they say to import it, ask for that ({request}); every "
        "request is recorded.\n"
    )
