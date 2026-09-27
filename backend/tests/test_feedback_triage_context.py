"""The feedback triage queue says who and where (v2.428.2).

A reviewer checks an agent's claims against what it actually did, so each row
carries the unified session it came from, the page that shows that session's
API calls, the call count, and the project and agent names — and the list is
the standard Paginated envelope with a project filter.
"""
from app.db.models_agent import AgentApiCall, AgentFeedback, AssistSession


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": "feedback"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"X-API-Key": body["api_key"]}, body["assist_session_id"]


def test_rows_link_to_the_session_and_its_calls(client, db_session, test_project):
    headers, assist_id = _start(client, test_project)
    # A couple of calls on the session, then feedback from it.
    assert client.get("/api/v1/agent/identity", headers=headers).status_code == 200
    r = client.post("/api/v1/agent/feedback", headers=headers, json={
        "source": "assist", "overall_rating": 4,
        "friction_notes": "context test",
        "api_critiques": [{"endpoint": "x", "issue": "y", "suggestion": "z"}],
    })
    assert r.status_code == 201, r.text
    fb = db_session.query(AgentFeedback).filter(AgentFeedback.friction_notes == "context test").one()
    assist = db_session.query(AssistSession).get(assist_id)
    assert fb.agent_session_id == assist.agent_session_id

    page = client.get(f"/api/v1/feedback/?project_id={test_project.id}").json()
    assert page["total"] >= 1 and page["has_more"] is False
    row = next(i for i in page["items"] if i["id"] == fb.id)
    assert row["agent_session_id"] == assist.agent_session_id
    assert row["session_page_id"] == assist_id
    assert row["project_name"] == test_project.name
    assert row["agent_name"]
    recorded = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.agent_session_id == assist.agent_session_id).count()
    )
    assert row["session_api_calls"] == recorded

    # Detail and PATCH carry the same context.
    one = client.get(f"/api/v1/feedback/{fb.id}").json()
    assert one["session_page_id"] == assist_id
    patched = client.patch(f"/api/v1/feedback/{fb.id}", json={"status": "new"}).json()
    assert patched["project_name"] == test_project.name

    stats = client.get("/api/v1/feedback/stats").json()
    assert stats["with_api_critiques"] >= 1


def test_project_filter_and_paging(client, db_session, test_project):
    for i in range(3):
        db_session.add(AgentFeedback(project_id=test_project.id, source="assist",
                                     status="new", friction_notes=f"p{i}"))
    db_session.commit()
    first = client.get(f"/api/v1/feedback/?project_id={test_project.id}&limit=2").json()
    assert first["total"] == 3 and len(first["items"]) == 2 and first["has_more"] is True
    other = client.get("/api/v1/feedback/?project_id=999999").json()
    assert other["total"] == 0 and other["items"] == []


def test_has_critiques_and_suggestions_filters_answer(client, db_session, test_project):
    """``json != json`` has no PostgreSQL operator: these filters used to 500."""
    db_session.add_all([
        AgentFeedback(project_id=test_project.id, source="assist", status="new",
                      api_critiques=[{"endpoint": "a", "issue": "b"}], tool_suggestions=[]),
        AgentFeedback(project_id=test_project.id, source="assist", status="new",
                      api_critiques=[], tool_suggestions=[{"name": "nuclei"}]),
    ])
    db_session.commit()
    base = f"/api/v1/feedback/?project_id={test_project.id}"
    crit = client.get(base + "&has_api_critiques=true")
    assert crit.status_code == 200, crit.text
    assert crit.json()["total"] == 1
    sugg = client.get(base + "&has_tool_suggestions=true")
    assert sugg.status_code == 200, sugg.text
    assert sugg.json()["total"] == 1
