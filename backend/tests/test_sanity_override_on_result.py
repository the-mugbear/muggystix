"""A target check is evidence, not a gate (v2.433.0).

From v2.91.0 ``record_test_result`` refused an executed result or a finding
unless the entry had a passing HostSanityCheck or the request carried a
``sanity_override_reason``.  That gate was part of the retired "agent on
rails" model: the operator drives their agent, and an agent executing its own
plan must be able to record what it ran and found.  A check the agent did run
is still recorded (POST .../sanity-check) and counted at completion.

These tests pin:
  1. An executed result records with no target check on file.
  2. A finding records with no target check on file.
  3. A FAILED check does not block a result either.
  4. A retired ``sanity_override_reason`` field is ignored, not stored.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def execution_session_row(db_session, test_plan, execution_key):
    """An active run on test_plan, owned by ``execution_key``'s session.

    v2.338.0 — a test-result write resolves the run through the caller's
    session, so the run must belong to the key's session for these tests to
    reach the sanity-check gate they are about."""
    from app.db.models_agent import ExecutionSession, ExecutionSessionStatus
    from app.db.models_auth import APIKey
    base_id = (
        db_session.query(APIKey.agent_session_id)
        .filter(APIKey.key_hash == hashlib.sha256(execution_key.encode()).hexdigest())
        .scalar()
    )
    session = ExecutionSession(
        test_plan_id=test_plan.id,
        status=ExecutionSessionStatus.ACTIVE.value,
        agent_session_id=base_id,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


@pytest.fixture
def execution_key(db_session, test_agent, test_plan):
    from app.db.models_auth import APIKey
    from app.db.models_agent import AgentSessionWorkflow
    from app.services.agent_session_service import create_agent_session
    base = create_agent_session(
        db_session, workflow=AgentSessionWorkflow.EXECUTION.value,
        project_id=test_plan.project_id, agent_id=test_agent.id,
        started_by_id=None,
    )
    raw = "nm_agent_san_override_" + "y" * 28
    db_session.add(APIKey(
        agent_id=test_agent.id,
        agent_session_id=base.id,
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
        status="draft",
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


def _record(client, key, plan, entry, **body):
    return client.post(
        f"/api/v1/agent/test-plans/{plan.id}/entries/{entry.id}/test-results",
        headers={"X-API-Key": key},
        json={"test_index": 0, **body},
    )


def test_executed_result_needs_no_target_check(
    client, execution_key, execution_session_row, test_plan, test_entry,
):
    resp = _record(client, execution_key, test_plan, test_entry, status="executed", is_finding=False)
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "executed"


def test_finding_needs_no_target_check(
    client, execution_key, execution_session_row, test_plan, test_entry, db_session,
):
    from app.db.models_agent import TestExecutionResult
    resp = _record(
        client, execution_key, test_plan, test_entry,
        status="executed", is_finding=True, severity="high",
        findings_summary="anonymous FTP login accepted",
    )
    assert resp.status_code == 201, resp.text
    row = db_session.get(TestExecutionResult, resp.json()["id"])
    assert row.is_finding is True and row.severity == "high"


def test_a_failed_check_does_not_block_a_result(
    client, execution_key, execution_session_row, test_plan, test_entry, db_session,
):
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
    resp = _record(client, execution_key, test_plan, test_entry, status="executed", is_finding=False)
    assert resp.status_code == 201, resp.text


def test_the_retired_override_field_is_ignored(
    client, execution_key, execution_session_row, test_plan, test_entry, db_session,
):
    """An agent on an older prompt may still send it; it is not an error and
    it is not stored as if a gate had been bypassed."""
    from app.db.models_agent import TestExecutionResult
    _passing_sanity_check(db_session, execution_session_row, test_entry)
    resp = _record(
        client, execution_key, test_plan, test_entry,
        status="executed", is_finding=False,
        sanity_override_reason="old prompt habit",
    )
    assert resp.status_code == 201, resp.text
    row = db_session.get(TestExecutionResult, resp.json()["id"])
    assert row is not None
    # v2.433.1 — the column itself is gone, so nothing can store it.
    assert not hasattr(TestExecutionResult, "sanity_override_reason")
