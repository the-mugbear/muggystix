"""
`has:planned` — plan membership, distinct from `has:tested` (v2.234.0).

/operations reports "not yet in any plan" as a coverage gap, but that
population was unreachable: `has:tested` joins TestPlanEntry through to
TestExecutionResult, so it answers "has been *executed against*", and there
was no predicate for the earlier pipeline stage. The gap count was a number
with no way to see what was behind it.

The crux below is host B: planned but never executed. It must match
`has:planned` and NOT `has:tested`, or the new predicate is just an alias.
"""

from datetime import datetime, timezone

import pytest

from app.db.models import Host
from app.db.models_agent import (
    TestPlan,
    TestPlanEntry,
    TestPlanStatus,
    TestExecutionResult,
    ExecutionSession,
    ExecutionSessionStatus,
)
from app.services.host_query_dsl import BuildCtx, evaluate, parse_query


def _host(db, project_id, ip):
    h = Host(
        project_id=project_id, ip_address=ip, state="up",
        first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


@pytest.fixture
def coverage_hosts(db_session, test_project, test_agent, test_user):
    """A = planned AND executed, B = planned only, C = neither."""
    a = _host(db_session, test_project.id, "10.44.0.1")
    b = _host(db_session, test_project.id, "10.44.0.2")
    c = _host(db_session, test_project.id, "10.44.0.3")

    plan = TestPlan(
        project_id=test_project.id, agent_id=test_agent.id, version=1,
        title="coverage plan", status=TestPlanStatus.APPROVED.value,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)

    def _entry(host):
        return TestPlanEntry(
            test_plan_id=plan.id, host_id=host.id, priority="medium",
            test_phase="enumeration", proposed_tests=["nmap -sV"],
            rationale="coverage fixture",
        )

    entry_a = _entry(a)
    entry_b = _entry(b)
    db_session.add_all([entry_a, entry_b])
    db_session.commit()
    db_session.refresh(entry_a)

    session = ExecutionSession(
        test_plan_id=plan.id, agent_id=test_agent.id,
        started_by_id=test_user.id, status=ExecutionSessionStatus.ACTIVE.value,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    # Only host A actually got executed against.
    db_session.add(
        TestExecutionResult(
            entry_id=entry_a.id, execution_session_id=session.id,
            test_index=0, status="passed",
        )
    )
    db_session.commit()
    return {"planned_and_tested": a, "planned_only": b, "neither": c}


def _matching_ips(db, project_id, user, q):
    return {
        h.ip_address
        for h in db.query(Host)
        .filter(Host.project_id == project_id)
        .filter(evaluate(parse_query(q), BuildCtx(db, user, project_id)))
        .all()
    }


def test_planned_includes_hosts_never_executed(
    db_session, test_project, test_user, coverage_hosts
):
    """The whole point — a host in a plan but never run counts as planned."""
    ips = _matching_ips(db_session, test_project.id, test_user, "has:planned")
    assert ips == {"10.44.0.1", "10.44.0.2"}


def test_tested_is_stricter_than_planned(
    db_session, test_project, test_user, coverage_hosts
):
    """If these matched the same set, `has:planned` would be a useless alias."""
    planned = _matching_ips(db_session, test_project.id, test_user, "has:planned")
    tested = _matching_ips(db_session, test_project.id, test_user, "has:tested")
    assert tested == {"10.44.0.1"}
    assert tested < planned


def test_not_planned_is_the_operations_coverage_gap(
    db_session, test_project, test_user, coverage_hosts
):
    """This is the query the /operations 'not yet in any plan' count links to."""
    ips = _matching_ips(db_session, test_project.id, test_user, "NOT has:planned")
    assert ips == {"10.44.0.3"}


def test_not_tested_keeps_planned_but_unexecuted_hosts(
    db_session, test_project, test_user, coverage_hosts
):
    """The 'not yet tested' gap must include hosts that were planned but never
    run — those are exactly the ones an operator needs to chase."""
    ips = _matching_ips(db_session, test_project.id, test_user, "NOT has:tested")
    assert ips == {"10.44.0.2", "10.44.0.3"}
