"""Regressions pinned by the v2.338.0 review of the unified agent session.

Each test names the defect it guards against:

* execution writes resolve the run through the CALLER's session, so a second
  session cannot write into a run it did not open, and a second
  ``/execution-sessions/start`` cannot silently redirect the first session's
  results into its own run;
* resume / rotate END the session they replace — that is what revokes the
  crashed agent's key; minting on the new session never touched it;
* ``/agent/identity`` reports recon and execution run ids in separate fields,
  so MCP ``execution_complete`` never auto-fills a recon id;
* the assist-sessions review page reads activity state through the
  unified session row, which is the one that is actually written;
* an operator can end any project session, not only one started from the
  assist dialog;
* ``last_activity_at`` is debounced rather than rewritten on every call;
* ``open_execution_phase`` re-reads the plan's status under the row lock.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import update

from app.db.models_agent import (
    AgentSession,
    AssistSession,
    ExecutionSession,
    ExecutionSessionStatus,
    TestExecutionResult,
    TestPlan,
    TestPlanEntry,
    TestPlanStatus,
)
from app.db.models_auth import APIKey
from app.services.agent_session_service import create_agent_session, mint_session_key


def _hdr(key):
    return {"X-API-Key": key}


def _start_session(client, project, purpose="review"):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": purpose})
    assert r.status_code == 201, r.text
    return r.json()


def _base_session_id(db, assist_session_id):
    return (
        db.query(AssistSession.agent_session_id)
        .filter(AssistSession.id == assist_session_id)
        .scalar()
    )


def _scope_with_subnet(db, project, cidr="10.0.0.0/24"):
    from app.db import models
    scope = models.Scope(project_id=project.id, name="s1", description="")
    db.add(scope)
    db.flush()
    db.add(models.Subnet(scope_id=scope.id, cidr=cidr))
    db.commit()
    return scope


def _executable_plan(db, project, ip="10.0.0.5"):
    from app.db import models
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    plan = TestPlan(
        project_id=project.id, version=1, title="executable",
        description="scope + method", status=TestPlanStatus.DRAFT.value,
    )
    db.add(plan)
    db.flush()
    entry = TestPlanEntry(
        test_plan_id=plan.id, host_id=host.id, priority="high",
        test_phase="enumeration", proposed_tests=[{"name": "t0", "command": "true"}],
        rationale="x",
    )
    db.add(entry)
    db.commit()
    return plan, entry, host


def _other_session_key(db, project, agent):
    other = create_agent_session(
        db, project_id=project.id, agent_id=agent.id, started_by_id=None,
    )
    key = mint_session_key(db, agent=agent, session=other)
    db.commit()
    return other, key


def _live_keys(db, agent_id):
    return (
        db.query(APIKey)
        .filter(APIKey.agent_id == agent_id, APIKey.is_active.is_(True))
        .all()
    )


# ---------------------------------------------------------------------------
# H1 — execution writes belong to the session that opened the run
# ---------------------------------------------------------------------------

def test_a_different_session_cannot_write_results_into_this_run(
    client, test_project, test_agent, db_session,
):
    body = _start_session(client, test_project)
    key = body["api_key"]
    plan, entry, host = _executable_plan(db_session, test_project)
    r = client.post(
        "/api/v1/agent/execution-sessions/start", headers=_hdr(key),
        json={"plan_id": plan.id},
    )
    assert r.status_code == 201, r.text
    run_id = r.json()["session_id"]

    _other, other_key = _other_session_key(db_session, test_project, test_agent)

    # The intruder's writes are refused: it has no active run on this plan.
    r = client.post(
        f"/api/v1/agent/test-plans/{plan.id}/entries/{entry.id}/sanity-check",
        headers=_hdr(other_key),
        json={"method": "ping", "target_ip": host.ip_address, "passed": True},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "no_active_execution_run"
    r = client.post(
        f"/api/v1/agent/test-plans/{plan.id}/entries/{entry.id}/test-results",
        headers=_hdr(other_key),
        json={"test_index": 0, "status": "skipped"},
    )
    assert r.status_code == 409, r.text
    r = client.post(
        f"/api/v1/agent/test-plans/{plan.id}/entries/{entry.id}/complete",
        headers=_hdr(other_key), json={},
    )
    assert r.status_code == 409, r.text
    assert db_session.query(TestExecutionResult).filter(
        TestExecutionResult.execution_session_id == run_id
    ).count() == 0

    # The owner's write lands.
    r = client.post(
        f"/api/v1/agent/test-plans/{plan.id}/entries/{entry.id}/test-results",
        headers=_hdr(key),
        json={"test_index": 0, "status": "skipped"},
    )
    assert r.status_code == 201, r.text
    rows = db_session.query(TestExecutionResult).filter(
        TestExecutionResult.execution_session_id == run_id
    ).all()
    assert len(rows) == 1


def test_a_second_start_does_not_redirect_the_first_sessions_results(
    client, test_project, test_agent, db_session,
):
    """Session B opening a run on the same plan pauses A's run.  A's next
    result must NOT land on B's run (the plan-scoped lookup did exactly
    that); A is told it has no active run and can re-open one."""
    key_a = _start_session(client, test_project, "A")["api_key"]
    plan, entry, _host = _executable_plan(db_session, test_project)
    run_a = client.post(
        "/api/v1/agent/execution-sessions/start", headers=_hdr(key_a),
        json={"plan_id": plan.id},
    ).json()["session_id"]

    _other, key_b = _other_session_key(db_session, test_project, test_agent)
    r = client.post(
        "/api/v1/agent/execution-sessions/start", headers=_hdr(key_b),
        json={"plan_id": plan.id},
    )
    assert r.status_code == 201, r.text
    run_b = r.json()["session_id"]
    assert run_b != run_a

    r = client.post(
        f"/api/v1/agent/test-plans/{plan.id}/entries/{entry.id}/test-results",
        headers=_hdr(key_a),
        json={"test_index": 0, "status": "skipped"},
    )
    assert r.status_code == 409, r.text
    assert db_session.query(TestExecutionResult).filter(
        TestExecutionResult.execution_session_id == run_b
    ).count() == 0


# H2 (per-run resume / per-plan rotate ending the session they replaced) is
# gone with those routes in v2.433.0: resuming is session-level only
# (POST /agent-sessions/{id}/resume), covered in test_agent_session_resume_and_end.


# ---------------------------------------------------------------------------
# H3 — identity reports the execution run id; MCP fills the right one
# ---------------------------------------------------------------------------

def test_identity_reports_the_execution_run_and_mcp_completes_it(
    client, test_project, db_session,
):
    key = _start_session(client, test_project)["api_key"]
    plan, _entry, _host = _executable_plan(db_session, test_project)
    exec_id = client.post(
        "/api/v1/agent/execution-sessions/start", headers=_hdr(key),
        json={"plan_id": plan.id},
    ).json()["session_id"]

    ident = client.get("/api/v1/agent/identity", headers=_hdr(key)).json()
    assert ident["execution_session_id"] == exec_id
    assert ident["plan_id"] == plan.id
    assert "workflow_session_id" not in ident

    # execution_complete with no session_id must resolve the EXECUTION run.
    resp = client.post(
        "/api/v1/mcp",
        json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "execution_complete_session",
                       "arguments": {"overall_status": "completed", "notes": "done"}},
        },
        headers=_hdr(key),
    )
    assert resp.status_code == 200, resp.text
    assert "result" in resp.json(), resp.json()
    result = resp.json()["result"]
    assert not result.get("isError"), result
    db_session.expire_all()
    run = db_session.query(ExecutionSession).filter(ExecutionSession.id == exec_id).first()
    assert run.status == ExecutionSessionStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# H4 — the review page reads the columns that are written
# ---------------------------------------------------------------------------

def test_assist_sessions_page_reflects_activity(client, test_project, db_session):
    body = _start_session(client, test_project)
    key, assist_id = body["api_key"], body["assist_session_id"]
    assert client.get("/api/v1/agent/identity", headers=_hdr(key)).status_code == 200

    rows = client.get(f"/api/v1/projects/{test_project.id}/assist/sessions").json()
    mine = next(s for s in rows if s["id"] == assist_id)
    assert mine["last_activity_at"] is not None
    assert mine["purpose"] == "review"
    assert mine["call_count"] >= 1
    detail = client.get(f"/api/v1/projects/{test_project.id}/assist/sessions/{assist_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["last_activity_at"] is not None


# ---------------------------------------------------------------------------
# H6 — any project session can be ended by its operator
# ---------------------------------------------------------------------------

def test_operator_can_end_a_session_with_an_open_execution_run(
    client, test_project, db_session,
):
    key = _start_session(client, test_project)["api_key"]
    plan, _entry, _host = _executable_plan(db_session, test_project)
    r = client.post("/api/v1/agent/execution-sessions/start", headers=_hdr(key), json={"plan_id": plan.id})
    assert r.status_code == 201, r.text
    exec_id = r.json()["session_id"]
    session_id = db_session.query(ExecutionSession.agent_session_id).filter(
        ExecutionSession.id == exec_id
    ).scalar()

    listing = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()
    row = next(s for s in listing["sessions"] if s["kind"] == "project" and s["id"] == session_id)
    assert row["status"] == "active"

    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{session_id}/end")
    assert r.status_code == 204, r.text
    db_session.expire_all()
    assert client.get("/api/v1/agent/identity", headers=_hdr(key)).status_code == 401
    run = db_session.query(ExecutionSession).filter(ExecutionSession.id == exec_id).first()
    # v2.433.1 — abandoned, not paused: nothing can continue a run whose
    # session has ended, and a paused one stayed "blocked" for good.
    assert run.status == "abandoned" and run.completed_at is not None
    # Idempotent-safe: a second end reports that nothing changed.
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{session_id}/end")
    assert r.status_code == 409
    r = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/999999/end")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# R1 — last_activity_at is debounced
# ---------------------------------------------------------------------------

def test_last_activity_is_not_rewritten_on_every_call(client, test_project, db_session):
    body = _start_session(client, test_project)
    key = body["api_key"]
    session_id = _base_session_id(db_session, body["assist_session_id"])

    assert client.get("/api/v1/agent/identity", headers=_hdr(key)).status_code == 200
    db_session.expire_all()
    first = db_session.query(AgentSession.last_activity_at).filter(
        AgentSession.id == session_id
    ).scalar()
    assert first is not None

    assert client.get("/api/v1/agent/identity", headers=_hdr(key)).status_code == 200
    db_session.expire_all()
    second = db_session.query(AgentSession.last_activity_at).filter(
        AgentSession.id == session_id
    ).scalar()
    assert second == first, "a call inside the debounce window must not rewrite the row"


# ---------------------------------------------------------------------------
# H5 — the executable check reads the plan's status under the lock
# ---------------------------------------------------------------------------

def test_open_execution_phase_sees_a_status_change_that_landed_before_the_lock(
    db_session, test_project, test_agent,
):
    from fastapi import HTTPException
    from app.services.agent_session_service import open_execution_phase

    plan, _entry, _host = _executable_plan(db_session, test_project)
    session = create_agent_session(
        db_session, project_id=test_project.id, agent_id=test_agent.id, started_by_id=None,
    )
    db_session.commit()
    assert plan.status == "draft"  # loaded, and now stale on purpose:
    # An archive lands underneath the loaded object.  A Core UPDATE against the
    # TABLE (an ORM-enabled ``update(TestPlan)`` would synchronise the identity
    # map and defeat the point) leaves the in-memory object stale, exactly
    # like another worker's committed transaction would; not committed here
    # because the test session's commit would expire — and so refresh — the
    # object we want to keep stale.
    tbl = TestPlan.__table__
    db_session.execute(
        update(tbl).where(tbl.c.id == plan.id).values(status="archived")
    )
    assert plan.status == "draft"  # still the pre-lock value in memory

    with pytest.raises(HTTPException) as exc:
        open_execution_phase(db_session, session=session, plan=plan)
    assert exc.value.status_code == 409
    assert "archived" in str(exc.value.detail)
    assert db_session.query(ExecutionSession).filter(
        ExecutionSession.test_plan_id == plan.id
    ).count() == 0
