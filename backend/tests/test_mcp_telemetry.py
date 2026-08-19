"""MCP transport telemetry.

The agent-API audit log only sees requests that reach ``/agent/*``. Everything
the MCP layer rejects short of that — unknown tool, arguments that don't fit the
advertised schema, a refused batch, a bad protocol version — reached nothing and
was recorded nowhere.

That blind spot was not hypothetical: for two releases the environment-probe
tool rejected the exact fields the assist prompt tells agents to send, blocking
every conforming agent, and no surface in the system could have shown it. These
tests pin that each of those now leaves a row, and that the row says enough to
act on.
"""
from __future__ import annotations

from app.db.models_agent import McpToolCall


def _rpc(client, body, headers=None):
    return client.post("/api/v1/mcp", json=body, headers=headers or {})


def _rows(db_session, **filters):
    q = db_session.query(McpToolCall)
    for column, value in filters.items():
        q = q.filter(getattr(McpToolCall, column) == value)
    return q.order_by(McpToolCall.id.desc()).all()


def test_successful_tool_call_is_recorded_with_its_tool_name(client, test_project, db_session):
    body = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "telemetry"}
    ).json()
    _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_get_context", "arguments": {}},
    }, headers={"X-API-Key": body["api_key"], "User-Agent": "acme-mcp/1.0"})

    row = _rows(db_session, tool_name="assist_get_context")[0]
    assert row.outcome == "ok"
    assert row.rpc_method == "tools/call"
    assert row.user_agent == "acme-mcp/1.0"
    # Prefix only — never the raw key, but enough to correlate to a session.
    assert row.api_key_prefix and row.api_key_prefix in body["api_key"]
    assert body["api_key"] not in (row.api_key_prefix or "")
    assert row.duration_ms is not None


def test_unknown_tool_leaves_a_row_naming_the_tool_that_was_asked_for(client, db_session):
    """The most actionable row in the table: a client working from a stale or
    invented tool list. Previously this produced a JSON-RPC error and nothing
    else — no way to know it had ever happened."""
    _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_delete_everything", "arguments": {}},
    })

    row = _rows(db_session, tool_name="assist_delete_everything")[0]
    assert row.outcome == "protocol_error"
    assert row.error_code == -32602
    assert "Unknown tool" in row.detail


def test_bad_arguments_record_what_was_wrong(client, db_session):
    """The environment-probe regression in the flesh: an agent sending a field
    the schema doesn't allow. The detail has to name the argument, or the row
    can't tell you which agent instruction is out of step."""
    _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_list_hosts", "arguments": {"nonsense": 1}},
    })

    row = _rows(db_session, tool_name="assist_list_hosts")[0]
    assert row.outcome == "protocol_error"
    assert "nonsense" in row.detail


def test_refused_tool_call_records_the_endpoint_status(client, test_project, db_session):
    """A capability refusal is a tool_error, not a protocol error — and the
    endpoint's status is lifted out of the message so failures group by cause
    rather than by string."""
    body = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "telemetry"}
    ).json()
    _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_add_note", "arguments": {"host_id": 1, "body": "x"}},
    }, headers={"X-API-Key": body["api_key"]})

    row = _rows(db_session, tool_name="assist_add_note")[0]
    assert row.outcome == "tool_error"
    assert row.error_code == 403


def test_transport_rejections_are_recorded(client, db_session):
    """These never reach a handler, so before telemetry a client stuck on an
    unsupported protocol version failed silently from our side."""
    _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "ping"},
         headers={"MCP-Protocol-Version": "2099-99-99"})
    version_row = _rows(db_session, outcome="rejected")[0]
    assert version_row.error_code == 400
    assert "Unsupported MCP-Protocol-Version" in version_row.detail

    _rpc(client, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
         headers={"MCP-Protocol-Version": "2025-06-18"})
    batch_row = _rows(db_session, outcome="rejected")[0]
    assert "batching was removed" in batch_row.detail


def test_initialize_records_which_client_connected(client, db_session):
    """clientInfo is the only place a client identifies itself; capturing it is
    how "which clients are people actually using" becomes answerable."""
    _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "claude-code", "version": "2.1.235"},
        },
    })

    row = _rows(db_session, rpc_method="initialize")[0]
    assert row.outcome == "ok"
    assert row.client_name == "claude-code"
    assert row.client_version == "2.1.235"
    assert row.protocol_version == "2025-06-18"


def test_notifications_are_not_recorded(client, db_session):
    """A notification is answered by nothing and can fail in no way — recording
    one per connect would be noise in a table meant for signal."""
    before = db_session.query(McpToolCall).count()
    resp = _rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp.status_code == 202
    assert db_session.query(McpToolCall).count() == before


def test_summary_reports_tools_nobody_has_called(client, test_project, db_session):
    """A view built only from recorded calls cannot show "no agent has ever used
    this tool", which is exactly the question the telemetry exists to answer."""
    body = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "telemetry"}
    ).json()
    _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_get_context", "arguments": {}},
    }, headers={"X-API-Key": body["api_key"]})

    summary = client.get("/api/v1/mcp-telemetry/summary").json()

    assert summary["tools"]["assist_get_context"]["ok"] == 1
    # Present with zeroes rather than absent.
    assert summary["tools"]["assist_list_scopes"] == {
        "ok": 0, "tool_error": 0, "protocol_error": 0, "kind": "read",
    }
    assert summary["tools"]["assist_add_note"]["kind"] == "write"
    assert summary["by_outcome"]["ok"] >= 1


def test_summary_surfaces_calls_to_tools_that_do_not_exist(client, db_session):
    _rpc(client, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_invented", "arguments": {}},
    })
    summary = client.get("/api/v1/mcp-telemetry/summary").json()
    assert "assist_invented" in summary["unknown_tools_called"]


def test_summary_is_gated_on_the_admin_role():
    """Telemetry names key prefixes and client software — deployment
    diagnostics, not something every project member should read.

    Asserted structurally because the ``client`` fixture authenticates as an
    admin, so an HTTP call can only ever show the allowed path. This pins the
    gate itself, which is the part a refactor could drop.
    """
    from app.api.v1.endpoints import mcp_telemetry

    from app.db.models_auth import UserRole

    gates = [d.dependency for d in (mcp_telemetry.router.dependencies or [])]
    assert len(gates) == 1, "the telemetry router should carry exactly one role gate"

    # require_role(role) returns a closure over the role it enforces — read the
    # cell rather than asserting on a repr, so this fails if the gate is ever
    # widened to a lesser role.
    enforced = [cell.cell_contents for cell in (gates[0].__closure__ or ())]
    assert UserRole.ADMIN in enforced
