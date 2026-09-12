"""End-to-end proof of the v2.337.0 unified agent session.

One project-scoped session + key does every kind of work — query, recon,
plan, execute — chosen by opening a phase rather than by minting a different
key.  This replaces the four per-workflow entry points (assist / recon /
plan generation / execution) whose isolation these tests' predecessors pinned.
"""
from app.db.models_agent import AgentSession, ReconSession, TestPlan


def _scope_with_subnet(db, project):
    from app.db import models
    scope = models.Scope(project_id=project.id, name="s1", description="")
    db.add(scope)
    db.flush()
    db.add(models.Subnet(scope_id=scope.id, cidr="10.0.0.0/24"))
    db.commit()
    return scope


def _start_session(client, project):
    """Mint a unified session key via the (repointed) assist-start entry point."""
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": "everything"})
    assert r.status_code == 201, r.text
    body = r.json()
    return body["api_key"], body["assist_session_id"]


def _hdr(key):
    return {"X-API-Key": key}


def test_one_key_reaches_identity_recon_and_plan(client, test_project, db_session):
    key, session_id = _start_session(client, test_project)

    # The session is a PROJECT session and the key resolves to it.
    row = db_session.query(AgentSession).filter(AgentSession.id == session_id).first()
    assert row is not None and row.workflow == "project"

    # identity: one key, project-scoped, no phase open yet.
    r = client.get("/api/v1/agent/identity", headers=_hdr(key))
    assert r.status_code == 200, r.text
    ident = r.json()
    assert ident["workflow"] == "project"
    assert ident["project_id"] == test_project.id
    assert ident["open_phases"]["active_recon_session_ids"] == []
    assert ident["can_write_project_data"] is True  # admin operator

    # environment probe on the session (one, not three).
    r = client.post(
        "/api/v1/agent/session/environment",
        headers=_hdr(key),
        json={"os_family": "linux", "shell": "bash"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["session_type"] == "session"

    # open a recon run — same key, no new credential.
    scope = _scope_with_subnet(db_session, test_project)
    r = client.post("/api/v1/agent/recon/start", headers=_hdr(key), json={"scope_id": scope.id})
    assert r.status_code == 201, r.text
    ctx = r.json()
    assert ctx["scope_id"] == scope.id
    assert "10.0.0.0/24" in ctx["scope_cidrs"]
    assert ctx["read_back"] and "10.0.0.0/24" in ctx["read_back"]
    recon_id = ctx["recon_session_id"]

    # the recon run is linked to the session, and the probe rode along.
    run = db_session.query(ReconSession).filter(ReconSession.id == recon_id).first()
    assert run.agent_session_id == session_id
    assert run.environment_probed_at is not None

    # recon context resolves the single active run without a param.
    r = client.get("/api/v1/agent/recon/context", headers=_hdr(key))
    assert r.status_code == 200 and r.json()["recon_session_id"] == recon_id

    # the SAME key drafts a plan.
    r = client.post("/api/v1/agent/test-plans", headers=_hdr(key), json={"title": "p1"})
    assert r.status_code == 201, r.text
    plan_id = r.json()["id"]
    plan = db_session.query(TestPlan).filter(TestPlan.id == plan_id).first()
    assert plan.agent_session_id == session_id


def test_execution_requires_an_approved_plan(client, test_project, db_session):
    """The human approval gate is the one control the consolidation keeps."""
    key, _ = _start_session(client, test_project)

    from app.db.models_agent import TestPlan, TestPlanEntry, TestPlanStatus
    from app.db import models
    host = models.Host(project_id=test_project.id, ip_address="10.0.0.5", state="up")
    db_session.add(host)
    db_session.flush()
    plan = TestPlan(project_id=test_project.id, version=1, title="draft", status=TestPlanStatus.DRAFT.value)
    db_session.add(plan)
    db_session.flush()
    db_session.add(TestPlanEntry(test_plan_id=plan.id, host_id=host.id, priority="high", test_phase="enumeration", proposed_tests=[], rationale="x"))
    db_session.commit()

    # A draft plan cannot be executed.
    r = client.post("/api/v1/agent/execution-sessions/start", headers=_hdr(key), json={"plan_id": plan.id})
    assert r.status_code == 409, r.text

    # Approve it, then the same key opens the run.
    plan.status = TestPlanStatus.APPROVED.value
    db_session.commit()
    r = client.post("/api/v1/agent/execution-sessions/start", headers=_hdr(key), json={"plan_id": plan.id})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["read_back"] and "10.0.0.5" in body["read_back"]
