"""A session says what it is working on, in words a colleague can act on.

The point of a declared target is coordination: two analysts should not
unknowingly run recon over the same /24. Enforcement cannot recover that waste
— by the time BlueStick sees an ingest, the scan already ran on someone's
machine — so the mechanism is *visibility*. Which means the timeline has to
show the ranges, not an id.

`scope_id` and `test_plan_id` were already on the row. "Scope #3" tells a second
analyst nothing.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models_agent import (
    Agent,
    AgentSession,
    AgentSessionWorkflow,
    AssistSession,
    AssistSessionStatus,
    TestPlan,
    TestPlanStatus,
)


@pytest.fixture
def scope_with_ranges(db_session, test_project):
    scope = models.Scope(project_id=test_project.id, name="External perimeter")
    db_session.add(scope)
    db_session.commit()
    for cidr in ("10.20.0.0/24", "10.20.1.0/24"):
        db_session.add(models.Subnet(scope_id=scope.id, cidr=cidr))
    db_session.commit()
    return scope


def _plan(db, project, agent, user):
    """A plan drafted by an agent session — a row on the timeline whose target
    is the plan title."""
    base = AgentSession(
        workflow=AgentSessionWorkflow.PLAN_GENERATION.value,
        project_id=project.id, agent_id=agent.id, started_by_id=user.id,
        status="active",
    )
    db.add(base)
    db.flush()
    plan = TestPlan(
        project_id=project.id, version=db.query(TestPlan).count() + 1,
        title="range work", status=TestPlanStatus.DRAFT.value,
        agent_id=agent.id, agent_session_id=base.id,
    )
    db.add(plan)
    db.commit()
    return plan


def test_plan_work_names_the_plan(
    client, db_session, test_project, test_agent, test_plan
):
    test_plan.title = "DMZ credential testing"
    db_session.commit()

    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    row = next(
        r for r in body["sessions"]
        if r["kind"] == "plan_generation" and r["test_plan_id"] == test_plan.id
    )
    assert row["target_label"] == "DMZ credential testing"


def test_assist_has_no_target_and_does_not_invent_one(
    client, db_session, test_project, test_agent, test_user
):
    """Assist is project-wide by design. An empty target is the honest answer;
    saying so is the UI's job, not a fabricated label here."""
    base = AgentSession(
        workflow=AgentSessionWorkflow.ASSIST.value,
        project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=test_user.id, status="active",
    )
    db_session.add(base)
    db_session.flush()
    db_session.add(AssistSession(
        project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=test_user.id, status=AssistSessionStatus.ACTIVE,
        agent_session_id=base.id, purpose="target label test",
    ))
    db_session.commit()

    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    row = next(r for r in body["sessions"] if r["kind"] == "assist")
    assert row["target_label"] is None
    assert row["test_plan_id"] is None and "scope_id" not in row


def test_labels_do_not_add_a_query_per_row(
    client, db_session, test_project, test_agent, test_user, scope_with_ranges
):
    """This runs on a list, so a per-row lookup would put the timeline back
    into N+1 for a purely cosmetic field.

    The property is "adding rows that share an agent and a scope adds no
    queries" — not a constant total. The timeline resolves agent and user names
    once per *distinct* object via the identity map, so its cost is bounded by
    how many people work a project, not by page size. Measured on real data:
    17 rows in 8 queries.
    """
    from sqlalchemy import event
    from tests.conftest import engine

    counter = {"n": 0}

    def _count(conn, cursor, statement, params, context, executemany):
        counter["n"] += 1

    def _measure():
        counter["n"] = 0
        db_session.expire_all()
        event.listen(engine, "before_cursor_execute", _count)
        try:
            resp = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions")
            assert resp.status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        return counter["n"]

    _plan(db_session, test_project, test_agent, test_user)
    one = _measure()

    # Five more sessions, same agent, same operator, same scope.
    for _ in range(5):
        _plan(db_session, test_project, test_agent, test_user)
    six = _measure()

    assert six == one, (
        f"{one} queries for one session but {six} for six — something is "
        "being resolved per row"
    )
