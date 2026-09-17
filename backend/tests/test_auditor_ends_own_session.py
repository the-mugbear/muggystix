"""v2.343.2 — an auditor can end the session they started, from either UI route.

Found while reviewing the Agents guide: session start (``/assist/start``) and
resume admit AUDITOR, but both operator-side end routes required ANALYST, so
an auditor's only way out of their own session was to wait for it to lapse.
The owner-or-project-admin check inside each route is the real authorization;
the role floor only needs to admit whoever can start one.
"""
from app.db.models_agent import AgentSession, AssistSession
from app.db.models_auth import UserRole
from app.db.models_project import ProjectMembership, ProjectRole


def _make_auditor(db, project, user):
    user.role = UserRole.MEMBER
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=ProjectRole.AUDITOR.value))
    db.commit()


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": "auditor"})
    assert r.status_code == 201, r.text
    return r.json()["assist_session_id"]


def _agent_session_id(db, assist_id):
    return db.query(AssistSession.agent_session_id).filter(AssistSession.id == assist_id).scalar()


def test_auditor_ends_own_session_from_the_sessions_panel(client, db_session, test_project, test_user):
    _make_auditor(db_session, test_project, test_user)
    assist_id = _start(client, test_project)
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/sessions/{assist_id}/end")
    assert r.status_code == 204, r.text
    db_session.expire_all()
    sid = _agent_session_id(db_session, assist_id)
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    assert row.status == "ended" and row.end_reason == "operator"


def test_auditor_ends_own_session_from_agent_runs(client, db_session, test_project, test_user):
    _make_auditor(db_session, test_project, test_user)
    assist_id = _start(client, test_project)
    sid = _agent_session_id(db_session, assist_id)
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/end")
    assert r.status_code == 204, r.text
    db_session.expire_all()
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    assert row.status == "ended" and row.end_reason == "operator"
