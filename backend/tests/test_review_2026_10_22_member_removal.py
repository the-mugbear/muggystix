"""Review 2026-10-22 (nitpick) — removing a member ends their agents there.

A session renews on its operator being active alone
(``deps.authenticate_for_renewal``), so a removed member's session stayed
renewable, and re-adding them within the session's life revived the old key.
``remove_member`` now ends the member's sessions in THAT project, through the
same step a password reset takes (``end_sessions_of_operator``); their sessions
in other projects are theirs to keep.
"""
from app.db.models_agent import AgentSession
from app.db.models_project import Project, ProjectMembership, ProjectRole
from tests.test_review_2026_10_10_agent_accounts import _analyst, _identity, _renew, _session_key


def test_removing_a_member_ends_their_agent_sessions_in_that_project_only(
    client, db_session, test_project,
):
    user = _analyst(db_session, test_project, user_id=94201)
    other = Project(name="elsewhere-94201", slug="elsewhere-94201")
    db_session.add(other)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=other.id, user_id=user.id, role=ProjectRole.ANALYST.value))
    db_session.commit()
    raw, session = _session_key(db_session, test_project, user)
    raw_other, session_other = _session_key(db_session, other, user)
    assert _identity(client, raw).status_code == 200

    removed = client.delete(f"/api/v1/projects/{test_project.id}/members/{user.id}")
    assert removed.status_code == 200, removed.text

    db_session.expire_all()
    ended = db_session.get(AgentSession, session.id)
    assert ended.status == "ended"
    assert "removed from the project" in ended.notes
    assert _renew(client, raw).status_code == 401           # was 200

    # Re-adding the member brings the old key back to nothing.
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=user.id, role=ProjectRole.ANALYST.value))
    db_session.commit()
    assert _identity(client, raw).status_code == 401        # was 200

    # Their session in the other project is untouched.
    assert db_session.get(AgentSession, session_other.id).status == "active"
    assert _identity(client, raw_other).status_code == 200
