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
    to every client alike. They disagree: VS Code wraps servers under `servers`
    where Claude Code uses `mcpServers`, so a client silently ignored the server
    the dialog told the operator to paste. This pins the shape per client.

    v2.275.0 — Cursor removed. It was the one recipe never verified against a
    real install, and inference is how the original bug shipped.
    """
    import json

    body = _start_session(client, test_project.id)
    assert body["mcp_url"].endswith("/api/v1/mcp")
    clients = {c["id"]: c for c in body["mcp_clients"]}
    assert set(clients) == {"vscode", "claude_code", "codex"}

    # VS Code: `servers`, workspace-local file.
    vscode = clients["vscode"]
    assert vscode["kind"] == "file" and vscode["path"] == ".vscode/mcp.json"
    server = json.loads(vscode["payload"])["servers"]["bluestick-assist"]
    assert server["type"] == "http"
    assert server["url"] == body["mcp_url"]
    assert server["headers"]["X-API-Key"] == body["api_key"]

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
    # client before it sends a request — but with the mechanism that applies to
    # it, and none of them by switching verification off. BlueStick is
    # self-hosted on a private address and will never hold a CA-signed cert, so
    # "pin this one" is the only fix that exists (v2.285.0).
    for entry in body["mcp_clients"]:
        if entry["id"] == "codex":
            # Rust binary: reads SSL_CERT_DIR, not the Node variable, and not
            # SSL_CERT_FILE either (both verified against codex 0.147.0).
            assert "SSL_CERT_DIR" in entry["hint"]
            assert "NODE_EXTRA_CA_CERTS does nothing" in entry["hint"]
        else:
            assert "NODE_EXTRA_CA_CERTS" in entry["hint"], entry["id"]
        # Every recipe can still fetch the cert from a remote deployment.
        assert "/references/tls-certificate" in entry["hint"], entry["id"]
        assert "NODE_TLS_REJECT_UNAUTHORIZED" not in entry["hint"], entry["id"]

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
        "assist_get_host_vulnerabilities", "assist_list_scopes", "assist_list_scans",
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


def test_tool_call_without_key_answers_a_real_401(client, test_project):
    """No credential -> HTTP 401 with a bearer challenge (v2.276.0).

    This used to be HTTP 200 carrying an isError result: a request that
    succeeded at the transport layer and failed inside. That reads fine to a
    model and not at all to a client — nothing in the exchange said "you are
    unauthenticated", so a client could not prompt for a key, surface a
    connection error, or stop retrying. The MCP layer is still not an auth
    bypass; it just says so in the status code now.
    """
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 11, "method": "tools/call",
        "params": {"name": "assist_get_context", "arguments": {}},
    })
    assert resp.status_code == 401
    challenge = resp.headers["WWW-Authenticate"]
    assert challenge.startswith("Bearer ")
    # No `resource_metadata=` — that is how MCP bootstraps OAuth discovery, and
    # advertising it when this server is not an OAuth resource server would send
    # capable clients into a flow that dead-ends.
    assert "resource_metadata" not in challenge
    # Nothing was authenticated, so the client is not told its token is bad.
    assert "invalid_token" not in challenge
    body = resp.json()
    assert body["id"] == 11 and body["error"]["code"] == -32001


def test_rejected_key_says_the_token_is_the_problem(client):
    """A key that was sent but isn't usable gets `error="invalid_token"`, so the
    client knows to re-authenticate rather than assume it forgot the header."""
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_get_context", "arguments": {}},
    }, headers={"X-API-Key": "nm_agent_not_a_real_key"})
    assert resp.status_code == 401
    assert 'error="invalid_token"' in resp.headers["WWW-Authenticate"]


# ``test_write_tool_blocked_for_readonly_key`` removed in v2.309.0 with the
# capability system: there is no longer a read-only assist key to hold, since a
# session acts with its operator's project permissions. The role-based refusal
# it stood for is covered by test_agent_operator_access.py.


def test_an_endpoint_refusal_stays_a_tool_result_not_a_transport_error(
    client, test_project
):
    """The line between a transport status and ``isError``.

    A per-call refusal is an outcome the model should read and work around
    ("that host doesn't exist"). Promoting it to a transport status would tell
    the client to re-authenticate over something no amount of re-authenticating
    fixes — the key is fine, the call isn't. Only *authentication* failures
    become HTTP 401.

    v2.309.0 — this used to drive a capability refusal (403) with a read-only
    key. Capabilities are gone, so it drives a missing host instead; the
    property under test is the transport's behaviour, not which refusal
    produced it.
    """
    body = _start_session(client, test_project.id)
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 13, "method": "tools/call",
        "params": {
            "name": "assist_set_follow",
            "arguments": {"host_id": 999_999, "status": "watching"},
        },
    }, headers={"X-API-Key": body["api_key"]})

    assert resp.status_code == 200, "a per-call refusal must not become a transport error"
    result = resp.json()["result"]
    assert result["isError"] is True


def test_write_tool_without_key_also_answers_401(client):
    """Every missing-credential case takes one path (v2.276.0).

    Writes used to short-circuit with a hand-written "you need an X-API-Key"
    message before any loopback, which meant one auth failure looked different
    from the others. The endpoint's own 401 says it better, and uniformity is
    what lets the transport turn all of them into a real status.
    """
    resp = _rpc(client, {
        "jsonrpc": "2.0", "id": 13, "method": "tools/call",
        "params": {"name": "assist_set_follow", "arguments": {"host_id": 1, "status": "watching"}},
    })
    assert resp.status_code == 401
    assert "X-API-Key" in resp.json()["error"]["message"]


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
    """`kind` is derived from the HTTP method the tool dispatches, so a write
    can never be documented as a safe-to-always-allow read.

    v2.311.0 — it used to be qualified against a per-tool `capability`, which
    the catalog also published. Both are gone: authority is the operator's
    project role, checked per request, so there is no per-tool permission to
    document. `kind` has to stand on its own now, which is what this pins.
    """
    tools = {t["name"]: t for t in client.get("/api/v1/references/mcp-tools").json()["tools"]}

    for name in ("assist_get_context", "assist_list_hosts", "assist_get_host_vulnerabilities"):
        assert tools[name]["kind"] == "read"

    for name in ("assist_add_note", "assist_set_follow", "assist_patch_host"):
        assert tools[name]["kind"] == "write"

    # The removed field must not linger on any entry — a client reading a stale
    # `capability` would be reasoning about a gate that no longer exists.
    assert all("capability" not in t for t in tools.values())

    # Every tool carries what the page renders: a description, the underlying
    # route, and a JSON-Schema object for its parameters.
    #
    # Routes live under /agent/* with two deliberate exceptions, both of them
    # deployment-wide documentation rather than project data: the approved-tool
    # listing (the same list a human reads at /reference/tools) and the guide.
    # Serving either through a project-scoped route would imply it varies per
    # project, which neither does.
    documentation_routes = {"/api/v1/references/tools", "/api/v1/agents-guide"}
    for tool in tools.values():
        assert tool["description"]
        assert tool["method"]
        assert (
            tool["path"].startswith("/api/v1/agent/")
            or tool["path"] in documentation_routes
        ), f"{tool['name']} points at an unexpected route: {tool['path']}"
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


def test_tools_list_is_scoped_by_workflow_not_by_grant(client, test_project):
    """v2.309.0 — was ``..._hides_writes_a_session_cannot_perform``.

    The capability filter is gone with the capability system, so write tools
    are listed for every assist session; whether a given write succeeds is the
    operator's project role, decided at the endpoint. That is also more honest
    than the filter was: with the old row-level constraint, a *listed* write
    could still be refused for an unassigned host, so the list never actually
    meant "these will work".

    The workflow filter stays — it is what keeps the catalogue (and its ~10k
    tokens) scoped to the surface the session can reach.
    """
    body = _start_session(client, test_project.id)
    resp = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"X-API-Key": body["api_key"]})
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert "assist_list_hosts" in names
    assert {"assist_add_note", "assist_set_follow", "assist_patch_host"} <= names
    # Still scoped by workflow: an assist key is not shown recon/plan tooling.
    assert not {"recon_upload", "plan_submit"} & names

    # Without a key the full catalogue is still listed — that's the docs view.
    anon = {t["name"] for t in _rpc(
        client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    ).json()["result"]["tools"]}
    assert {"assist_add_note", "assist_set_follow", "assist_patch_host"} <= anon


def test_write_tools_appear_for_every_assist_session(client, test_project):
    """v2.311.0 — was `..._for_a_granted_session`, and started the session with
    `can_write_assigned: True`. There is no grant to ask for: write tools are
    listed for every assist session, and whether a write lands is the operator's
    project role at request time."""
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start",
        json={"purpose": "ordinary session"},
    )
    assert resp.status_code == 201, resp.text
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
        resp = _rpc(client, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "assist_get_context", "arguments": {}},
        })

    assert resp.status_code == 401  # the caller still learns it was refused
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
    assert _TOOLS["assist_get_host_vulnerabilities"]["defaults"]["limit"] == 50

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


def test_granted_writes_land_through_mcp(client, test_project, test_user, db_session):
    """All three project-data writes, end to end through the transport.

    Committed late (v2.274.0): the suite covered refusals and the environment
    probe, so the body-forwarding path for project writes was only ever verified
    by hand. A silent regression there would have looked like a working server.
    """
    from datetime import datetime, timezone
    from app.db.models import Annotation, Host, HostFollow, FollowStatus

    now = datetime.now(timezone.utc)
    host = Host(project_id=test_project.id, ip_address="10.77.0.5", state="up",
                first_seen=now, last_seen=now)
    db_session.add(host); db_session.commit(); db_session.refresh(host)
    db_session.add(HostFollow(host_id=host.id, user_id=test_user.id,
                              status=FollowStatus.IN_REVIEW, assigned_at=now,
                              assigned_by_id=test_user.id))
    db_session.commit()

    key = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start",
        json={"purpose": "write path"},
    ).json()["api_key"]
    headers = {"X-API-Key": key}

    def call(name, args):
        return _rpc(client, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }, headers=headers).json()["result"]

    note = call("assist_add_note", {"host_id": host.id, "body": "vsftpd 2.3.4 on 21"})
    assert note["isError"] is False, note
    assert note["structuredContent"]["actor_type"] == "agent"

    follow = call("assist_set_follow", {"host_id": host.id, "status": "reviewed"})
    # 204 has no body to structure — the result is a plain OK, and the page says so.
    assert follow["isError"] is False and follow["content"][0]["text"] == "OK"
    assert "structuredContent" not in follow

    patch = call("assist_patch_host", {"host_id": host.id, "hostname": "ftp01.corp"})
    assert patch["isError"] is False, patch
    assert patch["structuredContent"]["changed"] == ["hostname"]

    # Each one actually reached the database, not just a 2xx.
    db_session.expire_all()
    assert db_session.query(Annotation).filter(
        Annotation.host_id == host.id, Annotation.body == "vsftpd 2.3.4 on 21"
    ).count() == 1
    assert db_session.get(Host, host.id).hostname == "ftp01.corp"
    assert db_session.query(HostFollow).filter(
        HostFollow.host_id == host.id, HostFollow.user_id == test_user.id
    ).one().status == FollowStatus.REVIEWED

    # `none` clears the follow — the inverse the enum otherwise lacks (v2.315.0).
    cleared = call("assist_set_follow", {"host_id": host.id, "status": "none"})
    assert cleared["isError"] is False and cleared["content"][0]["text"] == "OK"
    db_session.expire_all()
    assert db_session.query(HostFollow).filter(
        HostFollow.host_id == host.id, HostFollow.user_id == test_user.id
    ).count() == 0


def test_repeated_tools_list_does_not_spam_the_activity_log(client, test_project, db_session):
    """Workflow + capability filtering is server-initiated plumbing on an audited
    endpoint, so a client that re-lists tools each turn used to add a row to the
    operator's activity view every time (3 of 7 rows in the 2.273.0 end-to-end
    run were these). Cached for the session's benefit, not ours."""
    from app.api.v1.endpoints.mcp_assist import _identity_cache
    from app.db.models_agent import AgentApiCall

    _identity_cache.clear()
    body = _start_session(client, test_project.id)
    headers = {"X-API-Key": body["api_key"]}

    def session_rows():
        return (
            db_session.query(AgentApiCall)
            .filter(AgentApiCall.path == "/api/v1/agent/identity")
            .count()
        )

    before = session_rows()
    for _ in range(4):
        resp = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers)
        assert "assist_list_hosts" in {t["name"] for t in resp.json()["result"]["tools"]}

    assert session_rows() - before == 1, "each tools/list logged its own lookup"


def test_the_analysis_tools_round_trip_through_the_loopback(client, test_project):
    """v2.294.0 — posture, patterns and finding-detail over MCP.

    The registry restates each endpoint by hand (path, method, param split), so
    a tool can be well-formed, pass the route-exists contract test, and still
    fail on the first real call — a path parameter declared as a query one, say.
    These drive all three the way a client does.
    """
    body = _start_session(client, test_project.id)
    headers = {"X-API-Key": body["api_key"]}

    def call(name, args=None, rid=1):
        return _rpc(client, {
            "jsonrpc": "2.0", "id": rid, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        }, headers=headers).json()["result"]

    posture = call("assist_get_posture", rid=40)
    assert posture["isError"] is False, posture
    assert "label" in posture["content"][0]["text"]

    patterns = call("assist_get_patterns", rid=41)
    assert patterns["isError"] is False, patterns
    assert "adopted" in patterns["content"][0]["text"]

    # A path parameter has to reach the URL, not the query string — a miswired
    # one 404s or, worse, silently returns a different finding.
    missing = call("assist_get_finding", {"finding_id": 999_999}, rid=42)
    assert missing["isError"] is True
    assert "not found" in missing["content"][0]["text"].lower()


def test_the_p2_tools_round_trip_through_the_loopback(client, test_project):
    """v2.297.0 — ingestion issues and the reshaped segments tool.

    ``assist_list_segments`` changed shape (a bare list became an envelope) and
    gained an ``offset`` param the registry has to declare; the registry
    restates every endpoint by hand, so an undeclared param is silently dropped
    rather than rejected — the caller gets page one and no error.
    """
    body = _start_session(client, test_project.id)
    headers = {"X-API-Key": body["api_key"]}

    def call(name, args=None, rid=1):
        return _rpc(client, {
            "jsonrpc": "2.0", "id": rid, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        }, headers=headers).json()["result"]

    issues = call("assist_list_ingestion_issues", rid=50)
    assert issues["isError"] is False, issues
    assert "has_issues" in issues["content"][0]["text"]

    segments = call("assist_list_segments", {"limit": 5, "offset": 0}, rid=51)
    assert segments["isError"] is False, segments
    # The envelope, not a bare list — `total` is what tells the agent whether
    # it is looking at the whole estate or the worst of it.
    assert "adopted" in segments["content"][0]["text"]
