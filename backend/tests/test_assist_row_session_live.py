"""An ``active`` legacy row that nothing can act on says so (v2.402.0).

Agent Runs showed a run as ACTIVE, 15 days old, while its lead said no session
was still active.  The row says which — ``session_live`` — so the table and the
lead stop contradicting each other.  Workflow state, not evidence freshness.

The rows this was written for were execution runs, removed in v2.442.0, and
then the ``assist_sessions`` detail rows, whose status could disagree with
their session's.  Since v2.449.0 a legacy assist session is ONE
``agent_sessions`` row (migration b8e2a5c7d1f3), so that disagreement cannot be
stored any more; what is left of the rule is the case the hourly sweep has not
reached yet: a row still stored ``active`` whose key and renewal window have
both run out.
"""
from datetime import datetime, timedelta, timezone

from app.db.models_agent import AgentSession
from app.db.models_auth import APIKey


def _legacy_session(db, project, agent, user, *, status="active"):
    s = AgentSession(
        workflow="assist",
        project_id=project.id,
        agent_id=agent.id,
        started_by_id=user.id,
        status=status,
        started_at=datetime.now(timezone.utc) - timedelta(days=15),
        purpose="legacy assist",
    )
    db.add(s)
    db.commit()
    return s


def _row(client, project, row_id):
    body = client.get(f"/api/v1/projects/{project.id}/agent-sessions").json()
    return next(r for r in body["sessions"] if r["kind"] == "assist" and r["id"] == row_id)


def test_an_active_row_with_no_key_left_says_so(
    client, db_session, test_project, test_agent, test_user
):
    session = _legacy_session(db_session, test_project, test_agent, test_user)

    row = _row(client, test_project, session.id)
    assert row["status"] == "active"
    assert row["session_live"] is False
    # One id: the row IS the session, and names no second one.
    assert "agent_session_id" not in row and "assist_session_id" not in row


def test_an_active_row_with_a_live_key_is_live(
    client, db_session, test_project, test_agent, test_user
):
    session = _legacy_session(db_session, test_project, test_agent, test_user)
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

    assert _row(client, test_project, session.id)["session_live"] is True


def test_an_ended_row_is_not_judged(
    client, db_session, test_project, test_agent, test_user
):
    """``session_live`` is about rows still in progress; an ended one needs no
    session, so it is not computed."""
    session = _legacy_session(db_session, test_project, test_agent, test_user, status="ended")

    row = _row(client, test_project, session.id)
    assert row["status"] == "ended" and row["session_live"] is None


def test_rows_carry_the_operators_full_name_and_purpose(
    client, db_session, test_project, test_agent, test_user
):
    session = _legacy_session(db_session, test_project, test_agent, test_user, status="ended")

    row = _row(client, test_project, session.id)
    assert row["user_full_name"] == "Test Admin"
    assert row["user_username"] == "test-admin"
    # A legacy row used to be listed without its purpose (it was read from the
    # project kind only).
    assert row["purpose"] == "legacy assist"
