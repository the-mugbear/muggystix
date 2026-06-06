"""
Smoke tests for the execution workflow's agent surface (v2.66.0).

The execution router (`/agent/test-plans/.../complete`, `.../results`,
sanity checks, environment probe) was the second-largest agent
surface without dedicated test coverage.  Phase-1 regressions touch
its `/complete` semantics in detail, but the broader workflow
boundaries weren't pinned in a focused test file.

Tests pin:

  1. A plan-scoped key (api_keys.test_plan_id set) reads its plan's
     execution-context successfully.
  2. The same key is rejected by /agent/assist/* and /agent/recon/* —
     plan keys can't masquerade as other workflows.
  3. Environment probe round-trips through the response model
     (same response-validation guard as v2.64.1 for assist; recon
     and execution didn't have this bug, but pinning prevents a
     future schema change from regressing).
  4. Probe writes to a DIFFERENT execution-session id than the one
     bound to the key — verify scoping doesn't accidentally allow
     it (the endpoint uses `check_agent_rate_limit`, not
     `require_plan_scope`, so this pins the session-id arg check).

Not exhaustive of the eight execution endpoints — that's a larger
effort that should land alongside any execution behavior change.
"""

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
    """A plan-scoped API key.  Plan-scoped keys gate `/agent/test-plans/*`
    via require_plan_scope and bypass assist/recon."""
    from app.db.models_auth import APIKey
    raw = "nm_agent_exec_smoke_" + "x" * 28
    db_session.add(APIKey(
        agent_id=test_agent.id,
        test_plan_id=test_plan.id,
        name=f"exec-smoke-{test_plan.id}",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:14],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    ))
    db_session.commit()
    return raw


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_execution_context_returns_plan(
    client, execution_key, execution_session_row, test_plan,
):
    """Happy path: plan-scoped key reads its own /execution-context.
    Depends on `execution_session_row` so the endpoint's "an active
    execution session must exist" gate is satisfied — the alternative
    would be to skip the gate via a different fixture, but the gate
    is a real production invariant worth pinning."""
    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-context",
        headers={"X-API-Key": execution_key},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"]["id"] == test_plan.id


def test_execution_context_wrong_plan_403s(client, execution_key, test_plan, db_session, test_project, test_agent):
    """require_plan_scope rejects per-plan keys when the URL plan_id
    differs from the key's binding.  This is the bedrock invariant
    that lets two agents work on different plans concurrently without
    cross-contamination."""
    from app.db.models_agent import TestPlan, TestPlanStatus
    # Distinct version since (project_id, version) is unique.
    other_plan = TestPlan(
        project_id=test_project.id,
        agent_id=test_agent.id,
        version=99,
        title="other-plan-for-403",
        status=TestPlanStatus.APPROVED.value,
    )
    db_session.add(other_plan)
    db_session.commit()
    db_session.refresh(other_plan)

    resp = client.get(
        f"/api/v1/agent/test-plans/{other_plan.id}/execution-context",
        headers={"X-API-Key": execution_key},
    )
    assert resp.status_code == 403, resp.text
    assert "different test plan" in resp.json()["detail"].lower()


def test_execution_environment_probe_roundtrips(
    client, execution_key, execution_session_row,
):
    """v2.64.1 regression class — response model must validate after
    the DB commit lands.  Pin it for execution too."""
    resp = client.post(
        f"/api/v1/agent/execution-sessions/{execution_session_row.id}/environment",
        headers={"X-API-Key": execution_key},
        json={"os_family": "linux"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == execution_session_row.id
    assert body["session_type"] == "execution"
    assert body["probed_at"] is not None


def test_assist_key_cannot_write_execution_environment(
    client, test_project, execution_session_row,
):
    """Cross-workflow guarantee in the OTHER direction: an assist key
    must NOT be able to write into an execution-session environment
    row.  The execution-environment route is keyed by ``session_id``
    (not ``plan_id``), so ``require_plan_scope`` can't gate it — the
    handler enforces the assist-scope rejection inline.  Pre-fix this
    route checked only ``scoped_scope_id`` (recon keys), letting an
    assist key whose underlying agent owns the plan slip past and
    overwrite the environment audit row.
    """
    start = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start",
        json={"purpose": "regression: assist key on exec env"},
    )
    assert start.status_code == 201, start.text
    assist_key = start.json()["api_key"]

    resp = client.post(
        f"/api/v1/agent/execution-sessions/{execution_session_row.id}/environment",
        headers={"X-API-Key": assist_key},
        json={"os_family": "linux"},
    )
    assert resp.status_code == 403, resp.text
    assert "assist" in resp.json()["detail"].lower()


def test_execution_key_blocked_from_assist_and_recon(client, execution_key):
    """Cross-workflow guarantee mirroring the recon smoke: a plan-
    scoped key 403s on /agent/assist/* and /agent/recon/*."""
    headers = {"X-API-Key": execution_key}

    assist = client.get("/api/v1/agent/assist/context", headers=headers)
    assert assist.status_code == 403, assist.text
    assert "plan-scoped" in assist.json()["detail"].lower()

    recon = client.get("/api/v1/agent/recon/context", headers=headers)
    assert recon.status_code == 403, recon.text
    # Recon endpoint message just says "reconnaissance-scoped"; not
    # "plan-scoped".  Either word in the detail proves the rejection
    # came from require_recon_scope rather than an upstream error.
    assert "reconnaissance" in recon.json()["detail"].lower()
