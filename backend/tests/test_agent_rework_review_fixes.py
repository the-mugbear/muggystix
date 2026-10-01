"""Regressions from the review of the agent rework (v2.433.1).

Three of that review's rules were about test plans and execution runs (a draft
with entries is never "interrupted"; a draft still being written belongs to
its drafting session; ending a session abandons its open runs).  Plans and runs
were removed in v2.442.0 and those rules with them.  What is still pinned here:

* Portfolio's open-session count is the agent sessions, and its open-task
  count is the host tests still to do — not every test ever proposed;
* End / Resume are offered only where the routes would accept them.
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models_agent import AgentSession
from app.db.models_host_tests import HostTest
from app.services.agent_session_service import (
    create_agent_session,
    end_agent_session,
    mint_session_key,
)


def _session_with_key(db, project, agent):
    session = create_agent_session(
        db, project_id=project.id, agent_id=agent.id, started_by_id=None,
    )
    key = mint_session_key(db, agent=agent, session=session)
    db.commit()
    return session, key


# ---------------------------------------------------------------------------
# Portfolio counts open agent sessions and the tests still to do
# ---------------------------------------------------------------------------

def test_open_sessions_count_agent_sessions(db_session, test_project, test_agent):
    """An agent that is only scanning and uploading proposes no test; it
    still counts as an open session.  An ended one does not."""
    from app.services.project_signals_service import project_signals

    _session_with_key(db_session, test_project, test_agent)
    ended, _k = _session_with_key(db_session, test_project, test_agent)
    end_agent_session(db_session, ended, reason="done")
    db_session.commit()

    signals = project_signals(db_session, [test_project], datetime.now(timezone.utc))
    assert signals[test_project.id].active_sessions == 1


def test_open_tasks_are_the_host_tests_still_to_do(db_session, test_project):
    """``open_tasks`` is the same "planned" definition the Hosts page uses:
    proposed or in progress.  A done or dismissed test is not open work, and
    another project's tests are not this project's."""
    from app.db.models_project import Project
    from app.services.project_signals_service import project_signals

    other = Project(name="Other signals project", slug="signals-other")
    db_session.add(other)
    db_session.flush()

    def _add(project, ip, status):
        host = models.Host(project_id=project.id, ip_address=ip, state="up")
        db_session.add(host)
        db_session.flush()
        key = str(uuid.uuid4())
        db_session.add(HostTest(
            project_id=project.id, host_id=host.id, tool="nmap", description="d",
            rationale="r", priority="medium", status=status, source="person",
            request_key=key, request_hash=key,
            dismissed_reason="not in this engagement" if status == "dismissed" else None,
        ))

    for i, status in enumerate(("proposed", "in_progress", "done", "dismissed")):
        _add(test_project, f"10.48.0.{i + 1}", status)
    _add(other, "10.48.1.1", "proposed")
    db_session.commit()

    signals = project_signals(db_session, [test_project, other], datetime.now(timezone.utc))
    assert signals[test_project.id].open_tasks == 2
    assert signals[other.id].open_tasks == 1
    # Nothing can be "blocked" any more: the field went with execution runs.
    assert not hasattr(signals[test_project.id], "blocked_sessions")


# ---------------------------------------------------------------------------
# End / Resume are offered only where the routes would accept them
# ---------------------------------------------------------------------------

def _session_row(client, project, session_id):
    body = client.get(f"/api/v1/projects/{project.id}/agent-sessions").json()
    return next(r for r in body["sessions"] if r["kind"] == "project" and r["id"] == session_id)


def test_resume_is_not_offered_past_the_sessions_lifetime(client, db_session, test_project):
    """The Resume route refuses (409) once the session is past its lifetime;
    the flag used to say yes anyway."""
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    sid = r.json()["agent_session_id"]
    assert _session_row(client, test_project, sid)["can_resume"] is True

    session = db_session.get(AgentSession, sid)
    session.started_at = datetime.now(timezone.utc) - timedelta(days=3650)
    db_session.commit()
    row = _session_row(client, test_project, sid)
    assert row["can_resume"] is False
    assert row["can_end"] is True  # ending is still possible, and useful
    resume = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/resume", json={})
    assert resume.status_code == 409


def test_an_owner_demoted_below_auditor_is_offered_neither(client, db_session, test_project, test_user):
    from app.db.models_auth import UserRole
    from app.db.models_project import ProjectMembership
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    sid = r.json()["agent_session_id"]

    if test_user.role == UserRole.ADMIN:
        test_user.role = UserRole.MEMBER  # a global admin counts as project admin
    membership = db_session.query(ProjectMembership).filter(
        ProjectMembership.project_id == test_project.id,
        ProjectMembership.user_id == test_user.id,
    ).first()
    if membership is None:
        db_session.add(ProjectMembership(project_id=test_project.id, user_id=test_user.id, role="viewer"))
    else:
        membership.role = "viewer"
    db_session.commit()

    row = _session_row(client, test_project, sid)
    assert row["can_end"] is False and row["can_resume"] is False
