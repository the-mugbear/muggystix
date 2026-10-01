"""A detail row can outlive the session that drove it (v2.402.0).

Agent Runs showed a run as ACTIVE, 15 days old, while its lead said no session
was still active.  Both were true: the row's own status is only changed by the
agent that holds it, and that agent's session had lapsed.  The row says which —
``session_live`` — so the table and the lead stop contradicting each other.
Workflow state, not evidence freshness.

The rows this was written for were execution runs, removed in v2.442.0.  The
same rule still decides the one legacy kind left on the timeline: an assist
row whose parent session is not a project session.
"""
from datetime import datetime, timedelta, timezone

from app.db.models_agent import AgentSession, AssistSession, AssistSessionStatus
from app.db.models_auth import APIKey


def _legacy_session(db, project, agent, user, *, status):
    s = AgentSession(
        workflow="assist",
        project_id=project.id,
        agent_id=agent.id,
        started_by_id=user.id,
        status=status,
        started_at=datetime.now(timezone.utc) - timedelta(days=15),
    )
    db.add(s)
    db.commit()
    return s


def _assist_row(db, project, agent, user, session):
    row = AssistSession(
        project_id=project.id,
        agent_id=agent.id,
        started_by_id=user.id,
        status=AssistSessionStatus.ACTIVE,
        started_at=datetime.now(timezone.utc) - timedelta(days=15),
        agent_session_id=session.id,
        purpose="legacy assist",
    )
    db.add(row)
    db.commit()
    return row


def _row(client, project, row_id):
    body = client.get(f"/api/v1/projects/{project.id}/agent-sessions").json()
    return next(r for r in body["sessions"] if r["kind"] == "assist" and r["id"] == row_id)


def test_an_active_row_whose_session_ended_says_so(
    client, db_session, test_project, test_agent, test_user
):
    session = _legacy_session(db_session, test_project, test_agent, test_user, status="ended")
    assist = _assist_row(db_session, test_project, test_agent, test_user, session)

    row = _row(client, test_project, assist.id)
    assert row["status"] == "active"
    assert row["agent_session_id"] == session.id
    assert row["session_live"] is False


def test_an_active_row_with_a_live_key_is_live(
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
    assist = _assist_row(db_session, test_project, test_agent, test_user, session)

    assert _row(client, test_project, assist.id)["session_live"] is True


def test_rows_carry_the_operators_full_name(
    client, db_session, test_project, test_agent, test_user
):
    session = _legacy_session(db_session, test_project, test_agent, test_user, status="ended")
    assist = _assist_row(db_session, test_project, test_agent, test_user, session)

    row = _row(client, test_project, assist.id)
    assert row["user_full_name"] == "Test Admin"
    assert row["user_username"] == "test-admin"
