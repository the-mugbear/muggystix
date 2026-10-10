"""A session from before the consolidation is on the session list.

`AgentSession`'s docstring has always said assist sessions are sessions, but
`agent_session_service` once enumerated three kinds without it and so did the
endpoint's literal.  A project with a live assist key showed nothing on the
surface whose whole job is answering "what are the agents doing right now?" —
and the per-(model, tool) rollup under-reported what a harness had been doing.

v2.449.0 — such a session is an ``agent_sessions`` row with workflow ``assist``.

2.489.0 — and it READS like every other session: the row carries no ``kind``,
the list has no ``kind`` filter (the three tests of that filter — narrowing to
assist, excluding assist, and a retired kind being a 422 — went with the
parameter), and the rollup counts sessions, not kinds.  What stays guarded is
the original bug: the row is listed, counted, found by the status filter and
counted in the rollup.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models_agent import AgentSession, AgentSessionWorkflow


@pytest.fixture
def assist_session(db_session, test_project, test_agent, test_user):
    """A session stored before the consolidation, with the attribution the
    list reports."""
    session = AgentSession(
        workflow=AgentSessionWorkflow.ASSIST.value,
        project_id=test_project.id,
        agent_id=test_agent.id,
        started_by_id=test_user.id,
        status="active",
        purpose="Review the DMZ findings",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        generated_by_model="claude-opus-5",
        generated_by_tool="claude-code",
        prompt_version="1.56.0",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


def test_it_is_on_the_list(client, db_session, test_project, assist_session):
    body = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions"
    ).json()
    rows = [r for r in body["sessions"] if r["id"] == assist_session.id]
    assert len(rows) == 1, (
        "an active session is invisible on the surface that exists to show "
        "what the agents are doing"
    )
    row = rows[0]
    assert row["status"] == "active"
    assert row["generated_by_model"] == "claude-opus-5"
    assert row["prompt_version"] == "1.56.0"
    # No row says what kind it is, or carries a scope / plan id.
    for absent in ("kind", "scope_id", "test_plan_id"):
        assert absent not in row


def test_it_is_counted_in_the_total(client, test_project, assist_session):
    """`total` drives pagination. A row listed but not counted would make the
    pager disagree with the list."""
    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    assert body["total"] == len(body["sessions"]) == 1


def test_a_kind_is_not_something_the_list_can_be_asked_for(client, test_project, assist_session):
    """There is no ``kind`` parameter: one sent is an undeclared query
    parameter and narrows nothing (it used to select, and a retired value was
    a 422)."""
    for value in ("assist", "project", "execution"):
        body = client.get(
            f"/api/v1/projects/{test_project.id}/agent-sessions", params={"kind": value},
        )
        assert body.status_code == 200, body.text
        assert [r["id"] for r in body.json()["sessions"]] == [assist_session.id]


def test_when_ended_it_reports_its_completion_time(
    client, db_session, test_project, assist_session
):
    """A row that has ended must not look like it is still running."""
    ended = datetime.now(timezone.utc)
    assist_session.status = "ended"
    assist_session.completed_at = ended
    db_session.commit()

    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    row = next(r for r in body["sessions"] if r["id"] == assist_session.id)
    assert row["status"] == "ended"
    assert row["completed_at"] is not None


def test_the_active_filter_reaches_it(client, test_project, assist_session):
    """`status=active` is the live-sessions query, so it must find this row."""
    body = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions?status=active"
    ).json()
    assert [r["id"] for r in body["sessions"]] == [assist_session.id]


def test_the_model_tool_rollup_counts_it(client, test_project, assist_session):
    """The rollup answers 'what has this harness been doing here'. Missing a
    session makes that answer wrong, not merely incomplete."""
    body = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions/by-model-tool"
    ).json()
    row = next(
        r for r in body["summary"]
        if (r["generated_by_model"], r["generated_by_tool"])
        == ("claude-opus-5", "claude-code")
    )
    assert row == {
        "generated_by_model": "claude-opus-5", "generated_by_tool": "claude-code", "total": 1,
    }
