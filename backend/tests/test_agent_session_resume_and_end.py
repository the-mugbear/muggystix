"""v2.340.0 — a session has an exit, and a dead-agent session has a way back.

Two defects with one cause. Nothing on the agent surface could end a project
session (recon / execution runs had ``/complete``; the session had nothing),
and the operator's only action on an active row was End. So a client that died
mid-tool — the common case, an editor agent session timing out while a scan
ran — left a session that showed ``active`` for a week and offered the
operator no way to reconnect the agent it had lost.

These pin:

* the agent can end its own session; ending closes nothing else — the host
  tests it proposed and the evidence it recorded are project data and stay
  (until v2.442.0 an end was refused, 409, while an execution run was open);
* the operator can resume an active session — same session id, rotated key,
  prompt with the resumed notice, MCP setup — owner only, active only, and
  never past the lifetime cap;
* the timeline row says when the key expires and until when the session can
  be resumed, so "active" stops meaning "a process is alive".
"""
from datetime import datetime, timedelta, timezone

from app.db.models_agent import AgentSession, AssistSession
from app.db.models_auth import APIKey, User


def _scope_with_subnet(db, project):
    from app.db import models
    scope = models.Scope(project_id=project.id, name="s1", description="")
    db.add(scope)
    db.flush()
    db.add(models.Subnet(scope_id=scope.id, cidr="10.0.0.0/24"))
    db.commit()
    return scope


def _start_session(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": "resume me"})
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


def _propose_and_run_a_test(client, db, project, key):
    """Propose one host test with the session's key and record evidence for it
    — the work a session leaves behind (it replaces the execution run)."""
    from app.db import models
    host = models.Host(project_id=project.id, ip_address="10.0.0.5", state="up")
    db.add(host)
    db.commit()
    r = client.post("/api/v1/agent/host-tests", headers=_hdr(key), json={"tests": [{
        "request_key": "resume-1", "host_id": host.id, "tool": "nmap",
        "description": "Version scan", "rationale": "open ports",
    }]})
    assert r.status_code == 201, r.text
    test_id = r.json()["items"][0]["id"]
    r = client.post("/api/v1/agent/evidence", headers=_hdr(key), json={
        "host_id": host.id, "host_test_id": test_id, "request_key": "resume-ev-1",
        "tool": "nmap", "outcome": "no_finding", "summary": "nothing unusual",
    })
    assert r.status_code == 201, r.text
    return test_id, r.json()["id"]


# ---------------------------------------------------------------------------
# Agent-side end
# ---------------------------------------------------------------------------

def test_agent_ends_its_own_session_and_the_key_dies_with_it(client, test_project, db_session):
    key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)

    r = client.post("/api/v1/agent/session/end", headers=_hdr(key), json={"notes": "all done"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == sid and body["status"] == "ended"

    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    assert row.status == "ended" and row.completed_at is not None
    assert "closed by the agent: all done" in (row.notes or "")
    # The assist detail row the review page is keyed on agrees.
    assist = db_session.query(AssistSession).filter(AssistSession.id == assist_id).first()
    assert str(getattr(assist.status, "value", assist.status)) == "ended"

    # Nothing further authenticates — the end was the last call.
    r = client.get("/api/v1/agent/identity", headers=_hdr(key))
    assert r.status_code == 401, r.text
    # And ending twice is a 409, not a silent 200.
    r = client.post("/api/v1/agent/session/end", headers=_hdr(key))
    assert r.status_code == 401


def test_agent_end_is_not_refused_by_unfinished_work_and_keeps_it(client, test_project, db_session):
    """v2.442.0 — there is no run to complete first.  A session with a test
    still ``proposed`` ends at once, and the test and its evidence stay, still
    attributed to the ended session, for a person or a later session."""
    from app.db.models_host_tests import HostTest
    from app.db.models_proposals import EvidenceRecord

    key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    test_id, evidence_id = _propose_and_run_a_test(client, db_session, test_project, key)

    r = client.post("/api/v1/agent/session/end", headers=_hdr(key), json={})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.query(AgentSession).filter(AgentSession.id == sid).first().status == "ended"
    test = db_session.get(HostTest, test_id)
    assert (test.status, test.agent_session_id) == ("proposed", sid)
    assert db_session.get(EvidenceRecord, evidence_id).agent_session_id == sid

    # A person can carry the test on after the session is gone.
    r = client.patch(
        f"/api/v1/projects/{test_project.id}/host-tests/{test_id}",
        json={"expected_revision": test.revision, "status": "in_progress"},
    )
    assert r.status_code == 200, r.text


def test_end_session_is_an_mcp_tool():
    from app.api.v1.endpoints.mcp_tools import TOOLS
    spec = TOOLS["end_session"]
    assert spec["method"] == "POST" and spec["path"] == "/api/v1/agent/session/end"
    assert spec["body_params"] == ["notes", "agent_model"]
    assert spec.get("metadata_write") is True


# ---------------------------------------------------------------------------
# Operator-side resume
# ---------------------------------------------------------------------------

def test_resume_rotates_the_key_on_the_same_session(client, test_project, db_session):
    old_key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    test_id, evidence_id = _propose_and_run_a_test(client, db_session, test_project, old_key)

    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/resume")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == sid
    assert body["project_id"] == test_project.id
    new_key = body["api_key"]
    assert new_key != old_key and new_key.startswith("nm_agent_")
    assert "RESUMED SESSION" in body["instructions"]
    assert new_key in body["instructions"]
    assert body["mcp_clients"], "the resume must hand back the MCP setup like start does"
    assert body["mcp_url"].endswith("/mcp")
    assert "active_execution_session_ids" not in body
    assert body["key_expires_at"] and body["renewable_until"]
    # The resumed agent is told where its earlier work is, so it continues
    # coverage instead of repeating it.
    assert f"/agent/host-tests?agent_session_id={sid}" in body["instructions"]
    assert f"/agent/evidence?agent_session_id={sid}" in body["instructions"]

    # Same session: the work it did is reachable with the new key through the
    # reads the prompt names; the old key is dead.
    assert client.get("/api/v1/agent/identity", headers=_hdr(new_key)).status_code == 200
    assert client.get("/api/v1/agent/identity", headers=_hdr(old_key)).status_code == 401
    tests = client.get("/api/v1/agent/host-tests", headers=_hdr(new_key),
                       params={"agent_session_id": sid}).json()
    assert [t["id"] for t in tests["items"]] == [test_id]
    ev = client.get("/api/v1/agent/evidence", headers=_hdr(new_key),
                    params={"agent_session_id": sid}).json()
    assert [e["id"] for e in ev["items"]] == [evidence_id]
    assert client.get("/api/v1/agent/evidence", headers=_hdr(new_key),
                      params={"agent_session_id": sid + 999}).json()["total"] == 0

    # Exactly one live key on the session, and the session record says why.
    live = (
        db_session.query(APIKey)
        .filter(APIKey.agent_session_id == sid, APIKey.is_active.is_(True))
        .count()
    )
    assert live == 1
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    assert row.status == "active"
    assert "Session resumed by" in (row.notes or "") and "key rotated" in row.notes


def test_resume_is_owner_only_and_active_only(client, test_project, db_session):
    _, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()

    # Someone else's session: the key would act under their name, so even the
    # admin client is refused (End is the admin's tool, not Resume).
    # Explicit id: the ``test_user`` fixture inserts id=1 by hand, so the
    # sequence has not advanced and a default-id insert collides with it.
    other = User(id=4242, username="other", email="other@example.com", hashed_password="x", role="analyst")
    db_session.add(other)
    db_session.flush()
    owner_id = row.started_by_id
    row.started_by_id = other.id
    db_session.commit()
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/resume")
    assert r.status_code == 403, r.text
    row.started_by_id = owner_id
    db_session.commit()

    # Ended session: nothing to reconnect to.
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/end")
    assert r.status_code == 204, r.text
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/resume")
    assert r.status_code == 409, r.text
    assert "start a new session" in r.json()["detail"]

    # Unknown session in this project.
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/999999/resume")
    assert r.status_code == 404


def test_resume_is_refused_past_the_lifetime_cap(client, test_project, db_session):
    from app.core.config import settings
    _, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    row.started_at = datetime.now(timezone.utc) - timedelta(
        hours=settings.AGENT_SESSION_MAX_LIFETIME_HOURS + 1
    )
    db_session.commit()

    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/resume")
    assert r.status_code == 409, r.text
    assert "maximum lifetime" in r.json()["detail"]
    # Still exactly one key, untouched — the refusal minted nothing.
    live = (
        db_session.query(APIKey)
        .filter(APIKey.agent_session_id == sid, APIKey.is_active.is_(True))
        .count()
    )
    assert live == 1


# ---------------------------------------------------------------------------
# Timeline row
# ---------------------------------------------------------------------------

def test_timeline_row_carries_key_expiry_and_renewal_deadline(client, test_project, db_session):
    key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)

    def _row():
        r = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions", params={"kind": "project"})
        assert r.status_code == 200, r.text
        return next(s for s in r.json()["sessions"] if s["id"] == sid)

    row = _row()
    assert row["status"] == "active"
    assert row["key_expires_at"] is not None
    assert row["renewable_until"] is not None
    exp = datetime.fromisoformat(row["key_expires_at"].replace("Z", "+00:00"))
    cap = datetime.fromisoformat(row["renewable_until"].replace("Z", "+00:00"))
    assert exp > datetime.now(timezone.utc)
    assert cap > exp

    # Key expired but the session is under its cap: still "active", and the
    # row says the key is gone — which is what makes it resumable, not dead.
    db_session.query(APIKey).filter(APIKey.agent_session_id == sid).update(
        {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=5)},
        synchronize_session=False,
    )
    db_session.commit()
    row = _row()
    assert row["status"] == "active"
    assert datetime.fromisoformat(row["key_expires_at"].replace("Z", "+00:00")) < datetime.now(timezone.utc)

    # Ended by the agent: no live key, so no expiry to report.
    db_session.query(APIKey).filter(APIKey.agent_session_id == sid).update(
        {"expires_at": datetime.now(timezone.utc) + timedelta(hours=1)},
        synchronize_session=False,
    )
    db_session.commit()
    assert client.post("/api/v1/agent/session/end", headers=_hdr(key)).status_code == 200
    row = _row()
    assert row["status"] == "ended" and row["key_expires_at"] is None
