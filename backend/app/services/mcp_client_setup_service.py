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
from typing import Any, Dict, List

# Workflow keys match AgentSessionWorkflow values.
_SERVER_NAMES = {
    "assist": "bluestick-assist",
    "recon": "bluestick-recon",
    "plan_generation": "bluestick-plan",
    "execution": "bluestick-exec",
}
_KEY_ENV_VARS = {
    "assist": "BLUESTICK_ASSIST_KEY",
    "recon": "BLUESTICK_RECON_KEY",
    "plan_generation": "BLUESTICK_PLAN_KEY",
    "execution": "BLUESTICK_EXEC_KEY",
}
# The workflows whose agents run local commands, and therefore need the client
# sandbox pointed at the working directory.
_LOCAL_EXECUTION_WORKFLOWS = frozenset({"recon", "execution"})


def server_name(workflow: str) -> str:
    return _SERVER_NAMES.get(workflow, "bluestick")


def key_env_var(workflow: str) -> str:
    return _KEY_ENV_VARS.get(workflow, "BLUESTICK_API_KEY")


def _mcp_server_entry(mcp_url: str, raw_key: str) -> Dict[str, Any]:
    return {
        "type": "http",
        "url": mcp_url,
        "headers": {"X-API-Key": raw_key},
    }


# Deployments default to a self-signed certificate, which every client here
# rejects before any request is made — but the fix is NOT the same for all of
# them, and pretending it was is how the Codex recipe shipped advertising a
# workaround that does nothing (v2.282.0).
#
# VS Code and Claude Code are Node/Electron: they ignore the OS trust store, so
# "trust it in Keychain" doesn't help, and NODE_EXTRA_CA_CERTS pins this one
# deployment while leaving verification ON for every other host the process
# talks to (unlike NODE_TLS_REJECT_UNAUTHORIZED=0).
#
# Codex is a Rust binary. NODE_EXTRA_CA_CERTS is not read by anything in it, so
# the note was advice that could only ever fail — verified against Codex
# 0.147.0. There is no equivalent knob to point at, so the honest thing is to
# say the recipe does not work against a self-signed deployment and name the two
# ways out, rather than leave an operator debugging a TLS error against a
# variable nothing reads.
def tls_note(mcp_url: str, client_id: str = "vscode") -> str:
    cert_url = mcp_url.rsplit("/mcp", 1)[0] + "/references/tls-certificate"
    if client_id == "codex":
        return (
            "⚠ Self-signed cert? This will NOT connect. Codex (verified against "
            "0.147.0) is a Rust binary — NODE_EXTRA_CA_CERTS is a Node variable and "
            "does nothing here, and there is no supported way to pin a deployment "
            "certificate. Either give BlueStick a CA-trusted certificate, or use the "
            "VS Code / Claude Code recipe, which can pin this one. The bearer-token "
            "setup above is correct and will work once the certificate does."
        )
    return (
        "Self-signed cert? Node-based clients refuse it. Fetch the deployment cert "
        f"(curl -sk {cert_url} -o bluestick.pem) and export "
        "NODE_EXTRA_CA_CERTS=/path/to/bluestick.pem in the shell you launch the client "
        "from — that trusts this deployment without turning verification off."
    )


def sandbox_note(workflow: str, client_id: str) -> str:
    """Client flags that keep a command-running agent inside its directory.

    Empty for workflows that only call the API.  The wording is deliberately
    "your client enforces this": the working-directory rule is a real boundary
    only where the process actually lives, and an operator who believes the
    server is enforcing it would grant more than they meant to.
    """
    if workflow not in _LOCAL_EXECUTION_WORKFLOWS:
        return ""
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


def build_mcp_clients(
    mcp_url: str, raw_key: str, *, workflow: str = "assist"
) -> List[Dict[str, Any]]:
    """Connection recipes, one per supported client, as plain dicts.

    Returned as dicts rather than a Pydantic model so the three routers that
    emit this can each keep their own response model without importing one
    another's.  The wrapper key and the file path differ by client, which is why
    a single blob can't serve all three: VS Code's ``.vscode/mcp.json`` wraps
    servers under ``servers`` while Claude Code uses ``mcpServers``, so the one
    original recipe was silently ignored by two of the three clients it was
    handed to.
    """
    name = server_name(workflow)
    env_var = key_env_var(workflow)
    entry = {name: _mcp_server_entry(mcp_url, raw_key)}
    return [
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
