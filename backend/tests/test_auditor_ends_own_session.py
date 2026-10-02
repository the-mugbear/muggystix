"""v2.343.2 — an auditor can end the session they started.

Found while reviewing the Agents guide: session start (``/assist/start``) and
resume admit AUDITOR, but the operator-side end routes required ANALYST, so
an auditor's only way out of their own session was to wait for it to lapse.
The owner-or-project-admin check inside the route is the real authorization;
the role floor only needs to admit whoever can start one.

v2.449.0 — there is one End route (``/agent-sessions/{id}/end``).  The old
``/assist/sessions/{id}/end`` path calls the same handler, so the same floor
applies; it takes a session's old ``assist_sessions`` id, or — for a session
started since, which has none — the session's own id.
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


def test_auditor_ends_a_new_session_through_the_old_path(client, db_session, test_project, test_user):
    """A browser tab loaded before v2.449.0 starts a session, reads the start
    response's ``assist_session_id`` (now the session's own id) and ends it at
    the old path.  That was a 404, and the key stayed live."""
    _make_auditor(db_session, test_project, test_user)
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "old tab"})
    assert r.status_code == 201, r.text
    old_shape_id = r.json()["assist_session_id"]

    r = client.post(f"/api/v1/projects/{test_project.id}/assist/sessions/{old_shape_id}/end")
    assert r.status_code == 204, r.text
    db_session.expire_all()
    row = db_session.get(AgentSession, old_shape_id)
    assert row.legacy_assist_session_id is None
    assert row.status == "ended" and row.end_reason == "operator"
