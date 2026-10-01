"""Regressions pinned by the v2.338.0 review of the unified agent session.

Each test names the defect it guards against:

* ``/agent/identity`` describes the session and nothing else — the per-phase
  ids it once carried (``plan_id``, ``execution_session_id``, ``open_phases``,
  ``workflow_session_id``) are gone with the phases (v2.442.0);
* the assist-sessions review page reads activity state through the
  unified session row, which is the one that is actually written;
* an operator can end any project session, not only one started from the
  assist dialog — and what the session wrote stays;
* ``last_activity_at`` is debounced rather than rewritten on every call.

The execution-run findings of that review (H1: a run's writes belong to the
session that opened it; H5: the plan's status is re-read under the lock) went
with execution runs in v2.442.0.  Their successor — a test is project data,
its evidence is attributed to the session that recorded it — is pinned in
``test_agent_session_key_reach.py``.
"""
import uuid

from app.db import models
from app.db.models_agent import AgentSession
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord


def _hdr(key):
    return {"X-API-Key": key}


def _start_session(client, project, purpose="review"):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": purpose})
    assert r.status_code == 201, r.text
    return r.json()


def _base_session_id(db, started_id):
    """The session id IS the id the start returned (v2.449.0)."""
    return started_id


def _host(db, project, ip="10.0.0.5"):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


def _propose(client, key, host):
    r = client.post("/api/v1/agent/host-tests", headers=_hdr(key), json={"tests": [dict(
        request_key=str(uuid.uuid4()), host_id=host.id, tool="nmap",
        description="Service detection", rationale="Open ports, no versions",
    )]})
    assert r.status_code == 201, r.text
    return r.json()["items"][0]


# ---------------------------------------------------------------------------
# H3 — identity describes the session; there is no phase id to mis-fill
# ---------------------------------------------------------------------------

def test_identity_carries_the_session_and_no_phase_ids(client, test_project, db_session):
    body = _start_session(client, test_project)
    key = body["api_key"]
    session_id = _base_session_id(db_session, body["agent_session_id"])
    # Work in flight must not bring a phase block back.
    _propose(client, key, _host(db_session, test_project))

    ident = client.get("/api/v1/agent/identity", headers=_hdr(key)).json()
    assert ident["session_id"] == session_id
    assert ident["workflow"] == "project"
    assert ident["project_id"] == test_project.id
    for retired in ("plan_id", "execution_session_id", "open_phases", "workflow_session_id"):
        assert retired not in ident, retired


# ---------------------------------------------------------------------------
# H4 — the review page reads the columns that are written
# ---------------------------------------------------------------------------

def test_assist_sessions_page_reflects_activity(client, test_project, db_session):
    body = _start_session(client, test_project)
    key, assist_id = body["api_key"], body["agent_session_id"]
    assert client.get("/api/v1/agent/identity", headers=_hdr(key)).status_code == 200

    rows = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()["sessions"]
    mine = next(s for s in rows if s["id"] == assist_id)
    assert mine["last_activity_at"] is not None
    assert mine["purpose"] == "review"
    assert mine["call_count"] >= 1
    assert mine["connection"] == "curl" and mine["first_call_at"] is not None
    detail = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{assist_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["last_activity_at"] is not None


# ---------------------------------------------------------------------------
# H6 — any project session can be ended by its operator
# ---------------------------------------------------------------------------

def test_operator_can_end_a_session_and_its_work_stays(client, test_project, db_session):
    """Ending revokes the key and closes the session.  The tests it proposed
    and the evidence it recorded are project data: they are neither deleted
    nor closed, so a person or a later session carries them on."""
    body = _start_session(client, test_project)
    key = body["api_key"]
    session_id = _base_session_id(db_session, body["agent_session_id"])
    host = _host(db_session, test_project)
    test = _propose(client, key, host)
    client.patch(
        f"/api/v1/agent/host-tests/{test['id']}", headers=_hdr(key),
        json={"expected_revision": test["revision"], "status": "in_progress"},
    )
    r = client.post("/api/v1/agent/evidence", headers=_hdr(key), json={
        "host_id": host.id, "host_test_id": test["id"], "request_key": "before-end",
        "tool": "nmap", "outcome": "inconclusive", "summary": "Filtered",
    })
    assert r.status_code == 201, r.text

    listing = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    row = next(s for s in listing["sessions"] if s["kind"] == "project" and s["id"] == session_id)
    assert row["status"] == "active"

    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{session_id}/end")
    assert r.status_code == 204, r.text
    db_session.expire_all()
    assert client.get("/api/v1/agent/identity", headers=_hdr(key)).status_code == 401
    assert client.get("/api/v1/agent/host-tests", headers=_hdr(key)).status_code == 401

    kept = db_session.get(HostTest, test["id"])
    assert kept is not None and kept.status == "in_progress"
    assert kept.agent_session_id == session_id
    assert db_session.query(EvidenceRecord).filter(
        EvidenceRecord.host_test_id == test["id"]
    ).count() == 1
    # …and a person picks the test up where the agent left it.
    r = client.patch(
        f"/api/v1/projects/{test_project.id}/host-tests/{test['id']}",
        json={"expected_revision": kept.revision, "status": "done"},
    )
    assert r.status_code == 200, r.text

    # Idempotent-safe: a second end reports that nothing changed.
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{session_id}/end")
    assert r.status_code == 409
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/999999/end")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# R1 — last_activity_at is debounced
# ---------------------------------------------------------------------------

def test_last_activity_is_not_rewritten_on_every_call(client, test_project, db_session):
    body = _start_session(client, test_project)
    key = body["api_key"]
    session_id = _base_session_id(db_session, body["agent_session_id"])

    assert client.get("/api/v1/agent/identity", headers=_hdr(key)).status_code == 200
    db_session.expire_all()
    first = db_session.query(AgentSession.last_activity_at).filter(
        AgentSession.id == session_id
    ).scalar()
    assert first is not None

    assert client.get("/api/v1/agent/identity", headers=_hdr(key)).status_code == 200
    db_session.expire_all()
    second = db_session.query(AgentSession.last_activity_at).filter(
        AgentSession.id == session_id
    ).scalar()
    assert second == first, "a call inside the debounce window must not rewrite the row"
