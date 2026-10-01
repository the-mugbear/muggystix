"""Assist belongs on the unified agent-session timeline.

`AgentSession`'s docstring has always said "one row per plan-generation /
execution / recon / **assist** session", but `agent_session_service` enumerated
three kinds and the endpoint's `SessionKindLiteral` listed three. So a project
with a live assist key showed nothing on the surface whose whole job is
answering "what are the agents doing right now?" — and the per-(model, tool)
rollup under-reported what a given harness had been doing.

v2.442.0 — the plan-generation and execution kinds went with their tables, so
the timeline is `project` sessions plus these legacy assist rows; asking for a
retired kind is a 422, not an empty list.

v2.449.0 — an assist session is an ``agent_sessions`` row with workflow
``assist`` (it was sourced from the ``assist_sessions`` table, keyed by that
table's ids, until migration b8e2a5c7d1f3 folded the table in).
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models_agent import AgentSession, AgentSessionWorkflow


@pytest.fixture
def assist_session(db_session, test_project, test_agent, test_user):
    """An assist session with the attribution the timeline reports."""
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


def test_assist_sessions_appear_on_the_timeline(
    client, db_session, test_project, assist_session
):
    body = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions"
    ).json()
    assist_rows = [r for r in body["sessions"] if r["kind"] == "assist"]
    assert len(assist_rows) == 1, (
        "an active assist session is invisible on the surface that exists to "
        "show what the agents are doing"
    )
    row = assist_rows[0]
    assert row["id"] == assist_session.id
    assert row["status"] == "active"
    assert row["generated_by_model"] == "claude-opus-5"
    assert row["prompt_version"] == "1.56.0"
    # Assist is project-scoped.  (No row carries a scope_id since recon runs
    # went, v2.433.1, or a test_plan_id since plans went, v2.442.0.)
    assert "scope_id" not in row
    assert "test_plan_id" not in row


def test_assist_is_counted_in_the_total(client, test_project, assist_session):
    """`total` drives pagination. A kind listed but not counted would make the
    pager disagree with the list."""
    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    assert body["total"] == len(body["sessions"])
    assert body["total"] >= 1


def test_assist_can_be_filtered_for_on_its_own(client, test_project, assist_session):
    body = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions?kind=assist"
    ).json()
    assert body["total"] == 1
    assert {r["kind"] for r in body["sessions"]} == {"assist"}


def test_excluding_assist_still_works(client, test_project, assist_session):
    """The kind filter has to keep excluding what it isn't asked for —
    otherwise adding a kind quietly widens every existing caller's results."""
    body = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions?kind=project"
    ).json()
    assert [r for r in body["sessions"] if r["kind"] == "assist"] == []


@pytest.mark.parametrize("retired", ["execution", "plan_generation", "recon"])
def test_a_retired_kind_is_refused_not_answered_with_nothing(client, test_project, retired):
    r = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions?kind={retired}")
    assert r.status_code == 422, r.text


def test_ended_assist_reports_its_completion_time(
    client, db_session, test_project, assist_session
):
    """A row that has ended must not look like it is still running."""
    ended = datetime.now(timezone.utc)
    assist_session.status = "ended"
    assist_session.completed_at = ended
    db_session.commit()

    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    row = next(r for r in body["sessions"] if r["kind"] == "assist")
    assert row["status"] == "ended"
    assert row["completed_at"] is not None


def test_active_filter_reaches_assist(client, test_project, assist_session):
    """`status=active` is the in-flight banner's query, and 'active' is the one
    status value every kind shares — so it must find assist too."""
    body = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions?status=active"
    ).json()
    assert any(r["kind"] == "assist" for r in body["sessions"])


def test_model_tool_rollup_counts_assist(client, test_project, assist_session):
    """The rollup card answers 'what has this harness been doing here'. Missing
    a whole workflow makes that answer wrong, not merely incomplete."""
    body = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions/by-model-tool"
    ).json()
    row = next(
        r for r in body["summary"]
        if (r["generated_by_model"], r["generated_by_tool"])
        == ("claude-opus-5", "claude-code")
    )
    assert row["assist"] == 1
    assert row["total"] >= 1
