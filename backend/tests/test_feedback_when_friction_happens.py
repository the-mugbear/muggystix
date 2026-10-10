"""v2.343.0 — feedback is asked for when friction happens, and the exit is measured.

Dev data when this was written: 7 of 36 sessions had filed feedback and the
API log held one agent-side end call.  The feedback ask hung off the session
end, and the session end is the step that does not happen — sessions lapse or
the operator ends them.  Rather than asking more often (per request would be
noise the reader of this feedback pays for), the ask moves to the moments that
do occur, and the exit is finally counted so a prompt change can be evaluated.

These pin:

* ``end_reason`` is set by each of the three end paths — agent, operator, sweep;
* the timeline row carries ``end_reason`` and ``feedback_count``;
* feedback is attributed to the session from the key alone, and carries no
  label for the kind of work it is about: no field is required, and ``source``
  is not a field (v2.480.0) — over HTTP an unknown key is ignored, over MCP an
  argument the tool does not have is refused;
* the activity summary's ``session_hygiene`` counts starts, exits by kind, and
  sessions that filed feedback;
* the prompt and the MCP tool descriptions carry the trigger rule.
"""
from datetime import datetime, timedelta, timezone

from app.db.models_agent import AgentSession
from app.db.models_auth import APIKey


def _scope_with_subnet(db, project):
    from app.db import models
    scope = models.Scope(project_id=project.id, name="s1", description="")
    db.add(scope)
    db.flush()
    db.add(models.Subnet(scope_id=scope.id, cidr="10.0.0.0/24"))
    db.commit()
    return scope


def _start_session(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": "feedback"})
    assert r.status_code == 201, r.text
    body = r.json()
    return body["api_key"], body["agent_session_id"]


def _agent_session_id(db, session_id):
    """The session id IS the id the start returned (v2.449.0); until then the
    start returned a pointer row's id and this looked the session up."""
    return session_id


def _hdr(key):
    return {"X-API-Key": key}


def _file_feedback(client, key, note="assist_list_hosts has no total"):
    r = client.post(
        "/api/v1/agent/feedback", headers=_hdr(key),
        json={"overall_rating": 3, "friction_notes": note},
    )
    assert r.status_code in (200, 201), r.text


def _row(client, project, sid):
    r = client.get(f"/api/v1/projects/{project.id}/agent-sessions", params={"kind": "project"})
    assert r.status_code == 200, r.text
    return next(s for s in r.json()["sessions"] if s["id"] == sid)


# ---------------------------------------------------------------------------
# end_reason — one value per end path
# ---------------------------------------------------------------------------

def test_agent_end_records_end_reason_agent(client, test_project, db_session):
    key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    assert client.post("/api/v1/agent/session/end", headers=_hdr(key), json={}).status_code == 200
    db_session.expire_all()
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    assert row.status == "ended"
    assert row.end_reason == "agent"


def test_operator_end_records_end_reason_operator(client, test_project, db_session):
    _key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/end")
    assert r.status_code == 204, r.text
    db_session.expire_all()
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    assert row.end_reason == "operator"


def test_sweep_records_end_reason_lapsed(client, test_project, db_session):
    from app.services.agent_session_service import lapse_expired_agent_sessions
    _key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    # Key expired AND the session past its lifetime cap: the sweep's case.
    long_ago = datetime.now(timezone.utc) - timedelta(days=30)
    db_session.query(APIKey).filter(APIKey.agent_session_id == sid).update(
        {"expires_at": long_ago}, synchronize_session=False,
    )
    db_session.query(AgentSession).filter(AgentSession.id == sid).update(
        {"started_at": long_ago}, synchronize_session=False,
    )
    db_session.commit()
    assert lapse_expired_agent_sessions(db_session) >= 1
    db_session.expire_all()
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    assert row.status == "ended"
    assert row.end_reason == "lapsed"


# ---------------------------------------------------------------------------
# The timeline row and the hygiene summary
# ---------------------------------------------------------------------------

def test_timeline_row_carries_end_reason_and_feedback_count(client, test_project, db_session):
    key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)

    row = _row(client, test_project, sid)
    assert row["end_reason"] is None
    assert row["feedback_count"] == 0

    _file_feedback(client, key)
    _file_feedback(client, key, note="second item")
    assert client.post("/api/v1/agent/session/end", headers=_hdr(key), json={}).status_code == 200

    row = _row(client, test_project, sid)
    assert row["status"] == "ended"
    assert row["end_reason"] == "agent"
    assert row["feedback_count"] == 2


def test_activity_summary_counts_session_hygiene(client, test_project, db_session):
    # One session that files feedback and ends itself; one the operator ends
    # with nothing filed.  Both started inside the window.
    key_a, assist_a = _start_session(client, test_project)
    sid_a = _agent_session_id(db_session, assist_a)
    _file_feedback(client, key_a)
    assert client.post("/api/v1/agent/session/end", headers=_hdr(key_a), json={}).status_code == 200

    _key_b, assist_b = _start_session(client, test_project)
    sid_b = _agent_session_id(db_session, assist_b)
    assert client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid_b}/end").status_code == 204

    r = client.get(f"/api/v1/projects/{test_project.id}/agent-activity/summary")
    assert r.status_code == 200, r.text
    h = r.json()["session_hygiene"]
    assert h["sessions_started"] == 2
    assert h["sessions_active"] == 0
    assert h["sessions_ended"] == 2
    assert h["ended_by_agent"] == 1
    assert h["ended_by_operator"] == 1
    assert h["lapsed"] == 0
    assert h["sessions_with_feedback"] == 1
    assert {sid_a, sid_b} == {sid_a, sid_b}  # both sessions are the ones counted


# ---------------------------------------------------------------------------
# What a feedback row is about
#
# (v2.343.0 also had a "checkpoint": an execution run's ``/complete`` answered
# ``feedback_recorded`` / ``feedback_hint``.  Runs went in v2.442.0 and the
# checkpoint with them — there is no completion step left to hang it on.  The
# trigger rule in the prompt and the tool text, pinned below, is what remains.)
# ---------------------------------------------------------------------------

def test_testing_feedback_is_accepted_and_attributed_from_the_key(client, test_project, db_session):
    from app.db.models_agent import AgentFeedback

    key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    r = client.post("/api/v1/agent/feedback", headers=_hdr(key), json={
        "overall_rating": 2,
        "friction_notes": "host_tests_propose 409 did not say which request_key",
    })
    assert r.status_code in (200, 201), r.text
    body = r.json()
    for retired in ("test_plan_id", "execution_session_id", "source"):
        assert retired not in body
    # An acknowledgement, not an echo: the agent just wrote the text.
    assert "friction_notes" not in body and "api_critiques" not in body
    assert body["friction_notes_chars"] == len("host_tests_propose 409 did not say which request_key")
    assert body["status"] == "new" and body["agent_session_id"] == sid
    row = db_session.get(AgentFeedback, body["id"])
    assert (row.agent_session_id, row.project_id) == (sid, test_project.id)
    assert _row(client, test_project, sid)["feedback_count"] == 1


def _mcp_call(client, key, arguments, rpc_id=1):
    return client.post("/api/v1/mcp", headers=_hdr(key), json={
        "jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
        "params": {"name": "submit_feedback", "arguments": arguments},
    }).json()


def test_feedback_takes_no_source(client, test_project, db_session):
    """v2.480.0 — the label for the kind of work an entry was about is gone.

    Before, ``source`` was required (a body without it was a 422) and had to be
    one of a fixed list.  Now nothing reads it and nothing stores it: a body
    without it files.  Over plain HTTP the body model ignores a key it does
    not know, ``source`` like any other; the MCP tool does not have the
    argument, so a call carrying it is refused as an unknown argument."""
    from app.api.v1.endpoints.mcp_tools import TOOLS
    from app.db.models_agent import AgentFeedback

    key, sid = _start_session(client, test_project)

    # Over HTTP: with none, and with a key the body model does not know.
    r = client.post("/api/v1/agent/feedback", headers=_hdr(key),
                    json={"friction_notes": "no label at all"})
    assert r.status_code == 201, r.text
    assert "source" not in r.json()
    for unknown in ("source", "never_a_field"):
        r = client.post("/api/v1/agent/feedback", headers=_hdr(key),
                        json={unknown: "made_up", "friction_notes": f"sent {unknown}"})
        assert r.status_code == 201, (unknown, r.text)
        assert unknown not in r.json(), unknown
    # Nothing at all is a complete submission too: no field is required.
    assert client.post("/api/v1/agent/feedback", headers=_hdr(key), json={}).status_code == 201

    # Over MCP: the tool does not have the argument …
    spec = TOOLS["submit_feedback"]
    schema = spec["input_schema"]
    assert "source" not in schema["properties"]
    assert "source" not in schema.get("required", ())
    assert "retired_params" not in spec
    assert "`source`" not in spec["description"]
    body = _mcp_call(client, key, {"friction_notes": "mcp, no label"}, 1)
    assert "error" not in body and body["result"]["isError"] is False, body
    # … so one that carries it is refused like any unknown argument.
    refused = _mcp_call(client, key, {"source": "made_up", "friction_notes": "mcp, a label"}, 2)
    assert refused.get("error", {}).get("code") == -32602, refused

    # Every accepted one is a row on the key's session, and no row has a label.
    db_session.expire_all()
    rows = db_session.query(AgentFeedback).filter(AgentFeedback.agent_session_id == sid).all()
    assert len(rows) == 5
    assert not hasattr(AgentFeedback, "source")
    assert "source" not in AgentFeedback.__table__.columns
    assert "idx_agent_feedback_source" not in {i.name for i in AgentFeedback.__table__.indexes}


def test_the_admin_feedback_reads_carry_no_source(client, test_project, db_session):
    """The triage queue's list, detail and summary lost the label with the
    column: no ``source`` on a row, no ``by_source`` in the summary, and no
    ``source`` query parameter on the list."""
    from app.main import app

    key, _sid = _start_session(client, test_project)
    _file_feedback(client, key)
    page = client.get(f"/api/v1/feedback/?project_id={test_project.id}").json()
    assert page["total"] == 1 and "source" not in page["items"][0]
    assert "source" not in client.get(f"/api/v1/feedback/{page['items'][0]['id']}").json()
    assert "by_source" not in client.get("/api/v1/feedback/stats").json()
    listed = app.openapi()["paths"]["/api/v1/feedback/"]["get"].get("parameters", [])
    assert "source" not in {p["name"] for p in listed}


# ---------------------------------------------------------------------------
# The words the agent reads
# ---------------------------------------------------------------------------

def test_session_prompt_asks_for_feedback_at_the_moment_of_friction(client, test_project):
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    prompt = r.json()["instructions"]
    assert "file it when the friction happens" in prompt
    assert "trigger is an event, not the end of the session" in prompt
    # No checkpoint is advertised that no longer exists.
    assert "feedback_recorded" not in prompt
    assert "execution-sessions" not in prompt
    # The body is valid JSON and files as it stands; it names no kind of work.
    assert "`source`" not in prompt and "reconnaissance" not in prompt.split(
        "### Feedback", 1)[1].split("###", 1)[0]
    import json
    body = prompt.split("```json\n", 1)[1].split("\n```", 1)[0]
    sent = json.loads(body)
    assert set(sent) == {"prompt_version", "friction_notes"}
    assert client.post(
        "/api/v1/agent/feedback", headers={"X-API-Key": r.json()["api_key"]}, json=sent,
    ).status_code == 201
    # The long payload, the metrics and the tool suggestions are the guide's.
    for moved in ("agent_metrics", "tool_suggestions", "overall_rating"):
        assert moved not in prompt
    # The exit step no longer treats feedback as part of the ceremony.
    assert "if you have filed no feedback yet" in prompt
    assert "Before you finish, submit structured feedback" not in prompt


def test_mcp_tool_descriptions_carry_the_trigger_rule():
    from app.api.v1.endpoints.mcp_tools import TOOLS
    fb = TOOLS["submit_feedback"]["description"]
    assert "AT THE MOMENT you hit friction" in fb
    assert "execution_complete_session" not in fb and "feedback_recorded" not in fb
    assert "source" not in TOOLS["submit_feedback"]["input_schema"]["properties"]
    assert "file any feedback you have not filed yet first" in TOOLS["end_session"]["description"]


def test_assist_panel_end_is_an_operator_end_in_the_metrics(client, test_project, db_session):
    """Review (v2.343.1): the sessions-panel exit revoked the key and closed
    the assist row but left the AgentSession active with no end_reason — an
    ended session reported sessions_active: 1 and ended_by_operator: 0."""
    _key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/end")
    assert r.status_code == 204, r.text

    db_session.expire_all()
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    assert row.status == "ended"
    assert row.end_reason == "operator"
    assert db_session.query(APIKey).filter(
        APIKey.agent_session_id == sid, APIKey.is_active.is_(True),
    ).count() == 0

    h = client.get(f"/api/v1/projects/{test_project.id}/agent-activity/summary").json()["session_hygiene"]
    assert h["sessions_started"] == 1
    assert h["sessions_active"] == 0
    assert h["ended_by_operator"] == 1
    # And the timeline agrees.
    assert _row(client, test_project, sid)["end_reason"] == "operator"


def test_prompt_version_bumped_for_the_feedback_rule():
    from app.services.agent_prompt_service import PROMPT_VERSION
    assert tuple(int(p) for p in PROMPT_VERSION.split(".")) >= (2, 5, 0)
