"""v2.432.0 — the agent session is a page of its own.

The session row says what the session did; the detail read
``GET /agent-sessions/{id}`` returns that row; and the caller's End / Resume
rights come with it instead of being guessed from the global role.

v2.442.0 — a session no longer opens "phases" (plans, execution runs): the row
carries ``host_test_count`` and ``evidence_count``, the host tests it proposed
and the evidence records it wrote, counted for THAT session only.

The detail row (``assist_sessions``) has its own id sequence: session #72's
notes live under assist #52. The row names it, and the assist row names its
session back, so the two ids are never compared with each other again.
"""
from app.db import models
from app.db.models_agent import AgentSession, AssistSession
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord
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


def _do_work(db, project, user, session_id, *, tests=2, evidence=3, ip="10.71.0.1"):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    for i in range(tests):
        db.add(HostTest(
            project_id=project.id, host_id=host.id, tool="nxc", description=f"t{i}", rationale="r",
            priority="medium", status="proposed", source="agent", agent_session_id=session_id,
            created_by_user_id=user.id, request_key=f"detail-{session_id}-{i}", request_hash="0" * 64,
        ))
    for i in range(evidence):
        db.add(EvidenceRecord(
            project_id=project.id, host_id=host.id, tool="nxc", outcome="info",
            summary=f"e{i}", agent_session_id=session_id,
        ))
    db.commit()


def test_the_detail_read_returns_the_sessions_work_and_detail_row(
    client, db_session, test_project, test_user
):
    assist_id = _start(client, test_project)
    sid = _session_id(db_session, assist_id)
    other_sid = _session_id(db_session, _start(client, test_project, purpose="another"))
    _do_work(db_session, test_project, test_user, sid, tests=2, evidence=3)
    # Another session's work, and a person's, are not this session's.
    _do_work(db_session, test_project, test_user, other_sid, tests=5, evidence=1, ip="10.71.0.2")
    _do_work(db_session, test_project, test_user, None, tests=1, evidence=1, ip="10.71.0.3")

    r = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "project" and body["id"] == sid
    assert body["purpose"] == "map the DMZ"
    assert body["assist_session_id"] == assist_id
    assert (body["host_test_count"], body["evidence_count"]) == (2, 3)
    for retired in ("phases", "test_plan_id", "target_label"):
        assert retired not in body


def test_the_list_row_carries_the_same_counts(client, db_session, test_project, test_user):
    assist_id = _start(client, test_project)
    sid = _session_id(db_session, assist_id)
    idle = _session_id(db_session, _start(client, test_project, purpose="idle"))
    _do_work(db_session, test_project, test_user, sid, tests=4, evidence=1)

    rows = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()["sessions"]
    by_id = {r["id"]: r for r in rows if r["kind"] == "project"}
    detail = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}").json()
    assert (by_id[sid]["host_test_count"], by_id[sid]["evidence_count"]) == (4, 1)
    assert (detail["host_test_count"], detail["evidence_count"]) == (4, 1)
    assert (by_id[idle]["host_test_count"], by_id[idle]["evidence_count"]) == (0, 0)
    # The retired run kinds are not timeline rows, and cannot be asked for.
    assert {r["kind"] for r in rows} <= {"project", "assist"}
    assert client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions", params={"kind": "execution"},
    ).status_code == 422


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
