"""A run can outlive the session that drove it (v2.402.0).

Agent Runs showed an execution run as ACTIVE, 15 days old, while its lead said
no session was still active. Both were true: the run's own status is only
changed by the agent that holds it, and that agent's session had lapsed. The
row now says which — ``session_live`` — so the table and the lead stop
contradicting each other. Workflow state, not evidence freshness.
"""
from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models_agent import (
    AgentSession,
    ReconSession,
    ReconSessionStatus,
)
from app.db.models_auth import APIKey


def _legacy_session(db, project, agent, user, *, status):
    s = AgentSession(
        workflow="recon",
        project_id=project.id,
        agent_id=agent.id,
        started_by_id=user.id,
        status=status,
        started_at=datetime.now(timezone.utc) - timedelta(days=15),
    )
    db.add(s)
    db.commit()
    return s


def _run(db, project, agent, user, session):
    scope = models.Scope(project_id=project.id, name="perimeter")
    db.add(scope)
    db.commit()
    run = ReconSession(
        project_id=project.id,
        scope_id=scope.id,
        agent_id=agent.id,
        started_by_id=user.id,
        status=ReconSessionStatus.ACTIVE,
        started_at=datetime.now(timezone.utc) - timedelta(days=15),
        agent_session_id=session.id if session is not None else None,
    )
    db.add(run)
    db.commit()
    return run


def _row(client, project, run_id):
    body = client.get(f"/api/v1/projects/{project.id}/agent-sessions").json()
    return next(r for r in body["sessions"] if r["kind"] == "recon" and r["id"] == run_id)


def test_an_active_run_whose_session_ended_says_so(
    client, db_session, test_project, test_agent, test_user
):
    session = _legacy_session(db_session, test_project, test_agent, test_user, status="ended")
    run = _run(db_session, test_project, test_agent, test_user, session)

    row = _row(client, test_project, run.id)
    assert row["status"] == "active"
    assert row["agent_session_id"] == session.id
    assert row["session_live"] is False


def test_an_active_run_with_a_live_key_is_live(
    client, db_session, test_project, test_agent, test_user
):
    session = _legacy_session(db_session, test_project, test_agent, test_user, status="active")
    db_session.add(
        APIKey(
            agent_id=test_agent.id,
            agent_session_id=session.id,
            name="k",
            key_hash="hash-live-run",
            key_prefix="nm_live",
            is_active=True,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
    )
    db_session.commit()
    run = _run(db_session, test_project, test_agent, test_user, session)

    assert _row(client, test_project, run.id)["session_live"] is True


def test_rows_carry_the_operators_full_name(
    client, db_session, test_project, test_agent, test_user
):
    session = _legacy_session(db_session, test_project, test_agent, test_user, status="ended")
    run = _run(db_session, test_project, test_agent, test_user, session)

    row = _row(client, test_project, run.id)
    assert row["user_full_name"] == "Test Admin"
    assert row["user_username"] == "test-admin"
