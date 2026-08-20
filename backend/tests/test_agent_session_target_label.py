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
    ReconSession,
    ReconSessionStatus,
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


def _recon(db, project, agent, user, scope):
    session = ReconSession(
        project_id=project.id, agent_id=agent.id, started_by_id=user.id,
        scope_id=scope.id, status=ReconSessionStatus.ACTIVE,
        started_at=datetime.now(timezone.utc),
    )
    db.add(session)
    db.commit()
    return session


def test_a_recon_session_names_the_ranges_it_is_working(
    client, db_session, test_project, test_agent, test_user, scope_with_ranges
):
    _recon(db_session, test_project, test_agent, test_user, scope_with_ranges)

    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    row = next(r for r in body["sessions"] if r["kind"] == "recon")
    assert row["target_label"], "a recon session with a scope showed no target"
    # The CIDRs are the part a colleague needs to avoid duplicating the work.
    assert "10.20.0.0/24" in row["target_label"]
    assert "10.20.1.0/24" in row["target_label"]
    assert "External perimeter" in row["target_label"]


def test_a_large_scope_truncates_rather_than_filling_the_row(
    client, db_session, test_project, test_agent, test_user, scope_with_ranges
):
    """A scope with many subnets must still identify itself at a glance."""
    for i in range(2, 9):
        db_session.add(models.Subnet(scope_id=scope_with_ranges.id, cidr=f"10.20.{i}.0/24"))
    db_session.commit()
    _recon(db_session, test_project, test_agent, test_user, scope_with_ranges)

    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    label = next(r for r in body["sessions"] if r["kind"] == "recon")["target_label"]
    assert "more" in label, "a 9-subnet scope listed every range instead of truncating"
    assert label.count("/24") == 3


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
    assert row["scope_id"] is None and row["test_plan_id"] is None


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

    _recon(db_session, test_project, test_agent, test_user, scope_with_ranges)
    one = _measure()

    # Five more sessions, same agent, same operator, same scope.
    for _ in range(5):
        _recon(db_session, test_project, test_agent, test_user, scope_with_ranges)
    six = _measure()

    assert six == one, (
        f"{one} queries for one recon session but {six} for six — something is "
        "being resolved per row"
    )
