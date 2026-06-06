"""CR4-1 / CR5-C1 — proposed_tests must freeze once the plan can be executed.

TestExecutionResult rows reference a test by position (test_index) into the
entry's proposed_tests JSON array.  Reordering or replacing that array after
execution can begin would silently re-attribute recorded (or in-flight)
evidence, so TestPlanService.update_entry rejects the change (-> 409 at the
API) once the plan leaves drafting: it's approved/in_progress/completed, an
execution session exists, or a result has been recorded.  draft/proposed
plans (plan-gen drafting) stay editable.
"""
from __future__ import annotations

import pytest

from app.db import models
from app.db.models_agent import (
    TestPlan, TestPlanEntry, ExecutionSession, TestExecutionResult,
    TestPlanStatus,
)
from app.services.test_plan_service import TestPlanService


def _entry(db_session, project_id, status=TestPlanStatus.DRAFT.value):
    host = models.Host(project_id=project_id, ip_address="10.7.0.1", state="up")
    db_session.add(host)
    db_session.flush()
    plan = TestPlan(project_id=project_id, title="plan", status=status)
    db_session.add(plan)
    db_session.flush()
    entry = TestPlanEntry(
        test_plan_id=plan.id, host_id=host.id, priority="medium",
        test_phase="validation", rationale="r",
        proposed_tests=[{"description": "a"}, {"description": "b"}],
    )
    db_session.add(entry)
    db_session.flush()
    return plan, entry


def test_proposed_tests_editable_while_drafting(db_session, test_project):
    """draft/proposed plan with no session/results — plan-gen can revise."""
    plan, entry = _entry(db_session, test_project.id)
    svc = TestPlanService(db_session)

    updated = svc.update_entry(
        entry, "user", 1,
        {"proposed_tests": [{"description": "a"}, {"description": "b2"}]},
    )
    assert updated.proposed_tests[1]["description"] == "b2"


def test_proposed_tests_frozen_once_approved(db_session, test_project):
    """CR5-C1 — approval freezes the array even before any session/result,
    because a snapshot (live context or offline bundle) may already be out."""
    plan, entry = _entry(db_session, test_project.id, status=TestPlanStatus.APPROVED.value)
    svc = TestPlanService(db_session)
    with pytest.raises(ValueError, match="proposed_tests"):
        svc.update_entry(
            entry, "user", 1,
            {"proposed_tests": [{"description": "b"}, {"description": "a"}]},
        )


def test_proposed_tests_frozen_when_session_exists_without_results(db_session, test_project):
    """CR5-C1 — an execution session (even with zero results yet) freezes it."""
    plan, entry = _entry(db_session, test_project.id)  # draft, but session exists
    db_session.add(ExecutionSession(test_plan_id=plan.id))
    db_session.flush()
    svc = TestPlanService(db_session)
    with pytest.raises(ValueError, match="proposed_tests"):
        svc.update_entry(
            entry, "user", 1,
            {"proposed_tests": [{"description": "b"}, {"description": "a"}]},
        )


def test_proposed_tests_frozen_after_results(db_session, test_project):
    plan, entry = _entry(db_session, test_project.id)
    session = ExecutionSession(test_plan_id=plan.id)
    db_session.add(session)
    db_session.flush()
    db_session.add(TestExecutionResult(
        execution_session_id=session.id, entry_id=entry.id, test_index=0,
    ))
    db_session.flush()

    svc = TestPlanService(db_session)
    with pytest.raises(ValueError, match="proposed_tests"):
        svc.update_entry(
            entry, "user", 1,
            {"proposed_tests": [{"description": "b"}, {"description": "a"}]},
        )


def test_other_fields_editable_after_results(db_session, test_project):
    """The freeze is surgical — only proposed_tests is blocked once results
    exist; status/notes still update (an analyst can keep working the entry)."""
    plan, entry = _entry(db_session, test_project.id)
    session = ExecutionSession(test_plan_id=plan.id)
    db_session.add(session)
    db_session.flush()
    db_session.add(TestExecutionResult(
        execution_session_id=session.id, entry_id=entry.id, test_index=0,
    ))
    db_session.flush()

    svc = TestPlanService(db_session)
    updated = svc.update_entry(entry, "user", 1, {"notes": "still editable"})
    assert updated.notes == "still editable"
