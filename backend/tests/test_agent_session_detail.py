"""v2.432.0 — the agent session is a page of its own.

A consolidated session's recon / plan / execution rows are left off the
unified timeline (the session row represents them), and nothing else listed
them with the session — so an execution a session opened was reachable only
from /executions. The session row now carries its ``phases``; the detail read
``GET /agent-sessions/{id}`` returns that row; and the caller's End / Resume
rights come with it instead of being guessed from the global role.

The detail row (``assist_sessions``) has its own id sequence: session #72's
notes live under assist #52. The row names it, and the assist row names its
session back, so the two ids are never compared with each other again.
"""
from app.db import models
from app.db.models_agent import (
    AgentSession,
    AssistSession,
    ExecutionSession,
    ExecutionSessionStatus,
    TestPlan,
)
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole
from app.main import app
from app.api.v1.endpoints.auth import get_current_user


def _start(client, project, purpose="map the DMZ"):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": purpose})
    assert r.status_code == 201, r.text
    return r.json()["assist_session_id"]


def _session_id(db, assist_id):
    return db.query(AssistSession.agent_session_id).filter(AssistSession.id == assist_id).scalar()


def _open_work(db, project, agent, user, session_id):
    plan = TestPlan(
        project_id=project.id, title="SMB signing sweep", status="draft",
        agent_id=agent.id, created_by_user_id=user.id, agent_session_id=session_id,
    )
    db.add(plan)
    db.flush()
    run = ExecutionSession(
        test_plan_id=plan.id, agent_id=agent.id, started_by_id=user.id,
        status=ExecutionSessionStatus.PAUSED, agent_session_id=session_id,
    )
    db.add(run)
    db.commit()
    return plan, run


def test_the_detail_read_returns_the_sessions_work_and_detail_row(
    client, db_session, test_project, test_agent, test_user
):
    assist_id = _start(client, test_project)
    sid = _session_id(db_session, assist_id)
    plan, run = _open_work(db_session, test_project, test_agent, test_user, sid)

    r = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "project" and body["id"] == sid
    assert body["purpose"] == "map the DMZ"
    assert body["assist_session_id"] == assist_id
    by_kind = {p["kind"]: p for p in body["phases"]}
    assert by_kind["plan"]["id"] == plan.id and by_kind["plan"]["label"] == "SMB signing sweep"
    assert by_kind["plan"]["status"] == "draft"
    assert by_kind["execution"]["id"] == run.id
    assert by_kind["execution"]["test_plan_id"] == plan.id
    assert by_kind["execution"]["status"] == "paused"


def test_the_list_row_carries_the_same_phases(
    client, db_session, test_project, test_agent, test_user
):
    assist_id = _start(client, test_project)
    sid = _session_id(db_session, assist_id)
    _open_work(db_session, test_project, test_agent, test_user, sid)

    rows = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()["sessions"]
    row = next(r for r in rows if r["kind"] == "project" and r["id"] == sid)
    detail = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}").json()
    assert row["phases"] == detail["phases"]
    assert len(row["phases"]) == 2
    # The runs themselves stay off the timeline: the session row represents them.
    assert not [r for r in rows if r["kind"] == "execution" and r["agent_session_id"] == sid]


def test_the_owner_may_end_and_resume_their_active_session(client, db_session, test_project):
    sid = _session_id(db_session, _start(client, test_project))
    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}").json()
    assert body["can_end"] is True
    assert body["can_resume"] is True
    assert body["operator_role"] == "global_admin"


def test_a_project_admin_may_end_but_not_resume_anothers_session(
    client, db_session, test_project, test_user
):
    sid = _session_id(db_session, _start(client, test_project))
    other = User(
        id=2,  # the fixture user pins id 1 without advancing the sequence
        username="project-admin", email="pa@example.com", full_name="Project Admin",
        hashed_password="x", role=UserRole.MEMBER,
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=other.id, role=ProjectRole.ADMIN.value))
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: other
    try:
        body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}").json()
    finally:
        app.dependency_overrides[get_current_user] = lambda: test_user
    assert body["can_end"] is True
    assert body["can_resume"] is False


def test_an_ended_session_offers_neither(client, db_session, test_project):
    sid = _session_id(db_session, _start(client, test_project))
    assert client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/end").status_code == 204
    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}").json()
    assert body["status"] == "ended"
    assert body["can_end"] is False and body["can_resume"] is False


def test_a_legacy_row_or_another_projects_session_is_not_found(
    client, db_session, test_project, test_agent, test_user
):
    legacy = AgentSession(
        workflow="execution", project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=test_user.id, status="active",
    )
    db_session.add(legacy)
    db_session.commit()
    assert client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{legacy.id}").status_code == 404
    assert client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/999999").status_code == 404


def test_the_static_rollup_route_is_not_read_as_an_id(client, test_project):
    r = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/by-model-tool")
    assert r.status_code == 200, r.text
    assert "summary" in r.json()


def test_start_returns_the_session_id_beside_the_detail_rows(client, db_session, test_project):
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "ids"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["agent_session_id"] == _session_id(db_session, body["assist_session_id"])


def test_the_assist_row_names_its_session(client, db_session, test_project):
    assist_id = _start(client, test_project)
    sid = _session_id(db_session, assist_id)
    body = client.get(f"/api/v1/projects/{test_project.id}/assist/sessions/{assist_id}").json()
    assert body["agent_session_id"] == sid
