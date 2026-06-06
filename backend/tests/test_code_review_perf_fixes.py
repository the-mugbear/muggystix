"""Regression tests for v2.91.1 code-review perf fixes.

Covers:

  NEW E — execution-progress endpoint replaced .all() row
          materialisation with grouped SQL counts.  Pre-fix the
          endpoint hydrated every TestPlanEntry + every
          TestExecutionResult on every poll just to derive small
          integer counts.

  NEW H — list_agents replaced its per-agent APIKey lookup with a
          single batched IN(...) query.  Pre-fix N agents fired N+1
          queries; post-fix it's 2 queries regardless of agent
          count.

The E test asserts the returned counts match the row-by-row
arithmetic for a fixture set; the H test asserts the active-key
prefix is surfaced correctly for multiple agents in a single
listing call.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

import pytest


# conftest hard-codes test_user.id = 1 without bumping the Postgres
# sequence; subsequent User inserts without explicit id collide on the
# primary key.  Mirror the pattern test_dashboard_aggregates.py uses:
# a module-level counter assigning ids well above the seeded test_user.
_USER_ID_SEQ = [9000]


# ---------------------------------------------------------------------------
# NEW E — execution-progress counts
# ---------------------------------------------------------------------------


@pytest.fixture
def execution_session_row(db_session, test_plan):
    from app.db.models_agent import ExecutionSession, ExecutionSessionStatus
    s = ExecutionSession(
        test_plan_id=test_plan.id,
        status=ExecutionSessionStatus.ACTIVE.value,
    )
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


@pytest.fixture
def execution_key(db_session, test_agent, test_plan):
    from app.db.models_auth import APIKey
    raw = "nm_agent_perfE_" + "z" * 32
    db_session.add(APIKey(
        agent_id=test_agent.id,
        test_plan_id=test_plan.id,
        name=f"perf-E-{test_plan.id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    db_session.commit()
    return raw


def test_execution_progress_returns_correct_counts(
    client, execution_key, execution_session_row, test_plan, test_project, db_session,
):
    """Seed a known plan/session shape and assert the SQL-count
    path returns the same numbers the row-based Python loop would
    have produced.  Covers entries (total / completed / in_progress
    / total_tests via JSON array length) and results (group-by
    status + finding/critical aggregates)."""
    from app.db import models
    from app.db.models_agent import (
        TestPlanEntry,
        TestExecutionResult,
        TestExecutionStatus,
    )

    # Two hosts + two entries.  Entry A has 3 proposed tests; B has 2.
    host_a = models.Host(
        project_id=test_project.id, ip_address="10.0.0.1", state="up",
    )
    host_b = models.Host(
        project_id=test_project.id, ip_address="10.0.0.2", state="up",
    )
    db_session.add_all([host_a, host_b])
    db_session.commit()
    entry_a = TestPlanEntry(
        test_plan_id=test_plan.id,
        host_id=host_a.id,
        priority="medium",
        test_phase="enumeration",
        rationale="A",
        proposed_tests=[
            {"tool": "nmap"}, {"tool": "httpx"}, {"tool": "nuclei"},
        ],
        status="completed",
    )
    entry_b = TestPlanEntry(
        test_plan_id=test_plan.id,
        host_id=host_b.id,
        priority="high",
        test_phase="enumeration",
        rationale="B",
        proposed_tests=[{"tool": "smb"}, {"tool": "ldap"}],
        status="in_progress",
    )
    db_session.add_all([entry_a, entry_b])
    db_session.commit()

    # Results on entry_a: 1 executed (finding, critical), 1 skipped.
    db_session.add(TestExecutionResult(
        execution_session_id=execution_session_row.id,
        entry_id=entry_a.id,
        test_index=0,
        status=TestExecutionStatus.EXECUTED.value,
        is_finding=True,
        severity="critical",
    ))
    db_session.add(TestExecutionResult(
        execution_session_id=execution_session_row.id,
        entry_id=entry_a.id,
        test_index=1,
        status=TestExecutionStatus.SKIPPED.value,
        is_finding=False,
    ))
    # entry_b: 1 executed (finding, NOT critical).
    db_session.add(TestExecutionResult(
        execution_session_id=execution_session_row.id,
        entry_id=entry_b.id,
        test_index=0,
        status=TestExecutionStatus.EXECUTED.value,
        is_finding=True,
        severity="medium",
    ))
    db_session.commit()

    r = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-progress",
        headers={"X-API-Key": execution_key},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_entries"] == 2
    assert body["entries_completed"] == 1  # entry_a (completed)
    assert body["entries_in_progress"] == 1  # entry_b
    assert body["entries_remaining"] == 1  # total - completed
    assert body["total_tests"] == 5  # 3 + 2 proposed
    assert body["tests_executed"] == 2  # 1 on A + 1 on B
    assert body["tests_skipped"] == 1
    assert body["tests_failed"] == 0
    assert body["tests_pending"] == 2  # 5 proposed - 3 recorded
    assert body["findings_count"] == 2
    assert body["critical_findings"] == 1


def test_execution_progress_empty_session_zeros(
    client, execution_key, execution_session_row, test_plan,
):
    """A session with no entries / no results returns all zeros —
    asserts the COALESCE on the SUM() handles the no-rows case."""
    r = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-progress",
        headers={"X-API-Key": execution_key},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_entries"] == 0
    assert body["total_tests"] == 0
    assert body["tests_executed"] == 0
    assert body["findings_count"] == 0
    assert body["critical_findings"] == 0
    assert body["tests_pending"] == 0


# ---------------------------------------------------------------------------
# NEW H — list_agents batched APIKey lookup
# ---------------------------------------------------------------------------


def test_list_agents_returns_key_prefix_per_agent(client, db_session, test_project):
    """Seed 3 agents in the project (each owned by a distinct user, per
    the uq_agent_per_user_project unique constraint), each with its
    own active APIKey, and assert ``list_agents`` returns the right
    key prefix for each.  Pre-fix this worked but fired 4 queries
    (1 + 3); post-fix it's 2 queries.  The endpoint contract is
    what we care about here — that the batched dict-lookup returns
    the same per-agent prefixes the per-agent .first() did."""
    from app.db.models_agent import Agent
    from app.db.models_auth import APIKey, User, UserRole

    prefixes = ["nm_agent_X1_x", "nm_agent_Y2_y", "nm_agent_Z3_z"]
    for i, pre in enumerate(prefixes):
        _USER_ID_SEQ[0] += 1
        owner = User(
            id=_USER_ID_SEQ[0],
            username=f"perfH-owner-{i}",
            email=f"perfH-owner-{i}@example.com",
            full_name=f"PerfH Owner {i}",
            hashed_password="$2b$12$abcdefghijklmnopqrstuv",
            role=UserRole.MEMBER,
            is_active=True,
            is_verified=True,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(owner)
        db_session.flush()
        a = Agent(
            name=f"agent-{i}",
            project_id=test_project.id,
            owner_id=owner.id,
            description=f"perf-H agent {i}",
            is_active=True,
        )
        db_session.add(a)
        db_session.flush()
        raw = pre + "_active_key_padding_" + str(i)
        db_session.add(APIKey(
            agent_id=a.id,
            name=f"key-{i}",
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            key_prefix=raw[:14],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        ))
    db_session.commit()

    r = client.get(f"/api/v1/projects/{test_project.id}/agents/")
    assert r.status_code == 200, r.text
    body = r.json()
    # The test_agent fixture also creates an Agent on this project,
    # so we filter to just the ones we seeded by name.
    seeded = [row for row in body if row["name"].startswith("agent-")]
    assert len(seeded) == 3
    returned_prefixes = sorted(row["api_key_prefix"] for row in seeded)
    # The raw key is `${pre}_active_key_padding_${i}`; the stored
    # prefix is the first 14 chars, which lands one char past
    # `pre` (a trailing underscore).
    expected_prefixes = sorted(
        (f"{pre}_active_key_padding_{i}")[:14] for i, pre in enumerate(prefixes)
    )
    assert returned_prefixes == expected_prefixes
