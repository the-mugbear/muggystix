"""Regression tests for v2.91.0 — code review #2 Option B.

``record_test_result`` previously accepted ``status="executed"``
without checking for a passing HostSanityCheck on the entry; the
sanity-check gate fired only at completion time, leaving a window
where raw results landed against an unverified target with no audit
provenance.

Option B (preserve data, audit the gap): the endpoint now requires
either a passing HostSanityCheck for (session, entry) OR an explicit
``sanity_override_reason`` in the request body.  The reason is
persisted on the TestExecutionResult.sanity_override_reason column
and the bypass is recorded in the audit log.  Operators get to keep
the result data from runs that don't reach completion; reviewers get
a one-line SQL query to find every result that bypassed sanity.

These tests pin:
  1. Passing sanity check + no override → 201.
  2. No sanity check + no override → 409 with explanatory detail.
  3. No sanity check + override → 201, column populated, audit logged.
  4. Sanity check exists but ``passed=false`` is treated the same as
     "no check" — the override path still gates.
  5. Non-executed status (e.g. ``pending_approval``) is NOT gated —
     the agent can record a placeholder row before sanity has run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def execution_session_row(db_session, test_plan):
    from app.db.models_agent import ExecutionSession, ExecutionSessionStatus
    session = ExecutionSession(
        test_plan_id=test_plan.id,
        status=ExecutionSessionStatus.ACTIVE.value,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


@pytest.fixture
def execution_key(db_session, test_agent, test_plan):
    from app.db.models_auth import APIKey
    raw = "nm_agent_san_override_" + "y" * 28
    db_session.add(APIKey(
        agent_id=test_agent.id,
        test_plan_id=test_plan.id,
        name=f"san-override-{test_plan.id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    db_session.commit()
    return raw


@pytest.fixture
def test_entry(db_session, test_project, test_plan):
    """A TestPlanEntry attached to a host, with one proposed test."""
    from app.db import models
    from app.db.models_agent import TestPlanEntry
    host = models.Host(
        project_id=test_project.id,
        ip_address="10.99.0.99",
        state="up",
    )
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    entry = TestPlanEntry(
        test_plan_id=test_plan.id,
        host_id=host.id,
        priority="medium",
        test_phase="enumeration",
        rationale="fixture for sanity-override regression tests",
        proposed_tests=[{"tool": "nmap", "command": "nmap -sV 10.99.0.99"}],
        status="approved",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)
    return entry


def _passing_sanity_check(db_session, session, entry):
    """Seed a passing HostSanityCheck for (session, entry).  Mirrors
    the real fields the agent surface fills in."""
    from app.db.models_agent import HostSanityCheck
    sc = HostSanityCheck(
        execution_session_id=session.id,
        entry_id=entry.id,
        host_id=entry.host_id,
        method="ping",
        target_ip="10.99.0.99",
        actual_value="10.99.0.99 responds",
        passed=True,
    )
    db_session.add(sc)
    db_session.commit()
    return sc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_result_with_passing_sanity_no_override_accepted(
    client, execution_key, execution_session_row, test_plan, test_entry, db_session,
):
    """Happy path: sanity check passed → 201 without override."""
    _passing_sanity_check(db_session, execution_session_row, test_entry)
    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{test_entry.id}/test-results",
        headers={"X-API-Key": execution_key},
        json={"test_index": 0, "status": "executed", "is_finding": False},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "executed"


def test_result_without_sanity_no_override_409(
    client, execution_key, execution_session_row, test_plan, test_entry,
):
    """Reject when status='executed', no sanity check, no override."""
    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{test_entry.id}/test-results",
        headers={"X-API-Key": execution_key},
        json={"test_index": 0, "status": "executed", "is_finding": False},
    )
    assert resp.status_code == 409, resp.text
    assert "sanity" in resp.json()["detail"].lower()
    assert "sanity_override_reason" in resp.json()["detail"]


def test_result_without_sanity_with_override_accepted_and_persisted(
    client, execution_key, execution_session_row, test_plan, test_entry, db_session,
):
    """Override accepted, persisted on the row, audit-logged."""
    from app.db.models_agent import TestExecutionResult
    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{test_entry.id}/test-results",
        headers={"X-API-Key": execution_key},
        json={
            "test_index": 0,
            "status": "executed",
            "is_finding": True,
            "findings_summary": "found something",
            "sanity_override_reason": "target offline, evidence captured during pre-flight",
        },
    )
    assert resp.status_code == 201, resp.text
    row = (
        db_session.query(TestExecutionResult)
        .filter(TestExecutionResult.id == resp.json()["id"])
        .first()
    )
    assert row is not None
    assert row.sanity_override_reason == (
        "target offline, evidence captured during pre-flight"
    )


def test_failed_sanity_treated_as_missing(
    client, execution_key, execution_session_row, test_plan, test_entry, db_session,
):
    """A sanity check with passed=false is the same as no check at all
    — the override gate still fires."""
    from app.db.models_agent import HostSanityCheck
    db_session.add(HostSanityCheck(
        execution_session_id=execution_session_row.id,
        entry_id=test_entry.id,
        host_id=test_entry.host_id,
        method="ping",
        target_ip="10.99.0.99",
        actual_value="timeout",
        passed=False,
    ))
    db_session.commit()
    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{test_entry.id}/test-results",
        headers={"X-API-Key": execution_key},
        json={"test_index": 0, "status": "executed", "is_finding": False},
    )
    assert resp.status_code == 409, resp.text


def test_non_executed_status_not_gated(
    client, execution_key, execution_session_row, test_plan, test_entry,
):
    """Recording a non-executed placeholder (pending_approval, etc.)
    doesn't require sanity verification — the agent can lay down
    rows before the test actually runs."""
    resp = client.post(
        f"/api/v1/agent/test-plans/{test_plan.id}/entries/{test_entry.id}/test-results",
        headers={"X-API-Key": execution_key},
        json={
            "test_index": 0,
            "status": "pending_approval",
            "is_finding": False,
        },
    )
    assert resp.status_code == 201, resp.text
