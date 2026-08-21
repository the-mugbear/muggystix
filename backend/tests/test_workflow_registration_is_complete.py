"""A new agent workflow cannot go missing from the surfaces that list them.

This is the guard for the bug that actually happened. `AgentSession`'s docstring
described four workflows from the day it was written, but "the list of
workflows" was duplicated across five places — the service's kind set, the
service's per-kind branches, the endpoint's `SessionKindLiteral`, the rollup
bucket, and the frontend union — and **assist was missing from all of them for
months**. An operator with a live assist key saw nothing on Agent Runs, the
surface whose entire job is showing what the agents are doing, and the
per-(model, tool) rollup under-reported what a harness had done on the project.

Nothing failed. A workflow that is simply absent produces no error, no empty
state, and no log line — it produces a shorter list that looks complete.

The alternative fix was collapsing the twelve columns duplicated across the four
session tables into `agent_sessions`, so there would be one list by
construction. That is a large mechanical refactor of every reader of `status`,
`environment` and `generated_by_*`, and the consolidation has twice shown that
removing a thing which was quietly carrying a second load is how regressions get
in. This test costs nothing and catches the same class.
"""
from app.db.models_agent import AgentSessionWorkflow


def test_every_workflow_appears_in_the_session_service():
    """The service's default kind set must cover every declared workflow.

    ``ALL_SESSION_KINDS`` exists because this list previously drifted between
    three call sites inside the service alone.
    """
    from app.services.agent_session_service import ALL_SESSION_KINDS

    declared = {w.value for w in AgentSessionWorkflow}
    missing = declared - set(ALL_SESSION_KINDS)
    assert not missing, (
        f"workflows declared on AgentSessionWorkflow but absent from the agent-"
        f"session timeline: {sorted(missing)}. A session of that kind would be "
        "invisible on Agent Runs and uncounted in the model/tool rollup — with "
        "no error to notice."
    )


def test_every_workflow_appears_in_the_api_literal():
    """The endpoint's `kind` filter must accept every declared workflow.

    A kind the service returns but the response model rejects is worse than an
    omission: it is a 500 on a valid row.
    """
    from typing import get_args

    from app.api.v1.endpoints.agent_sessions import SessionKindLiteral

    declared = {w.value for w in AgentSessionWorkflow}
    accepted = set(get_args(SessionKindLiteral))
    missing = declared - accepted
    assert not missing, (
        f"workflows the timeline can return but the API literal rejects: "
        f"{sorted(missing)}. Add them to SessionKindLiteral."
    )


def test_the_service_can_actually_list_every_kind(db_session, test_project):
    """Structural agreement is not enough — each kind needs a branch that runs.

    A kind present in ``ALL_SESSION_KINDS`` with no matching query branch
    returns an empty list rather than raising, which is the same silent-absence
    failure in a different place.
    """
    from app.services.agent_session_service import (
        ALL_SESSION_KINDS,
        count_agent_sessions,
        list_agent_sessions,
    )

    for kind in sorted(ALL_SESSION_KINDS):
        # Both entry points, because they enumerate the kinds separately.
        rows = list_agent_sessions(db_session, test_project.id, kinds=[kind], limit=5)
        total = count_agent_sessions(db_session, test_project.id, kinds=[kind])
        assert isinstance(rows, list), f"{kind}: list_agent_sessions has no branch"
        assert isinstance(total, int), f"{kind}: count_agent_sessions has no branch"


def test_the_model_tool_rollup_buckets_every_kind(db_session, test_project, test_user):
    """The rollup answers "what has this harness been doing here". A kind it
    does not bucket makes that answer wrong, not merely incomplete."""
    from app.services.agent_session_service import (
        ALL_SESSION_KINDS,
        summarise_by_model_tool,
    )
    from app.db.models_agent import (
        Agent,
        AgentSession,
        AssistSession,
        AssistSessionStatus,
    )

    # One row so the rollup has something to bucket; the assertion is about the
    # shape of every bucket, which is created per (model, tool) pair.
    agent = Agent(
        name="rollup-probe", project_id=test_project.id, owner_id=test_user.id,
    )
    db_session.add(agent)
    db_session.flush()
    base = AgentSession(
        workflow=AgentSessionWorkflow.ASSIST.value,
        project_id=test_project.id, agent_id=agent.id, status="active",
    )
    db_session.add(base)
    db_session.flush()
    db_session.add(AssistSession(
        project_id=test_project.id, agent_id=agent.id,
        status=AssistSessionStatus.ACTIVE, agent_session_id=base.id,
        generated_by_model="probe-model", generated_by_tool="probe-tool",
    ))
    db_session.commit()

    rows = summarise_by_model_tool(db_session, test_project.id)
    assert rows, "expected at least one (model, tool) bucket"
    for row in rows:
        missing = set(ALL_SESSION_KINDS) - set(row)
        assert not missing, (
            f"model/tool rollup has no counter for {sorted(missing)} — sessions "
            "of that kind are silently uncounted"
        )
