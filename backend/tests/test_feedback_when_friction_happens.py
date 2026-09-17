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
* a phase completion says whether the session has filed feedback yet, with a
  hint when it has not, and is never refused over it;
* the activity summary's ``session_hygiene`` counts starts, exits by kind, and
  sessions that filed feedback;
* the prompt and the MCP tool descriptions carry the trigger rule.
"""
from datetime import datetime, timedelta, timezone

from app.db.models_agent import AgentSession, AssistSession
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
    return body["api_key"], body["assist_session_id"]


def _agent_session_id(db, assist_session_id):
    return (
        db.query(AssistSession.agent_session_id)
        .filter(AssistSession.id == assist_session_id).scalar()
    )


def _hdr(key):
    return {"X-API-Key": key}


def _file_feedback(client, key, note="assist_list_hosts has no total"):
    r = client.post(
        "/api/v1/agent/feedback", headers=_hdr(key),
        json={"source": "assist", "overall_rating": 3, "friction_notes": note},
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
# The checkpoint: phase completion says whether feedback was filed
# ---------------------------------------------------------------------------

def test_recon_complete_reports_missing_feedback_and_is_not_refused(client, test_project, db_session):
    key, _assist_id = _start_session(client, test_project)
    scope = _scope_with_subnet(db_session, test_project)
    r = client.post("/api/v1/agent/recon/start", headers=_hdr(key), json={"scope_id": scope.id})
    assert r.status_code == 201, r.text

    # Polled summary stays quiet — a nag on every poll would be noise.
    r = client.get("/api/v1/agent/recon/summary", headers=_hdr(key))
    assert r.status_code == 200, r.text
    assert r.json()["feedback_recorded"] is None

    # Completion with nothing filed: advisory flag + hint, still a 200.
    r = client.post("/api/v1/agent/recon/complete", headers=_hdr(key), json={"notes": "swept"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["feedback_recorded"] is False
    assert "/agent/feedback" in body["feedback_hint"]
    assert "submit_feedback" in body["feedback_hint"]


def test_recon_complete_acknowledges_feedback_already_filed(client, test_project, db_session):
    key, _assist_id = _start_session(client, test_project)
    scope = _scope_with_subnet(db_session, test_project)
    assert client.post(
        "/api/v1/agent/recon/start", headers=_hdr(key), json={"scope_id": scope.id},
    ).status_code == 201
    _file_feedback(client, key, note="recon/upload wanted multipart, guide showed json")
    r = client.post("/api/v1/agent/recon/complete", headers=_hdr(key), json={"notes": "swept"})
    assert r.status_code == 200, r.text
    assert r.json()["feedback_recorded"] is True
    assert r.json()["feedback_hint"] is None


def test_feedback_checkpoint_helper_handles_no_session():
    from app.services.agent_session_service import feedback_checkpoint
    assert feedback_checkpoint(None, None) == (None, None)


# ---------------------------------------------------------------------------
# The words the agent reads
# ---------------------------------------------------------------------------

def test_session_prompt_asks_for_feedback_at_the_moment_of_friction(client, test_project):
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    prompt = r.json()["instructions"]
    assert "file it when the friction happens" in prompt
    assert "trigger is an event, not the end of the session" in prompt
    assert "feedback_recorded" in prompt
    # The exit step no longer treats feedback as part of the ceremony.
    assert "if you have filed no feedback yet" in prompt
    assert "Before you finish, submit structured feedback" not in prompt


def test_mcp_tool_descriptions_carry_the_trigger_rule():
    from app.api.v1.endpoints.mcp_tools import TOOLS
    fb = TOOLS["submit_feedback"]["description"]
    assert "AT THE MOMENT you hit friction" in fb
    for name in ("recon_complete", "execution_complete_session"):
        assert "feedback_recorded" in TOOLS[name]["description"], name
    assert "file any feedback you have not filed yet first" in TOOLS["end_session"]["description"]


def test_assist_panel_end_is_an_operator_end_in_the_metrics(client, test_project, db_session):
    """Review (v2.343.1): the sessions-panel exit revoked the key and closed
    the assist row but left the AgentSession active with no end_reason — an
    ended session reported sessions_active: 1 and ended_by_operator: 0."""
    _key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/sessions/{assist_id}/end")
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
