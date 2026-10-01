"""v2.343.2 — an auditor can end the session they started.

Found while reviewing the Agents guide: session start (``/assist/start``) and
resume admit AUDITOR, but the operator-side end routes required ANALYST, so
an auditor's only way out of their own session was to wait for it to lapse.
The owner-or-project-admin check inside the route is the real authorization;
the role floor only needs to admit whoever can start one.

v2.449.0 — there is one End route (``/agent-sessions/{id}/end``).  The old
``/assist/sessions/{id}/end`` path answers only for a session that had an
``assist_sessions`` id, by calling the same handler, so the same floor applies.
"""
from app.db.models_agent import AgentSession
from app.db.models_auth import UserRole
from app.db.models_project import ProjectMembership, ProjectRole


def _make_auditor(db, project, user):
    user.role = UserRole.MEMBER
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=ProjectRole.AUDITOR.value))
    db.commit()


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": "auditor"})
    assert r.status_code == 201, r.text
    return r.json()["agent_session_id"]


def test_auditor_ends_own_session(client, db_session, test_project, test_user):
    _make_auditor(db_session, test_project, test_user)
    sid = _start(client, test_project)
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/end")
    assert r.status_code == 204, r.text
    db_session.expire_all()
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    assert row.status == "ended" and row.end_reason == "operator"


def test_auditor_ends_own_session_through_an_old_link(client, db_session, test_project, test_user):
    """A session from before v2.449.0, addressed by the id its pointer row had."""
    _make_auditor(db_session, test_project, test_user)
    sid = _start(client, test_project)
    db_session.query(AgentSession).filter(AgentSession.id == sid).update(
        {"legacy_assist_session_id": sid + 5000}
    )
    db_session.commit()

    r = client.post(f"/api/v1/projects/{test_project.id}/assist/sessions/{sid + 5000}/end")
    assert r.status_code == 204, r.text
    db_session.expire_all()
    row = db_session.query(AgentSession).filter(AgentSession.id == sid).first()
    assert row.status == "ended" and row.end_reason == "operator"
    # The session's own id is not an old id: that path does not take it.
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/sessions/{sid}/end")
    assert r.status_code == 404
