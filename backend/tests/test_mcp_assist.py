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
    assert set(clients) == {"vscode", "claude_code", "cursor"}

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
    # A session id is issued on initialize.
    assert resp.headers.get("Mcp-Session-Id")


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


def test_unknown_tool_is_tool_error(client):
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    })
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "Unknown tool" in result["content"][0]["text"]


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
