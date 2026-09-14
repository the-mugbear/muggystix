"""v2.340.0 — a session has an exit, and a dead-agent session has a way back.

Two defects with one cause. Nothing on the agent surface could end a project
session (recon / execution phases had ``/complete``; the session had nothing),
and the operator's only action on an active row was End. So a client that died
mid-tool — the common case, an editor agent session timing out while a scan
ran — left a session that showed ``active`` for a week and offered the
operator no way to reconnect the agent it had lost.

These pin:

* the agent can end its own session, and is refused while a phase is open;
* the operator can resume an active session — same session id, rotated key,
  prompt with the resumed notice, MCP setup — owner only, active only, and
  never past the lifetime cap;
* the timeline row says when the key expires and until when the session can
  be resumed, so "active" stops meaning "a process is alive".
"""
from datetime import datetime, timedelta, timezone

from app.db.models_agent import AgentSession, AssistSession, ReconSession
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


def test_agent_end_is_refused_while_a_phase_is_open(client, test_project, db_session):
    key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    scope = _scope_with_subnet(db_session, test_project)
    r = client.post("/api/v1/agent/recon/start", headers=_hdr(key), json={"scope_id": scope.id})
    assert r.status_code == 201, r.text
    recon_id = r.json()["recon_session_id"]

    r = client.post("/api/v1/agent/session/end", headers=_hdr(key), json={})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["active_recon_session_ids"] == [recon_id]
    assert detail["active_execution_session_ids"] == []
    assert "/agent/recon/complete" in detail["message"]

    # Refusal changed nothing: the session is still active, the key still works,
    # the recon run is still active (not abandoned by an end underneath it).
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    assert row.status == "active"
    run = db_session.query(ReconSession).filter(ReconSession.id == recon_id).first()
    assert run.status == "active"
    assert client.get("/api/v1/agent/identity", headers=_hdr(key)).status_code == 200

    # Complete the phase, then the end goes through.
    r = client.post("/api/v1/agent/recon/complete", headers=_hdr(key), json={"notes": "done"})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/agent/session/end", headers=_hdr(key), json={})
    assert r.status_code == 200, r.text


def test_end_session_is_an_mcp_tool():
    from app.api.v1.endpoints.mcp_tools import TOOLS
    spec = TOOLS["end_session"]
    assert spec["method"] == "POST" and spec["path"] == "/api/v1/agent/session/end"
    assert spec["body_params"] == ["notes"]
    assert spec.get("metadata_write") is True


# ---------------------------------------------------------------------------
# Operator-side resume
# ---------------------------------------------------------------------------

def test_resume_rotates_the_key_on_the_same_session(client, test_project, db_session):
    old_key, assist_id = _start_session(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    scope = _scope_with_subnet(db_session, test_project)
    r = client.post("/api/v1/agent/recon/start", headers=_hdr(old_key), json={"scope_id": scope.id})
    assert r.status_code == 201, r.text
    recon_id = r.json()["recon_session_id"]

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
    assert body["active_recon_session_ids"] == [recon_id]
    assert body["key_expires_at"] and body["renewable_until"]

    # Same session, same open phase: the recon run still belongs to it and is
    # reachable with the new key; the old key is dead.
    run = db_session.query(ReconSession).filter(ReconSession.id == recon_id).first()
    assert run.agent_session_id == sid and run.status == "active"
    r = client.get("/api/v1/agent/recon/context", headers=_hdr(new_key))
    assert r.status_code == 200 and r.json()["recon_session_id"] == recon_id
    assert client.get("/api/v1/agent/identity", headers=_hdr(old_key)).status_code == 401

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
