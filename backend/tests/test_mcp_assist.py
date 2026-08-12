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


def test_start_session_emits_valid_mcp_config(client, test_project):
    """The assist-start response carries a ready-to-paste MCP client config
    pointing at /api/v1/mcp with this session's key."""
    import json

    body = _start_session(client, test_project.id)
    assert body["mcp_url"].endswith("/api/v1/mcp")
    cfg = json.loads(body["mcp_config"])
    server = cfg["servers"]["bluestick-assist"]
    assert server["type"] == "http"
    assert server["url"] == body["mcp_url"]
    assert server["headers"]["X-API-Key"] == body["api_key"]


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
    # The bulk report stream is advertised in instructions, not as a tool.
    assert "report-context.ndjson" in result["instructions"]
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
