"""An ``active`` row that nothing can act on can be told from a live one.

Agent Runs showed a run as ACTIVE, 15 days old, while its lead said no session
was still active (v2.402.0).  The rows this was written for were execution
runs (removed in v2.442.0) and then the ``assist_sessions`` detail rows; since
v2.449.0 a session from before the consolidation is ONE ``agent_sessions`` row.
What is left is the case the hourly sweep has not reached yet: a row still
stored ``active`` whose key and renewal window have both run out.

2.489.0 — re-meant.  Such a row used to carry its own flag, ``session_live``,
computed for rows stored as ``assist`` only (and by a second copy of the
rule).  Every session reads the same now: the row carries ``status``,
``key_expires_at`` and ``renewable_until`` — the three facts of ANY session —
and ``agent_session_service.session_key_state`` (the page's Live / Resumable /
Expired) is the one reading of them.  These tests keep what the flag was for:
the stored-``active`` row with nothing left reads "ended", the one with a live
key reads "live", for a row stored before the consolidation exactly as for one
started today.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models_agent import AgentSession
from app.db.models_auth import APIKey
from app.services.agent_session_service import session_key_state


def _old_session(db, project, agent, user, *, workflow="assist", status="active"):
    s = AgentSession(
        workflow=workflow,
        project_id=project.id,
        agent_id=agent.id,
        started_by_id=user.id,
        status=status,
        started_at=datetime.now(timezone.utc) - timedelta(days=15),
        purpose="from before",
    )
    db.add(s)
    db.commit()
    return s


def _row(client, project, row_id):
    body = client.get(f"/api/v1/projects/{project.id}/agent-sessions").json()
    return next(r for r in body["sessions"] if r["id"] == row_id)


def _state(row) -> str:
    def _when(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None

    return session_key_state(row["status"], _when(row["key_expires_at"]), _when(row["renewable_until"]))


@pytest.mark.parametrize("workflow", ["assist", "project"])
def test_an_active_row_with_no_key_left_says_so(
    client, db_session, test_project, test_agent, test_user, workflow
):
    session = _old_session(db_session, test_project, test_agent, test_user, workflow=workflow)

    row = _row(client, test_project, session.id)
    assert row["status"] == "active"
    assert row["key_expires_at"] is None and row["renewable_until"] is not None
    assert _state(row) == "ended"
    assert row["can_resume"] is False
    # One id, one shape: the row names no second id and carries no flag of its own.
    for retired in ("agent_session_id", "assist_session_id", "session_live", "kind"):
        assert retired not in row


@pytest.mark.parametrize("workflow", ["assist", "project"])
def test_an_active_row_with_a_live_key_is_live(
    client, db_session, test_project, test_agent, test_user, workflow
):
    session = _old_session(db_session, test_project, test_agent, test_user, workflow=workflow)
    db_session.add(
        APIKey(
            agent_id=test_agent.id,
            agent_session_id=session.id,
            name="k",
            key_hash=f"hash-live-run-{workflow}",
            key_prefix="nm_live",
            is_active=True,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
    )
    db_session.commit()

    assert _state(_row(client, test_project, session.id)) == "live"


def test_an_ended_row_reads_ended(
    client, db_session, test_project, test_agent, test_user
):
    session = _old_session(db_session, test_project, test_agent, test_user, status="ended")

    row = _row(client, test_project, session.id)
    assert row["status"] == "ended" and _state(row) == "ended"
    assert row["can_end"] is False and row["can_resume"] is False


def test_rows_carry_the_operators_full_name_and_purpose(
    client, db_session, test_project, test_agent, test_user
):
    session = _old_session(db_session, test_project, test_agent, test_user, status="ended")

    row = _row(client, test_project, session.id)
    assert row["user_full_name"] == "Test Admin"
    assert row["user_username"] == "test-admin"
    # A row from before the consolidation used to be listed without its purpose.
    assert row["purpose"] == "from before"
