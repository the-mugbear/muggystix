"""An /agent/ path that is not an endpoint (v2.444.0).

Seen on the test host: an agent guessed ``/agent/recon/context``, got the
framework's bare ``{"detail": "Not Found"}``, took it for its own mistake and
filed no feedback — and the server had no record that it had happened.
"""
from app.db.models_agent import AgentApiCall


def _key(client, project):
    response = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert response.status_code == 201, response.text
    return response.json()


def test_the_404_says_what_to_read_and_where_to_report_it(client, db_session, test_project):
    started = _key(client, test_project)
    response = client.get("/api/v1/agent/recon/context", headers={"X-API-Key": started["api_key"]})
    assert response.status_code == 404
    body = response.json()
    assert body["detail"] == "No agent endpoint at GET /api/v1/agent/recon/context."
    assert "submit_feedback" in body["hint"] and "agents-guide" in body["hint"]
    assert body["guide"] == "/api/v1/agents-guide" and body["feedback"] == "/api/v1/agent/feedback"


def test_it_is_recorded_against_the_session_with_no_route_template(client, db_session, test_project):
    started = _key(client, test_project)
    # Either header carries the key (the agent on the test host used Bearer).
    client.get("/api/v1/agent/recon/context", headers={"Authorization": f"Bearer {started['api_key']}"})
    db_session.expire_all()
    row = db_session.query(AgentApiCall).filter(AgentApiCall.path == "/api/v1/agent/recon/context").one()
    assert (row.status_code, row.path_template, row.project_id) == (404, None, test_project.id)
    session_id = client.get("/api/v1/agent/identity", headers={"X-API-Key": started["api_key"]}).json()["session_id"]
    assert row.agent_session_id == session_id
    # … and the session's API activity shows it.
    activity = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions/{session_id}/api-activity",
        params={"mine": False},
    ).json()
    assert any(i["path"] == "/api/v1/agent/recon/context" and i["status_code"] == 404 for i in activity["items"])


def test_an_anonymous_or_made_up_key_leaves_no_row(client, db_session):
    for headers in ({}, {"X-API-Key": "nm_agent_not-a-real-key"}):
        response = client.get("/api/v1/agent/recon/context", headers=headers)
        assert response.status_code == 404 and "hint" in response.json()
    assert db_session.query(AgentApiCall).count() == 0


def test_a_missing_object_on_a_real_route_keeps_its_own_404(client, db_session, test_project):
    started = _key(client, test_project)
    response = client.get("/api/v1/agent/host-tests/999999", headers={"X-API-Key": started["api_key"]})
    assert response.status_code == 404
    assert response.json() == {"detail": "Host test not found in this project"}


def test_the_mcp_opening_instructions_ask_for_feedback_when_it_happens(client):
    response = client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    text = response.json()["result"]["instructions"]
    assert "submit_feedback" in text and "right then" in text
    assert "test plan and execute" not in text
