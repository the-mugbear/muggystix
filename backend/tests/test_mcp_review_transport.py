"""External MCP review (2026-09-17), findings 1 / 5 / 6 / 8 — the transport,
registry and gate side.  The reviewer's reproductions live in
``test_mcp_review.py``; these pin the mechanism behind each fix so a later
registry edit cannot quietly reopen it.

1. Arguments are validated against the advertised schema (type / enum / bounds)
   BEFORE URL construction, and path segments are rendered so they can never
   carry ``/``, ``?`` or ``#`` — a mistyped path argument is -32602, not a
   call to a different endpoint.
6. ``POST /agent/session/end`` is a session-metadata write (any member).
8. ``idempotentHint`` is false for every tool whose retry creates a row.
"""
import pytest

from app.api.v1.endpoints.mcp_tools import TOOLS, tool_list_payload


def _rpc(client, key, body):
    return client.post("/api/v1/mcp", headers={"X-API-Key": key}, json=body).json()


def _call(client, key, name, **arguments):
    return _rpc(client, key, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return r.json()["api_key"]


# ---------------------------------------------------------------------------
# Finding 1 — schema validation before URL construction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name, arguments, fragment",
    [
        # A string where an integer path param is declared: the reviewer's case.
        ("assist_add_note", {"host_id": "../session/end#", "body": "x"}, "'host_id' must be an integer"),
        # Same for a query/body integer.
        ("assist_list_hosts", {"limit": "50"}, "'limit' must be an integer"),
        # Booleans are not integers, whatever Python thinks.
        ("assist_get_host", {"host_id": True}, "'host_id' must be an integer"),
        # Bounds the registry declares.
        ("assist_get_host", {"host_id": 0}, "'host_id' must be >= 1"),
        # Enum the registry declares.
        ("assist_set_follow", {"host_id": 1, "status": "definitely_not_a_status"}, "must be one of"),
        # Unknown argument under additionalProperties: false.
        ("assist_get_host", {"host_id": 1, "verbose": True}, "unknown argument(s) verbose"),
    ],
)
def test_mistyped_arguments_are_protocol_errors(client, test_project, name, arguments, fragment):
    key = _start(client, test_project)
    resp = _call(client, key, name, **arguments)
    assert resp.get("error", {}).get("code") == -32602, resp
    assert fragment in resp["error"]["message"], resp


def test_validation_rejects_before_any_endpoint_runs(client, db_session, test_project):
    """The reviewer's redirect: the mistyped host_id must never become a URL.
    The session stays active and the audit log holds no /session/end call."""
    from app.db.models_agent import AgentApiCall, AgentSession

    key = _start(client, test_project)
    resp = _call(client, key, "assist_add_note", host_id="../session/end#", body="probe")
    assert resp["error"]["code"] == -32602
    db_session.expire_all()
    session = db_session.query(AgentSession).filter_by(project_id=test_project.id).one()
    assert session.status == "active"
    assert db_session.query(AgentApiCall).filter(AgentApiCall.path.like("%/session/end%")).count() == 0


def test_path_segment_never_carries_a_separator():
    from app.api.v1.endpoints.mcp_assist import _path_segment

    int_spec = {"input_schema": {"properties": {"host_id": {"type": "integer"}}}}
    assert _path_segment(int_spec, "host_id", 42) == "42"
    # Untyped / string-typed values are one encoded segment, whatever they hold.
    str_spec = {"input_schema": {"properties": {"name": {"type": "string"}}}}
    seg = _path_segment(str_spec, "name", "../session/end#?x=1")
    assert "/" not in seg and "?" not in seg and "#" not in seg
    assert seg == "..%2Fsession%2Fend%23%3Fx%3D1"


def test_every_path_param_in_the_registry_is_typed():
    """The path-segment rule keys off the declared type; an undeclared one
    would fall back to percent-encoding, which is safe but wrong for an id."""
    for name, spec in TOOLS.items():
        for pname in spec.get("path_params", ()):
            prop = spec["input_schema"]["properties"].get(pname)
            assert prop is not None and prop.get("type") == "integer", (name, pname)


# ---------------------------------------------------------------------------
# Finding 6 — ending one's own session is session bookkeeping
# ---------------------------------------------------------------------------

def test_session_end_is_on_the_metadata_write_allowlist():
    import app.main  # noqa: F401
    from app.main import app
    from tests.agent_route_declarations import agent_route_declarations

    declared = agent_route_declarations(app)
    assert declared[("POST", "/api/v1/agent/session/end")].session_metadata_write


# ---------------------------------------------------------------------------
# Finding 8 — retry safety
# ---------------------------------------------------------------------------

#: Tools whose retry creates a second row.  Every additive tool is here by
#: construction; the creators are the ones the inference used to get wrong.
_CREATES_A_ROW = {
    "submit_feedback", "suggest_tool", "assist_add_note",
    # v2.442.0 (replaces create_test_plan / start_execution / plan_add_entries
    # / the execution_record_* tools).  A retry with the SAME request_key
    # returns the existing test, but a caller that mints a new key per attempt
    # creates a second one, so the hint stays the conservative "not idempotent".
    "host_tests_propose",
    # v2.436.0: each call records a new evidence row / proposal.
    "record_evidence", "propose_finding_text", "propose_finding",
    "propose_observation", "propose_endpoint_status",
    # v2.457.0: a note without a request_key is a second entry on retry; the
    # field changes of remediation_apply converge, its notes need not.
    "remediation_add_note", "remediation_apply",
}
#: Writes that converge on retry.
_CONVERGES = {
    "session_renew", "end_session",
    # v2.461.0: a row already followed up on that day is left alone.
    "remediation_record_follow_up",
    "assist_set_follow", "assist_patch_host",
    # v2.442.0: guarded by ``expected_revision`` — a retry of an applied change
    # is refused (409) rather than applied twice.
    "host_tests_update",
}


def test_idempotent_hint_matches_retry_semantics():
    tools = {t["name"]: t["annotations"] for t in tool_list_payload()}
    missing = (_CREATES_A_ROW | _CONVERGES) - set(tools)
    assert not missing, f"registry lost tools this test expects: {missing}"
    for name in _CREATES_A_ROW:
        assert tools[name]["idempotentHint"] is False, name
    for name in _CONVERGES:
        assert tools[name]["idempotentHint"] is True, name
    # No additive tool may ever advertise itself as idempotent, whatever else
    # its spec says (submit_feedback is additive AND metadata_write).
    for name, spec in TOOLS.items():
        if spec.get("additive"):
            assert tools[name]["idempotentHint"] is False, name
    # And every write that is not classified above is declared explicitly, so a
    # new creating tool cannot inherit the converging default unnoticed.
    unclassified = {
        n for n, s in TOOLS.items()
        if s["method"] != "GET" and n not in _CREATES_A_ROW | _CONVERGES
    }
    assert not unclassified, f"classify these write tools' retry semantics: {unclassified}"


def test_destructive_hint_follows_what_a_write_replaces():
    """destructiveHint is the flag clients gate auto-approval on, and it is
    decided by whether the write REPLACES a stored value — never by the
    idempotency fix.  Until v2.442.0 this pinned create_test_plan and
    start_execution (non-additive creators); the writes that replace a value
    today are the host-test update and the two host corrections."""
    tools = {t["name"]: t["annotations"] for t in tool_list_payload()}
    for name in ("host_tests_update", "assist_set_follow", "assist_patch_host"):
        assert tools[name]["destructiveHint"] is True, name
        assert tools[name]["readOnlyHint"] is False, name
    # Appends are not destructive, and are still writes.
    for name in ("host_tests_propose", "record_evidence", "assist_add_note"):
        assert tools[name]["destructiveHint"] is False, name
        assert tools[name]["readOnlyHint"] is False, name
    # Every read is read-only: nothing else may be auto-approved as one.
    for name, spec in TOOLS.items():
        assert tools[name]["readOnlyHint"] is (spec["method"] == "GET"), name
