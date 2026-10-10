"""Per-client MCP connection recipes for a freshly-minted agent key.

One builder for the start and the resume of a session, so a fix to a client
recipe (VS Code's wrapper key, Codex's env-var flag, the sandbox note) lands
everywhere at once.  One project session, one server entry (``bluestick``) and
one key env var.

Every recipe carries the sandbox advice, because any session may run *commands
on the operator's machine*; that boundary is enforced by the client —
BlueStick can record what an agent reports it did, and cannot stop a command
from running.  Saying so plainly is the honest version.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

_SERVER_NAME = "bluestick"
_KEY_ENV_VAR = "BLUESTICK_API_KEY"


class McpClientSetup(BaseModel):
    """How one MCP-capable host connects to this session's /api/v1/mcp endpoint.

    v2.269.0 — this used to be a single `mcp_config` string in VS Code's shape,
    handed to operators on VS Code, Claude Code, AND Cursor alike.  The clients
    do not agree: VS Code's `.vscode/mcp.json` wraps servers under `servers`,
    while Claude Code and Cursor use `mcpServers` — so two of the three named
    hosts silently ignored the server the dialog told the operator to paste.
    The file path differs per client too, which is why `path` is part of the
    payload rather than something the dialog hardcodes.
    """

    id: str
    # Client name as the operator knows it, for the dialog's tab.
    label: str
    # "file"    -> `payload` is JSON to write at `path`
    # "command" -> `payload` is a shell command to run; `path` is empty
    kind: str
    path: str
    payload: str
    # One line under the payload: what to do with it.
    hint: str
    # v2.331.0 — the handoff the recipes used to stop short of: how this client
    # shows "connected", the first prompt to give the agent, and what the answer
    # looks like from the session that was actually minted.  See
    # ``verify_prompt``.
    verify_check: str = ""
    verify_prompt: str = ""
    verify_expected: str = ""


def server_name() -> str:
    return _SERVER_NAME


def key_env_var() -> str:
    return _KEY_ENV_VAR


def _mcp_server_entry(mcp_url: str, raw_key: str) -> Dict[str, Any]:
    return {
        "type": "http",
        "url": mcp_url,
        "headers": {"X-API-Key": raw_key},
    }


def sandbox_note(client_id: str) -> str:
    """Client flags that keep a command-running agent inside its directory.

    Always emitted: any session may run commands on the operator's machine,
    so the client sandbox is the real boundary whatever the session does first.
    The wording is deliberately "your client enforces this": an operator who
    believes the server is enforcing it would grant more than they meant to.
    """
    # One paragraph ("\n\n" opens it; McpConnectPanel renders paragraphs and
    # backtick spans as <code>): launch line, then why.
    common = (
        "Run the client FROM the directory you want the run's output in: that "
        "directory is the sandbox, and anything outside it — other paths, machine "
        "settings — should come back to you as a prompt, not happen quietly."
    )
    if client_id == "codex":
        return (
            "\n\nLaunch with `codex --sandbox workspace-write --ask-for-approval on-request`"
            " so writes stay in the working directory and anything else asks first. "
            + common
        )
    if client_id == "claude_code":
        return (
            "\n\nLaunch plain `claude` in that directory — it defaults to asking before "
            "acting outside it. Do not pass `--dangerously-skip-permissions` for a run "
            "that executes scanners. " + common
        )
    return "\n\n" + common


# ---------------------------------------------------------------------------
# Verification (v2.331.0)
#
# The recipes above end at "the config is installed". Nothing told the operator
# how to find out whether it worked, and the two signals a client offers are
# both misleading on their own: a server can be REGISTERED (``codex mcp list``,
# a saved mcp.json) with a dead key or an untrusted certificate, and ``tools/list``
# succeeds WITHOUT a key by design (the documentation view), so "I can see the
# tools" proves nothing about the credential.  The only check that proves the
# key works end-to-end is an authenticated tool call — ``agent_identity`` —
# and the operator can compare its answer against the project and session this
# dialog just minted.
# ---------------------------------------------------------------------------

# What each client shows for "connected", and which of its checks means what.
_VERIFY_CHECKS = {
    "vscode": (
        "In VS Code, run “MCP: List Servers” from the command palette: {name} "
        "should show as Running, and its tools appear in the Copilot chat tool "
        "picker. Stopped, or an error on start, means it has not connected — the "
        "MCP output channel shows why (certificate first, then the key)."
    ),
    "claude_code": (
        "`claude mcp list` should report {name} as Connected; inside a session, "
        "`/mcp` lists it with its tools. “Failed to connect” is the certificate "
        "or the key, in that order."
    ),
    "codex": (
        "`codex mcp list` only shows that {name} is CONFIGURED. Inside an "
        "interactive `codex` session, `/mcp` shows whether it actually connected "
        "and which tools it loaded — that is the check that counts."
    ),
}


def verify_check(client_id: str) -> str:
    return _VERIFY_CHECKS.get(client_id, "").format(name=server_name())


def verify_prompt() -> str:
    """The first thing to ask the agent, once the client is configured.

    Names the server so a client with several BlueStick servers picks the
    right one, asks for the identity fields the operator can check against
    the dialog, and tells the agent what to do when the tools are missing —
    otherwise a model with no tools answers from general knowledge and the
    operator reads a confident paragraph as a working connection.

    One call only.  It does not ask for the guide: the opening instructions
    say to read the part that is needed, and the whole guide in the first
    turn is most of a context window spent before any work.

    It also forbids the workaround a field report showed: with the
    client unconnected (untrusted certificate), the model read the key out of
    mcp.json and drove the endpoint by hand with curl — on Windows, where
    ``curl`` is an Invoke-WebRequest alias — then reported SSE/session problems
    this server does not have (it answers plain JSON, statelessly).
    """
    name = server_name()
    return (
        f"Using the {name} MCP server, call agent_identity. "
        "Report the project, the session id, my operator role, whether "
        "you can write project data, and when the key expires — exactly as BlueStick "
        f"returned them. If the {name} tools are not available or the "
        "call fails, say so plainly and help me troubleshoot the connection rather "
        "than answering from general knowledge. Do not reach the server yourself "
        "with curl or a script, and do not read the API key out of the config file: "
        "a hand-made request says nothing about whether this client is connected. "
        "The first thing to check is whether this client trusts the server's "
        "certificate."
    )


def verify_expected(expected: Optional[Dict[str, Any]]) -> str:
    """What a correct answer to ``verify_prompt`` contains, from the session
    that was actually minted — so the operator compares against facts, not a
    template.  ``expected`` carries ``project_name`` and ``session_label``
    (e.g. "agent session #12"); either may be absent (the reference page has
    no session), in which case the sentence points at the dialog instead.

    The first sentence is this session's facts and ends at the first ". ":
    the start dialog shows that sentence and folds the rest
    (``McpConnectPanel`` ``CompactVerify``).
    """
    expected = expected or {}
    facts: List[str] = []
    project = expected.get("project_name")
    if project:
        facts.append(f"project “{project}”")
    label = expected.get("session_label")
    if label:
        facts.append(label)
    if not facts:
        facts.append("the project and session id this dialog shows")
    return (
        f"A working connection answers with {', '.join(facts)}. "
        "A different project, a session it cannot name, or “those "
        "tools are not available” means the client is talking to the wrong "
        "server or the key was not accepted — BlueStick marks the session as "
        "connected only after a call like this reaches it."
    )


def build_mcp_clients(
    mcp_url: str,
    raw_key: str,
    *,
    expected: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Connection recipes, one per supported client, as plain dicts.

    Returned as dicts rather than a Pydantic model so the three routers that
    emit this can each keep their own response model without importing one
    another's.  The wrapper key and the file path differ by client, which is why
    a single blob can't serve all three: VS Code's ``.vscode/mcp.json`` wraps
    servers under ``servers`` while Claude Code uses ``mcpServers``, so the one
    original recipe was silently ignored by two of the three clients it was
    handed to.

    ``expected`` (v2.331.0) is the project name and session label the
    verification step should see back — see ``verify_expected``.
    """
    name = server_name()
    env_var = key_env_var()
    entry = {name: _mcp_server_entry(mcp_url, raw_key)}
    prompt = verify_prompt()
    expected_text = verify_expected(expected)
    clients = [
        {
            "id": "vscode",
            "label": "VS Code Copilot",
            "kind": "file",
            "path": ".vscode/mcp.json",
            "payload": json.dumps({"servers": entry}, indent=2),
            "hint": (
                "Save as .vscode/mcp.json in your workspace, then start the server from the "
                "Copilot MCP panel. The file holds a live key — keep it out of version control. "
                + sandbox_note("vscode")
            ),
        },
        {
            "id": "claude_code",
            "label": "Claude Code",
            "kind": "command",
            "path": "",
            "payload": (
                f"claude mcp add --transport http {name} {mcp_url} "
                f'--header "X-API-Key: {raw_key}"'
            ),
            "hint": (
                "Run in your project directory. -s local keeps the key in your own config; "
                "-s project writes .mcp.json into the repo, so do not use it with a live key. "
                + sandbox_note("claude_code")
            ),
        },
        {
            "id": "codex",
            "label": "Codex",
            "kind": "command",
            "path": "",
            "payload": (
                f"read -rs {env_var} && export {env_var}   # paste the key, then Enter\n"
                f"codex mcp add {name} --url {mcp_url} "
                f"--bearer-token-env-var {env_var}"
            ),
            "hint": (
                "Codex keeps the key out of config.toml — it reads the env var at run time. "
                "`read -rs` keeps it out of your shell history too; re-run it in each new shell "
                "rather than writing the key into a profile. "
                + sandbox_note("codex")
            ),
        },
    ]
    # No Cursor recipe (removed v2.275.0): it was the one client whose config
    # shape was never verified against a real install, and nobody here uses it.
    for client in clients:
        client["verify_check"] = verify_check(client["id"])
        client["verify_prompt"] = prompt
        client["verify_expected"] = expected_text
    return clients


def build_session_mcp_clients(
    mcp_url: str, raw_key: str, *, project_name: str, agent_session_id: int
) -> List[McpClientSetup]:
    """The recipes for one agent session — what the start dialog and the
    resume route both return.

    The label the operator checks the agent's answer against must be the id
    the agent will actually report — ``session_id`` on /agent/identity, the
    AgentSession id (the session's only id since v2.449.0).
    """
    return [
        McpClientSetup(**client)
        for client in build_mcp_clients(
            mcp_url,
            raw_key,
            expected={
                "project_name": project_name,
                "session_label": f"agent session #{agent_session_id}",
            },
        )
    ]
