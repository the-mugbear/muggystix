"""Which client, model and prompt produced an agent's work (v2.434.0).

The environment probe used to carry this; it is gone.  Now:

* the CLIENT (harness) comes from the MCP ``initialize`` handshake, where every
  MCP client names itself — or, for a curl agent, the first call's User-Agent;
* the PROMPT VERSION is recorded by the server, which issued the prompt;
* the MODEL is an optional self-report (``agent_model``) on the writes where it
  matters — registering a plan, opening a run, ending the session.  The
  session keeps the latest; a run snapshots it when it opens, so a session
  that switches models does not relabel earlier work.
"""
from app.db.models_agent import AgentSession, ExecutionSession, TestPlan
from app.services.agent_prompt_history import PROMPT_VERSION


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    return body["api_key"], body["agent_session_id"]


def _initialize(client, key, client_info):
    return client.post(
        "/api/v1/mcp",
        headers={"X-API-Key": key} if key else {},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": client_info,
        }},
    )


def _session(db, sid):
    db.expire_all()
    return db.get(AgentSession, sid)


def test_the_server_records_the_prompt_version_it_issued(client, db_session, test_project):
    _key, sid = _start(client, test_project)
    assert _session(db_session, sid).prompt_version == PROMPT_VERSION


def test_the_mcp_handshake_names_the_client(client, db_session, test_project):
    key, sid = _start(client, test_project)
    r = _initialize(client, key, {"name": "Visual Studio Code", "version": "1.99.0"})
    assert r.status_code == 200 and "result" in r.json(), r.text
    assert _session(db_session, sid).generated_by_tool == "Visual Studio Code 1.99.0"


def test_a_handshake_without_a_key_records_nothing_and_still_succeeds(client, db_session, test_project):
    _key, sid = _start(client, test_project)
    r = _initialize(client, None, {"name": "claude-code", "version": "2.1.0"})
    assert r.status_code == 200 and "result" in r.json(), r.text
    assert _session(db_session, sid).generated_by_tool is None


def test_a_curl_agent_is_named_by_its_user_agent_until_a_handshake_names_it(
    client, db_session, test_project,
):
    key, sid = _start(client, test_project)
    r = client.get("/api/v1/agent/identity", headers={"X-API-Key": key, "User-Agent": "curl/8.5.0"})
    assert r.status_code == 200, r.text
    assert _session(db_session, sid).generated_by_tool == "curl/8.5.0"

    # A later User-Agent never replaces it; a handshake does.
    client.get("/api/v1/agent/identity", headers={"X-API-Key": key, "User-Agent": "Wget/1.21"})
    assert _session(db_session, sid).generated_by_tool == "curl/8.5.0"
    _initialize(client, key, {"name": "codex", "version": "0.9"})
    assert _session(db_session, sid).generated_by_tool == "codex 0.9"


def test_the_mcp_loopbacks_default_user_agent_names_nothing(client, db_session, test_project):
    key, sid = _start(client, test_project)
    client.get("/api/v1/agent/identity", headers={"X-API-Key": key, "User-Agent": "python-httpx/0.27.0"})
    assert _session(db_session, sid).generated_by_tool is None


def test_the_model_is_reported_on_plan_run_and_end_and_a_run_keeps_its_own(
    client, db_session, test_project,
):
    from app.db import models
    from app.db.models_agent import TestPlanEntry

    key, sid = _start(client, test_project)
    hdr = {"X-API-Key": key}
    _initialize(client, key, {"name": "claude-code", "version": "2.1.0"})

    plan = client.post("/api/v1/agent/test-plans", headers=hdr,
                       json={"title": "p", "agent_model": "claude-opus-5-5"})
    assert plan.status_code == 201, plan.text
    plan_id = plan.json()["id"]
    db_session.expire_all()
    row = db_session.get(TestPlan, plan_id)
    assert (row.generated_by_model, row.generated_by_tool, row.prompt_version) == (
        "claude-opus-5-5", "claude-code 2.1.0", PROMPT_VERSION,
    )

    host = models.Host(project_id=test_project.id, ip_address="10.0.0.7", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(TestPlanEntry(
        test_plan_id=plan_id, host_id=host.id, priority="high", test_phase="enumeration",
        proposed_tests=[{"name": "t0", "command": "true"}], rationale="x",
    ))
    db_session.commit()

    # The operator switched models in the same session before opening the run.
    run = client.post("/api/v1/agent/execution-sessions/start", headers=hdr,
                      json={"plan_id": plan_id, "agent_model": "gpt-5.5"})
    assert run.status_code == 201, run.text
    run_id = run.json()["session_id"]
    db_session.expire_all()
    assert db_session.get(ExecutionSession, run_id).generated_by_model == "gpt-5.5"
    assert db_session.get(TestPlan, plan_id).generated_by_model == "claude-opus-5-5"
    assert _session(db_session, sid).generated_by_model == "gpt-5.5"

    client.post(f"/api/v1/agent/execution-sessions/{run_id}/complete", headers=hdr, json={})
    end = client.post("/api/v1/agent/session/end", headers=hdr,
                      json={"agent_model": "claude-sonnet-5-5"})
    assert end.status_code == 200, end.text
    assert _session(db_session, sid).generated_by_model == "claude-sonnet-5-5"
    # A run keeps the model it opened with.
    assert db_session.get(ExecutionSession, run_id).generated_by_model == "gpt-5.5"


def test_the_model_is_optional(client, db_session, test_project):
    key, sid = _start(client, test_project)
    r = client.post("/api/v1/agent/test-plans", headers={"X-API-Key": key}, json={"title": "p"})
    assert r.status_code == 201, r.text
    assert _session(db_session, sid).generated_by_model is None


def test_the_probe_route_is_gone(client, test_project):
    key, _sid = _start(client, test_project)
    r = client.post("/api/v1/agent/session/environment", headers={"X-API-Key": key},
                    json={"os_family": "linux"})
    assert r.status_code in (404, 405)
