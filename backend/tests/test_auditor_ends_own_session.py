"""v2.343.2 — an auditor can end the session they started.

Found while reviewing the Agents guide: session start (``/assist/start``) and
resume admit AUDITOR, but the operator-side end routes required ANALYST, so
an auditor's only way out of their own session was to wait for it to lapse.
The owner-or-project-admin check inside the route is the real authorization;
the role floor only needs to admit whoever can start one.

v2.449.0 — there is one End route (``/agent-sessions/{id}/end``).  The old
``/assist/sessions/{id}/end`` path, which called the same handler, is gone
(2.489.0).
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


def test_the_auditor_is_offered_the_end_the_route_accepts(client, db_session, test_project, test_user):
    """The row's ``can_end`` says what the one End route does for an auditor's
    own session.  (Two tests here drove the deprecated
    ``/assist/sessions/{id}/end`` path — by an old id, and by the id a
    pre-v2.449.0 browser tab read from the start response.  That path ended in
    2.489.0; the floor it shared is this route's, tested above.)"""
    _make_auditor(db_session, test_project, test_user)
    sid = _start(client, test_project)
    row = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}").json()
    assert row["can_end"] is True and row["operator_role"] == "auditor"
    assert client.post(
        f"/api/v1/projects/{test_project.id}/assist/sessions/{sid}/end"
    ).status_code == 404
    db_session.expire_all()
    assert db_session.get(AgentSession, sid).status == "active"
