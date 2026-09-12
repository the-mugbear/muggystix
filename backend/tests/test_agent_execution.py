"""Execution workflow's agent surface (rewritten for the v2.337.0 unified session).

An execution run is a phase of one project session, opened via
POST /agent/execution-sessions/start on a human-approved plan.  The key is the
session's; the run belongs to the session that opened it.  These tests pin the
surviving invariants — the run resolves for the session that owns it, a
different session cannot complete it, the approval gate holds — rather than the
retired per-workflow key isolation.
"""
from datetime import datetime, timedelta, timezone

import hashlib

import pytest

from app.services.agent_session_service import create_agent_session, mint_session_key


@pytest.fixture
def exec_ctx(db_session, test_project, test_agent, test_plan):
    """A project session that has opened an active execution run on test_plan,
    plus the session's key.  Returns (key, session_id, run)."""
    from app.db.models_agent import ExecutionSession, ExecutionSessionStatus
    session = create_agent_session(
        db_session, project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=None,
    )
    run = ExecutionSession(
        test_plan_id=test_plan.id,
        agent_id=test_agent.id,
        status=ExecutionSessionStatus.ACTIVE.value,
        agent_session_id=session.id,
    )
    db_session.add(run)
    db_session.flush()
    raw = mint_session_key(db_session, agent=test_agent, session=session)
    db_session.commit()
    db_session.refresh(run)
    return raw, session.id, run


def test_execution_context_returns_plan(client, exec_ctx, test_plan):
    key, _sid, _run = exec_ctx
    resp = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/execution-context",
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan"]["id"] == test_plan.id


def test_execution_context_without_an_active_run_409s(
    client, exec_ctx, db_session, test_project, test_agent,
):
    """A plan with no active execution run returns 409 pointing at
    /execution-sessions/start — the per-plan-key 403 is retired (a session
    can read any plan), but you still cannot fetch execution context for a
    plan nothing is executing."""
    from app.db.models_agent import TestPlan, TestPlanStatus
    key, _sid, _run = exec_ctx
    other = TestPlan(
        project_id=test_project.id, agent_id=test_agent.id, version=99,
        title="no-run", status=TestPlanStatus.APPROVED.value,
    )
    db_session.add(other)
    db_session.commit()
    resp = client.get(
        f"/api/v1/agent/test-plans/{other.id}/execution-context",
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 409, resp.text
    assert "no active execution run" in resp.text.lower()


def test_session_environment_probe_roundtrips(client, exec_ctx):
    """One probe per session (replaces the per-phase probe endpoints)."""
    key, _sid, _run = exec_ctx
    resp = client.post(
        "/api/v1/agent/session/environment",
        headers={"X-API-Key": key},
        json={"os_family": "linux"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_type"] == "session"
    assert body["probed_at"] is not None


def test_a_different_session_cannot_complete_this_run(
    client, db_session, exec_ctx, test_project, test_agent,
):
    """Record integrity: completing an execution run is a terminal transition
    on the owning session's work. A different session's key must not do it —
    the boundary the per-key scope used to carry, now enforced by the run's
    agent_session_id."""
    from app.db.models_agent import ExecutionSessionStatus
    _key, _sid, run = exec_ctx
    # A second, unrelated session + key.
    other_session = create_agent_session(
        db_session, project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=None,
    )
    other_key = mint_session_key(db_session, agent=test_agent, session=other_session)
    db_session.commit()

    before = run.status
    resp = client.post(
        f"/api/v1/agent/execution-sessions/{run.id}/complete",
        headers={"X-API-Key": other_key},
        json={"overall_status": "completed"},
    )
    assert resp.status_code == 403, resp.text
    assert "different session" in resp.json()["detail"].lower()

    db_session.expire_all()
    db_session.refresh(run)
    assert run.status == before
    assert run.status != ExecutionSessionStatus.COMPLETED.value


def test_one_session_key_also_reaches_assist_and_recon_reads(client, exec_ctx):
    """v2.337.0 — the cross-workflow block is gone: the session that opened an
    execution run is one project session, so its key also reads the assist
    inventory. (Recon reads need an open recon run; that is a phase state, not
    a key-type rejection.)"""
    key, _sid, _run = exec_ctx
    assert client.get("/api/v1/agent/assist/context", headers={"X-API-Key": key}).status_code == 200
    # No open recon run → 409 (no active recon run), not a 403 by key type.
    r = client.get("/api/v1/agent/recon/context", headers={"X-API-Key": key})
    assert r.status_code == 409, r.text


def test_expired_key_rejected(client, db_session, exec_ctx, test_agent):
    """A key past its expires_at 401s with the structured expired body."""
    from app.db.models_auth import APIKey
    key, sid, _run = exec_ctx
    db_session.query(APIKey).filter(
        APIKey.key_hash == hashlib.sha256(key.encode()).hexdigest()
    ).update({"expires_at": datetime.now(timezone.utc) - timedelta(hours=1)})
    db_session.commit()
    r = client.get(
        "/api/v1/agent/test-plans/1/execution-context",
        headers={"X-API-Key": key},
    )
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["error"] == "key_expired"
