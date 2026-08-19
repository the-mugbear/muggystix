"""Tests for the in-process MCP server that fronts the AI-Assist surface.

The MCP transport (`/api/v1/mcp`) is a thin JSON-RPC front door: `tools/call`
loops back into the real `/agent/assist/*` and `/agent/hosts/*` endpoints in
process, forwarding the caller's ``X-API-Key``.  These tests pin:

  1. The Streamable-HTTP handshake (initialize / tools/list / ping / notifs).
  2. That a real assist key drives a read tool and gets the same data the HTTP
     endpoint returns — proving the loopback + auth path works end to end.
  3. That auth STILL bites through MCP: no key -> the tool surfaces the
     endpoint's 403; a read-only key -> the write tool surfaces the capability
     gate's 403.  The MCP layer must never be an auth bypass.
"""

from __future__ import annotations


def _start_session(client, project_id, purpose="MCP smoke"):
    resp = client.post(
        f"/api/v1/projects/{project_id}/assist/start",
        json={"purpose": purpose},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _rpc(client, body, headers=None):
    return client.post("/api/v1/mcp", json=body, headers=headers or {})


def test_start_session_emits_per_client_mcp_setup(client, test_project):
    """The assist-start response carries MCP setup in the shape each client
    actually reads.

    v2.269.0 — this used to be one `mcp_config` blob in VS Code's shape handed
    to VS Code, Claude Code, and Cursor alike. The clients disagree: VS Code
    wraps servers under `servers`, Claude Code and Cursor under `mcpServers`,
    so two of the three silently ignored the server the dialog told the
    operator to paste. This pins the wrapper key per client.
    """
    import json

    body = _start_session(client, test_project.id)
    assert body["mcp_url"].endswith("/api/v1/mcp")
    clients = {c["id"]: c for c in body["mcp_clients"]}
    assert set(clients) == {"vscode", "claude_code", "codex", "cursor"}

    # VS Code: `servers`, workspace-local file.
    vscode = clients["vscode"]
    assert vscode["kind"] == "file" and vscode["path"] == ".vscode/mcp.json"
    server = json.loads(vscode["payload"])["servers"]["bluestick-assist"]
    assert server["type"] == "http"
    assert server["url"] == body["mcp_url"]
    assert server["headers"]["X-API-Key"] == body["api_key"]

    # Cursor: same entry, DIFFERENT wrapper key.
    cursor = clients["cursor"]
    assert cursor["kind"] == "file" and cursor["path"] == ".cursor/mcp.json"
    cursor_cfg = json.loads(cursor["payload"])
    assert "servers" not in cursor_cfg, "Cursor reads mcpServers, not servers"
    assert cursor_cfg["mcpServers"]["bluestick-assist"] == server

    # Claude Code: a CLI command, not a file — `claude mcp add` writes the
    # entry itself, so there is no wrapper key for the operator to get wrong.
    cc = clients["claude_code"]
    assert cc["kind"] == "command" and cc["path"] == ""
    assert cc["payload"].startswith("claude mcp add --transport http bluestick-assist ")
    assert body["mcp_url"] in cc["payload"]
    assert f'--header "X-API-Key: {body["api_key"]}"' in cc["payload"]

    # Codex: a command too, but it reads the key from the environment rather
    # than writing it into config.toml — the one client where the credential
    # never lands on disk in plaintext.
    codex = clients["codex"]
    assert codex["kind"] == "command"
    assert "--bearer-token-env-var BLUESTICK_ASSIST_KEY" in codex["payload"]
    # `read -rs` rather than a literal export: the key stays out of shell
    # history, and out of the shell profile the hint used to recommend —
    # which contradicted the "never lands on disk" claim beside it.
    assert "read -rs BLUESTICK_ASSIST_KEY" in codex["payload"]
    assert f"export {codex['payload'].split()[1]}=" not in codex["payload"]

    # Every recipe warns about the self-signed certificate, which blocks every
    # Node-based client before it sends a request.
    for entry in body["mcp_clients"]:
        assert "NODE_TLS_REJECT_UNAUTHORIZED" in entry["hint"], entry["id"]

    # Every entry is renderable: label, hint, payload all present.
    for c in body["mcp_clients"]:
        assert c["label"] and c["hint"] and c["payload"]


# ---------------------------------------------------------------------------
# Handshake / protocol
# ---------------------------------------------------------------------------

def test_initialize_handshake(client):
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 1
    result = body["result"]
    # We echo the client's requested protocol version.
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["serverInfo"]["name"] == "bluestick-assist"
    # The bulk report stream is advertised in instructions, not as a tool —
    # with a REAL url. v2.268.1: this carried a literal "{base}" placeholder
    # that nothing substituted, so every client got an unusable curl.
    instructions = result["instructions"]
    assert "report-context.ndjson" in instructions
    assert "{base}" not in instructions
    assert "https://" in instructions or "http://" in instructions
    assert "/api/v1/agent/assist/report-context.ndjson" in instructions
    # v2.271.0 — NO session id. The server is stateless; it used to mint an
    # Mcp-Session-Id and then ignore it, which tells a client state exists when
    # none does (it accepted any id, and DELETE was a no-op).
    assert resp.headers.get("Mcp-Session-Id") is None


def test_initialize_defaults_protocol_when_absent(client):
    resp = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp.json()["result"]["protocolVersion"] == "2025-06-18"


def test_tools_list_exposes_the_full_surface(client):
    resp = _rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
    # 7 reads + 3 writes.
    for name in (
        "assist_get_context", "assist_list_hosts", "assist_get_host",
        "assist_get_host_findings", "assist_list_scopes", "assist_list_scans",
        "assist_session_info", "assist_add_note", "assist_set_follow",
        "assist_patch_host",
    ):
        assert name in tools, f"missing tool {name}"
        # Every tool advertises a JSON-Schema object with a description.
        assert tools[name]["inputSchema"]["type"] == "object"
        assert tools[name]["description"]
    # Write tools require host_id.
    assert "host_id" in tools["assist_add_note"]["inputSchema"]["required"]


def test_ping_returns_empty_result(client):
    resp = _rpc(client, {"jsonrpc": "2.0", "id": 9, "method": "ping"})
    assert resp.json()["result"] == {}


def test_notification_gets_202_no_body(client):
    resp = _rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp.status_code == 202
    assert resp.content == b""


def test_unknown_method_is_jsonrpc_error(client):
    resp = _rpc(client, {"jsonrpc": "2.0", "id": 5, "method": "bogus/thing"})
    err = resp.json()["error"]
    assert err["code"] == -32601


def test_unknown_tool_is_a_protocol_error(client):
    """v2.271.0 — an unknown tool is a malformed request, not a tool that ran
    and failed. The spec puts it in the JSON-RPC error channel; `isError` is
    reserved for execution failures the model should reason about."""
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    })
    body = resp.json()
    assert "result" not in body
    assert body["error"]["code"] == -32602
    assert "Unknown tool" in body["error"]["message"]


# ---------------------------------------------------------------------------
# Loopback + auth
# ---------------------------------------------------------------------------

def test_tool_call_with_real_key_reads_context(client, test_project):
    """A real assist key drives assist_get_context through MCP and gets the
    same project payload the HTTP endpoint returns."""
    body = _start_session(client, test_project.id)
    headers = {"X-API-Key": body["api_key"]}

    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 10, "method": "tools/call",
        "params": {"name": "assist_get_context", "arguments": {}},
    }, headers=headers)
    result = resp.json()["result"]
    assert result["isError"] is False, result
    text = result["content"][0]["text"]
    assert f'"id": {test_project.id}' in text or f'"id":{test_project.id}' in text


def test_tool_call_without_key_is_forbidden(client, test_project):
    """No key on the MCP connection -> the read tool surfaces the endpoint's 403.
    The MCP layer is not an auth bypass."""
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 11, "method": "tools/call",
        "params": {"name": "assist_get_context", "arguments": {}},
    })
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "403" in result["content"][0]["text"] or "401" in result["content"][0]["text"]


def test_write_tool_blocked_for_readonly_key(client, test_project):
    """A default (read-only) assist key hitting assist_add_note surfaces the
    write:notes capability gate's 403 through MCP."""
    body = _start_session(client, test_project.id)
    headers = {"X-API-Key": body["api_key"]}
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 12, "method": "tools/call",
        "params": {
            "name": "assist_add_note",
            "arguments": {"host_id": 1, "body": "should be blocked"},
        },
    }, headers=headers)
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "403" in result["content"][0]["text"]


def test_write_tool_without_key_reports_missing_key(client):
    """A write tool with no key at all short-circuits with a clear message
    before any loopback."""
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 13, "method": "tools/call",
        "params": {"name": "assist_set_follow", "arguments": {"host_id": 1, "status": "watching"}},
    })
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "X-API-Key" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Pre-auth ceilings (v2.268.0)
#
# /api/v1/mcp takes no key at the FastAPI layer, so an anonymous caller must
# not be able to make the server do unbounded work before auth runs.
# ---------------------------------------------------------------------------

def test_oversize_body_is_rejected_before_parsing(client):
    """A body over the cap gets 413 — it is never materialised in memory.

    nginx allows 2 GB on /api/, so without this an anonymous POST could pin a
    worker's memory the same way the pre-v2.240.2 audit middleware could.
    """
    from app.api.v1.endpoints.mcp_assist import _MAX_REQUEST_BYTES

    payload = b'{"jsonrpc":"2.0","id":1,"method":"ping","pad":"' + b"a" * (
        _MAX_REQUEST_BYTES + 1024
    ) + b'"}'
    resp = client.post(
        "/api/v1/mcp", content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert "exceeds" in resp.json()["error"]["message"]


def test_oversize_body_rejected_when_content_length_lies(client):
    """The cap is on bytes actually read, so a false Content-Length or a
    chunked body can't smuggle a large payload past it."""
    from app.api.v1.endpoints.mcp_assist import _MAX_REQUEST_BYTES

    def _chunks():
        # No Content-Length at all — httpx sends this chunked.
        yield b'{"jsonrpc":"2.0","id":1,"method":"ping","pad":"'
        for _ in range((_MAX_REQUEST_BYTES // 1024) + 2):
            yield b"a" * 1024
        yield b'"}'

    resp = client.post(
        "/api/v1/mcp", content=_chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413


def test_oversize_batch_is_rejected(client):
    """Each batch element costs a full in-process loopback, so the array is
    length-capped: one anonymous request must not buy unbounded server work."""
    from app.api.v1.endpoints.mcp_assist import _MAX_BATCH_MESSAGES

    batch = [
        {"jsonrpc": "2.0", "id": i, "method": "tools/call",
         "params": {"name": "assist_get_context", "arguments": {}}}
        for i in range(_MAX_BATCH_MESSAGES + 1)
    ]
    resp = client.post("/api/v1/mcp", json=batch)
    assert resp.status_code == 413
    assert "Batch" in resp.json()["error"]["message"]


def test_batch_at_the_limit_still_works(client):
    """The cap is a ceiling, not a ban — a legal batch is still served."""
    from app.api.v1.endpoints.mcp_assist import _MAX_BATCH_MESSAGES

    batch = [
        {"jsonrpc": "2.0", "id": i, "method": "ping"}
        for i in range(_MAX_BATCH_MESSAGES)
    ]
    resp = client.post("/api/v1/mcp", json=batch)
    assert resp.status_code == 200
    assert len(resp.json()) == _MAX_BATCH_MESSAGES


def test_untrusted_browser_origin_is_rejected(client):
    """MCP's transport spec asks for Origin validation (DNS-rebinding
    defence).  Real clients send no Origin; a hostile page does."""
    resp = client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert "Origin" in resp.json()["error"]["message"]


def test_no_origin_header_is_allowed(client):
    """The check must not break the actual clients, which send no Origin."""
    resp = client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 200


def test_trailing_slash_is_served_not_redirected(client):
    """A POST to /api/v1/mcp/ is handled in place — a 307 would strand clients
    that drop the body on redirect."""
    resp = client.post(
        "/api/v1/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert resp.json()["result"] == {}


# ---------------------------------------------------------------------------
# Reference catalog (v2.270.0)
#
# The /reference/mcp page documents this surface. It reads the catalog off the
# live registry rather than restating it, so the docs can't drift from the
# server — these tests pin that they really are the same source.
# ---------------------------------------------------------------------------

def test_reference_catalog_matches_what_agents_see(client):
    """The documented tool set is exactly the tool set tools/list returns.

    A hand-maintained doc list would silently omit a newly added tool; this
    fails instead.
    """
    catalog = client.get("/api/v1/references/mcp-tools")
    assert catalog.status_code == 200, catalog.text
    documented = {t["name"] for t in catalog.json()["tools"]}

    served = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert documented == {t["name"] for t in served.json()["result"]["tools"]}


def test_reference_catalog_classifies_reads_and_writes(client):
    """`kind` is derived from the capability the endpoint requires, so a write
    can never be documented as a safe-to-always-allow read."""
    tools = {t["name"]: t for t in client.get("/api/v1/references/mcp-tools").json()["tools"]}

    for name in ("assist_get_context", "assist_list_hosts", "assist_get_host_findings"):
        assert tools[name]["kind"] == "read"
        assert tools[name]["capability"] is None

    assert tools["assist_add_note"]["capability"] == "write:notes"
    assert tools["assist_set_follow"]["capability"] == "write:follow"
    assert tools["assist_patch_host"]["capability"] == "write:host"
    for name in ("assist_add_note", "assist_set_follow", "assist_patch_host"):
        assert tools[name]["kind"] == "write"

    # Every tool carries what the page renders: a description, the underlying
    # route, and a JSON-Schema object for its parameters.
    for tool in tools.values():
        assert tool["description"]
        assert tool["method"] and tool["path"].startswith("/api/v1/agent/")
        assert tool["input_schema"]["type"] == "object"


def test_reference_catalog_reports_the_live_transport_facts(client):
    """Endpoint, protocol version, and the pre-auth ceilings come from the
    server, so the page can't advertise a limit the server doesn't enforce."""
    from app.api.v1.endpoints.mcp_assist import (
        _MAX_BATCH_MESSAGES,
        _MAX_REQUEST_BYTES,
        _PREFERRED_PROTOCOL_VERSION,
    )

    body = client.get("/api/v1/references/mcp-tools").json()
    assert body["endpoint"].endswith("/api/v1/mcp")
    assert body["protocol_version"] == _PREFERRED_PROTOCOL_VERSION
    assert body["max_request_bytes"] == _MAX_REQUEST_BYTES
    assert body["max_batch_messages"] == _MAX_BATCH_MESSAGES


# ---------------------------------------------------------------------------
# Protocol conformance (v2.271.0)
#
# All four came out of an external review of the 2.269.0 deployment.
# ---------------------------------------------------------------------------

def test_initialize_negotiates_instead_of_echoing_any_version(client):
    """A version we don't implement must not be reported as negotiated.

    Pre-fix the server echoed whatever it was asked for, so `2099-99-99` came
    back as agreed and the client believed it was talking a revision nobody
    implements.
    """
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2099-99-99"},
    })
    assert resp.json()["result"]["protocolVersion"] == "2025-06-18"

    # A version we DO implement is still honoured.
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 2, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    })
    assert resp.json()["result"]["protocolVersion"] == "2025-03-26"


def test_unsupported_protocol_version_header_is_rejected(client):
    resp = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"MCP-Protocol-Version": "2099-99-99"})
    assert resp.status_code == 400
    assert "Unsupported MCP-Protocol-Version" in resp.json()["error"]["message"]

    ok = _rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "ping"},
              headers={"MCP-Protocol-Version": "2025-06-18"})
    assert ok.status_code == 200


def test_malformed_params_are_invalid_params_not_a_500(client):
    """`params` as a string used to reach `.get()` and surface as an
    application HTTP 500 with an AttributeError."""
    for method in ("initialize", "tools/call"):
        resp = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": method, "params": "nope"})
        assert resp.status_code == 200, f"{method} -> HTTP {resp.status_code}"
        assert resp.json()["error"]["code"] == -32602

    # Same for arguments that aren't an object.
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "assist_get_context", "arguments": "nope"},
    })
    assert resp.json()["error"]["code"] == -32602


def test_arguments_are_checked_against_the_advertised_schema(client):
    """The schemas say additionalProperties:false, so an unknown argument is a
    protocol error — not silently dropped, which would let a model believe a
    filter applied when it never reached the endpoint."""
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_list_hosts", "arguments": {"nonsense": 1}},
    })
    err = resp.json()["error"]
    assert err["code"] == -32602 and "nonsense" in err["message"]

    # Missing required argument, likewise.
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "assist_get_host", "arguments": {}},
    })
    assert resp.json()["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# Client-facing metadata
# ---------------------------------------------------------------------------

def test_tools_carry_annotations_so_clients_can_default_approvals(client):
    tools = {t["name"]: t for t in _rpc(
        client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]}

    # readOnlyHint is what lets a host offer "always allow" on the reads —
    # the entire friction argument for MCP over curl.
    assert tools["assist_list_hosts"]["annotations"]["readOnlyHint"] is True
    assert tools["assist_add_note"]["annotations"]["readOnlyHint"] is False
    assert tools["assist_add_note"]["annotations"]["destructiveHint"] is False
    # A note is a new note each time; setting follow twice converges.
    assert tools["assist_add_note"]["annotations"]["idempotentHint"] is False
    assert tools["assist_set_follow"]["annotations"]["idempotentHint"] is True


def test_tools_list_hides_writes_a_session_cannot_perform(client, test_project):
    """A read-only key shouldn't be shown three tools it will only ever be
    refused for."""
    body = _start_session(client, test_project.id)
    resp = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-API-Key": body["api_key"]})
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert "assist_list_hosts" in names
    assert not {"assist_add_note", "assist_set_follow", "assist_patch_host"} & names

    # Without a key the full catalogue is still listed — that's the docs view.
    anon = {t["name"] for t in _rpc(
        client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    ).json()["result"]["tools"]}
    assert {"assist_add_note", "assist_set_follow", "assist_patch_host"} <= anon


def test_write_tools_appear_for_a_granted_session(client, test_project):
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start",
        json={"purpose": "granted", "can_write_assigned": True},
    )
    key = resp.json()["api_key"]
    names = {t["name"] for t in _rpc(
        client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"X-API-Key": key},
    ).json()["result"]["tools"]}
    assert {"assist_add_note", "assist_set_follow", "assist_patch_host"} <= names


def test_successful_reads_carry_structured_content(client, test_project):
    """Clients that understand structured results shouldn't have to re-parse a
    JSON string out of a text block."""
    body = _start_session(client, test_project.id)
    result = _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_get_context", "arguments": {}},
    }, headers={"X-API-Key": body["api_key"]}).json()["result"]

    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"        # still there for older clients
    assert result["structuredContent"]["project"]["id"] == test_project.id


# ---------------------------------------------------------------------------
# Audit fidelity (v2.271.0)
# ---------------------------------------------------------------------------

def test_audit_records_the_real_caller_not_the_loopback(client, test_project, db_session):
    """MCP-driven calls used to be audited as 127.0.0.1 / python-httpx — the
    in-process loopback's own identity — which makes the activity log useless
    for the one question it answers: who did this.

    The identity is captured server-side from the inbound request, so it can't
    be spoofed by a caller-supplied header.
    """
    from app.db.models_agent import AgentApiCall

    body = _start_session(client, test_project.id)
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_get_context", "arguments": {}},
    }, headers={"X-API-Key": body["api_key"], "User-Agent": "acme-mcp-client/2.1"})
    assert resp.json()["result"]["isError"] is False

    row = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.path == "/api/v1/agent/assist/context")
        .order_by(AgentApiCall.id.desc())
        .first()
    )
    assert row is not None
    assert row.user_agent == "acme-mcp-client/2.1"
    assert "httpx" not in (row.user_agent or "")


def test_unauthenticated_calls_write_no_audit_row(client, caplog):
    """A request rejected before auth has no attribution, and the table's CHECK
    requires attribution or an error_class — so the insert was rejected and the
    failure logged as an ERROR traceback on every anonymous probe."""
    import logging
    from app.db.models_agent import AgentApiCall

    with caplog.at_level(logging.ERROR):
        result = _rpc(client, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "assist_get_context", "arguments": {}},
        }).json()["result"]

    assert result["isError"] is True  # the agent still learns it was refused
    assert not [m for m in caplog.messages if "agent_api_call write failed" in m]


def test_environment_probe_tool_resolves_the_session_from_the_key(client, test_project):
    """The guide makes the environment probe the mandatory first step, but no
    tool exposed it — an MCP-only client had to fall back to curl for the one
    call it must make first (v2.271.0).

    The session id is resolved from the key rather than made the model's
    problem: the key is already bound to exactly one assist session.
    """
    body = _start_session(client, test_project.id)
    result = _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "assist_record_environment",
            "arguments": {"os_family": "linux", "shell": "bash"},
        },
    }, headers={"X-API-Key": body["api_key"]}).json()["result"]

    assert result["isError"] is False, result
    assert result["structuredContent"]["environment"]["os_family"] == "linux"
    assert result["structuredContent"]["session_type"] == "assist"

    # And the session now reports it as probed, so the agent's context reflects it.
    info = _rpc(client, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "assist_session_info", "arguments": {}},
    }, headers={"X-API-Key": body["api_key"]}).json()["result"]
    assert info["structuredContent"]["environment_probed"] is True


def test_mcp_page_size_defaults_are_smaller_than_the_download_defaults(client, test_project):
    """500 hosts is right for a file download and a lot of tokens for a model
    that usually wants the first handful."""
    from app.api.v1.endpoints.mcp_assist import _TOOLS

    assert _TOOLS["assist_list_hosts"]["defaults"]["limit"] == 100
    assert _TOOLS["assist_get_host_findings"]["defaults"]["limit"] == 50

    # An explicit limit still wins.
    body = _start_session(client, test_project.id)
    result = _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_list_hosts", "arguments": {"limit": 3}},
    }, headers={"X-API-Key": body["api_key"]}).json()["result"]
    assert result["isError"] is False


def test_bearer_token_authenticates_an_mcp_call(client, test_project):
    """Codex sends the key as `Authorization: Bearer`, which the agent auth layer
    has always accepted — but the MCP layer only read X-API-Key, so a Codex
    client authenticated as nobody (v2.271.0)."""
    body = _start_session(client, test_project.id)
    result = _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_get_context", "arguments": {}},
    }, headers={"Authorization": f"Bearer {body['api_key']}"}).json()["result"]

    assert result["isError"] is False, result
    assert result["structuredContent"]["project"]["id"] == test_project.id


# ---------------------------------------------------------------------------
# Registry ↔ API contract (v2.272.0)
#
# "Read off the live registry" only guarantees the docs match the registry —
# the registry itself still restates the API by hand. These pin it to the
# actual routes and to the behaviour the schemas advertise.
# ---------------------------------------------------------------------------

def test_every_tool_targets_a_route_that_exists():
    """A tool whose path or method doesn't exist is a 404 the agent can't act
    on, and the reference page would document it as real."""
    from app.api.v1.endpoints.mcp_assist import _TOOLS
    from app.main import app

    paths = app.openapi()["paths"]
    for name, spec in _TOOLS.items():
        assert spec["path"] in paths, f"{name} targets unknown path {spec['path']}"
        assert spec["method"].lower() in paths[spec["path"]], (
            f"{name} uses {spec['method']} on {spec['path']}, which does not accept it"
        )


def test_advertised_defaults_match_what_the_server_injects(client):
    """The registry injected smaller page sizes while the schema still
    advertised the endpoint's own — so a client read 500 and got 100, and
    /references/mcp-tools published the wrong number."""
    from app.api.v1.endpoints.mcp_assist import _TOOLS

    tools = {t["name"]: t for t in _rpc(
        client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]}

    for name, spec in _TOOLS.items():
        for arg, injected in spec.get("defaults", {}).items():
            advertised = tools[name]["inputSchema"]["properties"][arg].get("default")
            assert advertised == injected, (
                f"{name}.{arg}: advertises {advertised}, server applies {injected}"
            )

    # And the reference page publishes the same corrected numbers.
    catalog = {t["name"]: t for t in client.get("/api/v1/references/mcp-tools").json()["tools"]}
    assert catalog["assist_list_hosts"]["input_schema"]["properties"]["limit"]["default"] == 100


def test_probe_accepts_the_attribution_fields_the_prompt_asks_for(client, test_project):
    """The assist prompt tells agents to send agent_model / agent_tool /
    agent_prompt_version. The tool omitted them, and since unknown arguments
    became an error an agent following its instructions got -32602."""
    body = _start_session(client, test_project.id)
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "assist_record_environment",
            "arguments": {
                "os_family": "linux", "shell": "bash",
                "agent_model": "claude-opus-5",
                "agent_tool": "claude-code",
                "agent_prompt_version": "1.49.0",
            },
        },
    }, headers={"X-API-Key": body["api_key"]}).json()

    assert "error" not in resp, resp
    assert resp["result"]["isError"] is False, resp


def test_overwriting_tools_are_not_advertised_as_additive(client):
    """destructiveHint:false means "additive updates only" per the spec. Only
    add_note is additive; the others replace a stored value."""
    tools = {t["name"]: t["annotations"] for t in _rpc(
        client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"]}

    assert tools["assist_add_note"]["destructiveHint"] is False
    for name in ("assist_set_follow", "assist_patch_host", "assist_record_environment"):
        assert tools[name]["destructiveHint"] is True, name
    # Reads are never destructive.
    assert tools["assist_list_hosts"]["destructiveHint"] is False


# ---------------------------------------------------------------------------
# Remaining 2025-06-18 conformance
# ---------------------------------------------------------------------------

def test_batching_is_refused_under_the_revision_that_removed_it(client):
    batch = [{"jsonrpc": "2.0", "id": 1, "method": "ping"}]
    resp = _rpc(client, batch, headers={"MCP-Protocol-Version": "2025-06-18"})
    assert resp.status_code == 400
    assert "batching was removed" in resp.json()["error"]["message"]

    # 2025-03-26 still permits it, so a client that declared that version works.
    ok = _rpc(client, batch, headers={"MCP-Protocol-Version": "2025-03-26"})
    assert ok.status_code == 200 and len(ok.json()) == 1


def test_empty_batch_is_an_invalid_request(client):
    resp = _rpc(client, [])
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32600


def test_params_are_shape_checked_on_every_method(client):
    """Only initialize and tools/call used to look, so `tools/list` with a
    string `params` sailed through."""
    resp = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": "invalid"})
    assert resp.json()["error"]["code"] == -32602
