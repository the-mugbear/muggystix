"""Assist belongs on the unified agent-session timeline.

`AgentSession`'s docstring has always said "one row per plan-generation /
execution / recon / **assist** session", but `agent_session_service` enumerated
three kinds and the endpoint's `SessionKindLiteral` listed three. So a project
with a live assist key showed nothing on the surface whose whole job is
answering "what are the agents doing right now?" — and the per-(model, tool)
rollup under-reported what a given harness had been doing.

The fix is additive: assist is sourced from `AssistSession`, exactly as the
other three kinds come from their own detail tables.

Deliberately NOT collapsed onto the unified `AgentSession` base row, which the
external review proposed. Three things block that and none are cosmetic:
  * plan_generation rows are keyed by `TestPlan.id`, not `AgentSession.id`, so
    switching would change every id the UI links on;
  * plan_generation status is derived live from `TestPlan.status` and nothing
    copies it onto the base row, so reading the base row would freeze every
    plan session at "active";
  * the backfill migration (b8e1f37a92c4) creates base rows for execution,
    recon and assist — but not plan_generation.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models_agent import (
    AgentSession,
    AgentSessionWorkflow,
    AssistSession,
    AssistSessionStatus,
)


@pytest.fixture
def assist_session(db_session, test_project, test_agent, test_user):
    """An assist session with the attribution the timeline reports."""
    base = AgentSession(
        workflow=AgentSessionWorkflow.ASSIST.value,
        project_id=test_project.id,
        agent_id=test_agent.id,
        started_by_id=test_user.id,
        status="active",
    )
    db_session.add(base)
    db_session.flush()
    session = AssistSession(
        project_id=test_project.id,
        agent_id=test_agent.id,
        started_by_id=test_user.id,
        status=AssistSessionStatus.ACTIVE,
        agent_session_id=base.id,
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
    # Assist is project-scoped: neither target id applies.
    assert row["scope_id"] is None
    assert row["test_plan_id"] is None


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
        f"/api/v1/projects/{test_project.id}/agent-sessions?kind=recon"
    ).json()
    assert [r for r in body["sessions"] if r["kind"] == "assist"] == []


def test_ended_assist_reports_its_completion_time(
    client, db_session, test_project, assist_session
):
    """Assist calls it `ended_at`; the timeline calls it `completed_at`. Same
    event — a row that has ended must not look like it is still running."""
    ended = datetime.now(timezone.utc)
    assist_session.status = AssistSessionStatus.ENDED
    assist_session.ended_at = ended
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
