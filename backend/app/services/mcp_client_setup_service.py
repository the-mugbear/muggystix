"""Per-client MCP connection recipes for a freshly-minted agent key.

Extracted from ``assist.py`` in v2.279.0 and parameterised by workflow, because
MCP stopped being assist-only: a recon or execution session mints a key with the
same shape and the operator has the same "how do I point my client at this"
problem.  Keeping one builder means a fix to a client recipe (VS Code's wrapper
key, Codex's env-var flag, the self-signed-cert note) lands everywhere at once —
the divergence this replaces is the reason two of the three original recipes
silently didn't work.

Two things vary per workflow:

* **The server name and key env var.**  Distinct per workflow so an operator who
  connects a recon session and a plan session to the same client ends up with
  two servers, not one overwriting the other.
* **The sandbox advice.**  Recon and execution run *commands on the operator's
  machine*; assist and plan generation do not.  For those two the recipe carries
  the client flags that keep the agent inside its working directory, because
  that boundary is enforced by the client — BlueStick can record what an agent
  claims it did, and cannot stop a command from running.  Saying so plainly is
  the honest version; implying the server sandboxes anything would be worse than
  saying nothing.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# v2.337.0 — one project session, one server entry.  The per-workflow server
# names (bluestick-recon / -plan / -exec / -assist) are gone: an operator who
# used to connect four servers now connects one that does everything.  The
# ``workflow`` argument is accepted for call-site compatibility and ignored.
_SERVER_NAME = "bluestick"
_KEY_ENV_VAR = "BLUESTICK_API_KEY"


def server_name(workflow: str = "project") -> str:
    return _SERVER_NAME


def key_env_var(workflow: str = "project") -> str:
    return _KEY_ENV_VAR


def _mcp_server_entry(mcp_url: str, raw_key: str) -> Dict[str, Any]:
    return {
        "type": "http",
        "url": mcp_url,
        "headers": {"X-API-Key": raw_key},
    }


# BlueStick is self-hosted on a private address, so its certificate is
# self-signed and always will be — "get one from a public CA" is not an option
# for something that only ever listens on 127.0.0.1 or a LAN address. Every MCP
# client therefore refuses the connection until told to trust THIS certificate,
# and the way to tell it differs by client:
#
#   * VS Code and Claude Code are Node/Electron. Node ignores the OS trust
#     store, so installing the cert system-wide does nothing; NODE_EXTRA_CA_CERTS
#     takes a single PEM file.
#   * Codex is a Rust binary built against native-tls (OpenSSL). It reads
#     SSL_CERT_DIR — a directory of hash-named symlinks, not a file.
#
# v2.282.0 told Codex operators to export NODE_EXTRA_CA_CERTS, which nothing in
# a Rust binary reads, and then concluded there was "no supported way to pin" and
# they needed a CA-signed certificate. Both halves were wrong. Verified against
# codex 0.147.0: SSL_CERT_FILE alone does NOT take effect, SSL_CERT_DIR does, and
# a client pinned this way still validates public hosts normally — so this ADDS
# trust rather than replacing it, unlike NODE_TLS_REJECT_UNAUTHORIZED=0.
#
# Both variables are read at process START. That is the failure operators
# actually hit: exporting them inside a running client changes nothing, which
# reads as "the pin doesn't work".
def tls_note(mcp_url: str, client_id: str = "vscode") -> str:
    cert_url = mcp_url.rsplit("/mcp", 1)[0] + "/references/tls-certificate"
    # v2.331.0 — the exports are the step operators miss. The script runs in a
    # child shell and cannot set variables for the shell that launched it, so
    # "run it, then restart" leaves the client with no trust unless the exports
    # went into a profile (or the client was launched from a shell that has
    # them). Say so here, where the config is copied.
    common = (
        " Run ./scripts/trust-cert.sh on the machine running the client: it "
        "installs the certificate and PRINTS two exports, which it cannot apply "
        "to your shell for you. Add them to your shell profile, then relaunch "
        "the client from a new shell — both are read only at client start. "
        f"Remote host? Fetch the cert first: curl -sk {cert_url} -o bluestick.pem"
    )
    if client_id == "codex":
        return (
            "Self-signed cert? Codex refuses it until pinned. Codex is a Rust "
            "binary: NODE_EXTRA_CA_CERTS does nothing for it, and SSL_CERT_FILE "
            "does not take effect either (tested on 0.147.0) — it reads "
            "SSL_CERT_DIR, a directory of hash-named symlinks."
            + common
        )
    return (
        "Self-signed cert? Node-based clients refuse it — Node ignores the OS "
        "trust store, so trusting it system-wide won't help. Export "
        "NODE_EXTRA_CA_CERTS=/path/to/bluestick.pem, which trusts this one "
        "deployment and leaves verification on everywhere else."
        + common
    )


def sandbox_note(workflow: str, client_id: str) -> str:
    """Client flags that keep a command-running agent inside its directory.

    v2.337.0 — always emitted: a single session can open a reconnaissance or
    execution run that shells out on the operator's machine, so the client
    sandbox is the real boundary regardless of what the session does first.
    The wording is deliberately "your client enforces this": an operator who
    believes the server is enforcing it would grant more than they meant to.
    """
    common = (
        " Run the client FROM the directory you want the run's output in: that "
        "directory is the sandbox, and anything outside it — other paths, machine "
        "settings — should come back to you as a prompt, not happen quietly."
    )
    if client_id == "codex":
        return (
            " Launch with `codex --sandbox workspace-write --ask-for-approval on-request`"
            " so writes stay in the working directory and anything else asks first."
            + common
        )
    if client_id == "claude_code":
        return (
            " Launch plain `claude` in that directory — it defaults to asking before "
            "acting outside it. Do not pass --dangerously-skip-permissions for a run "
            "that executes scanners." + common
        )
    return common


# ---------------------------------------------------------------------------
# Verification (v2.331.0)
#
# The recipes above end at "the config is installed". Nothing told the operator
# how to find out whether it worked, and the two signals a client offers are
# both misleading on their own: a server can be REGISTERED (``codex mcp list``,
# a saved mcp.json) with a dead key or an untrusted certificate, and ``tools/list``
# succeeds WITHOUT a key by design (the documentation view), so "I can see the
# tools" proves nothing about the credential.  The only check that proves the
# key works end-to-end is an authenticated tool call — ``agent_identity`` is
# the one every workflow has — and the operator can compare its answer against
# the project and session this dialog just minted.
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


def verify_check(workflow: str, client_id: str) -> str:
    return _VERIFY_CHECKS.get(client_id, "").format(name=server_name(workflow))


def verify_prompt(workflow: str) -> str:
    """The first thing to ask the agent, once the client is configured.

    Names the server so a client with several BlueStick servers picks the
    right one, asks for the identity fields the operator can check against
    the dialog, and tells the agent what to do when the tools are missing —
    otherwise a model with no tools answers from general knowledge and the
    operator reads a confident paragraph as a working connection.
    """
    name = server_name(workflow)
    return (
        f"Using the {name} MCP server, call agent_identity and then read_agent_guide. "
        "Report the project, the session id, the workflow, my operator role, whether "
        "you can write project data, and when the key expires — exactly as BlueStick "
        "returned them. Then, from the guide, summarise in a few lines what you can "
        f"help me do in this session. If the {name} tools are not available or the "
        "call fails, say so plainly and help me troubleshoot the connection rather "
        "than answering from general knowledge."
    )


def verify_expected(workflow: str, expected: Optional[Dict[str, Any]]) -> str:
    """What a correct answer to ``verify_prompt`` contains, from the session
    that was actually minted — so the operator compares against facts, not a
    template.  ``expected`` carries ``project_name`` and ``session_label``
    (e.g. "assist session #12"); either may be absent (the reference page has
    no session), in which case the sentence points at the dialog instead.
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
        f"A working connection answers with {', '.join(facts)} and workflow "
        f"“{workflow}”. A different project, a session it cannot name, or “those "
        "tools are not available” means the client is talking to the wrong "
        "server or the key was not accepted — BlueStick marks the session as "
        "connected only after a call like this reaches it."
    )


def build_mcp_clients(
    mcp_url: str,
    raw_key: str,
    *,
    workflow: str = "project",
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
    name = server_name(workflow)
    env_var = key_env_var(workflow)
    entry = {name: _mcp_server_entry(mcp_url, raw_key)}
    prompt = verify_prompt(workflow)
    expected_text = verify_expected(workflow, expected)
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
                + tls_note(mcp_url, "vscode")
                + sandbox_note(workflow, "vscode")
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
                + tls_note(mcp_url, "claude_code")
                + sandbox_note(workflow, "claude_code")
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
                + tls_note(mcp_url, "codex")
                + sandbox_note(workflow, "codex")
            ),
        },
    ]
    # No Cursor recipe (removed v2.275.0): it was the one client whose config
    # shape was never verified against a real install, and nobody here uses it.
    for client in clients:
        client["verify_check"] = verify_check(workflow, client["id"])
        client["verify_prompt"] = prompt
        client["verify_expected"] = expected_text
    return clients
