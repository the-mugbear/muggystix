"""Regressions from the review of the agent rework (v2.433.1).

With plan approval gone, ``draft`` is where a finished plan waits for its
first run, and a run can only be continued by the session that opened it.
Three rules follow, each pinned here:

* a draft WITH entries is never "possibly interrupted" (only an empty one);
* ending a session abandons its open runs (tested in
  ``test_unified_session_review_fixes``), and another session may not start
  a draft that its drafting session is still writing;
* Portfolio's open-session count is the agent sessions, not execution runs.
"""
from datetime import datetime, timedelta, timezone

from app.db.models_agent import (
    AgentSession,
    ExecutionSession,
    TestPlan,
    TestPlanEntry,
    TestPlanHistory,
    TestPlanStatus,
)
from app.services.agent_session_service import (
    create_agent_session,
    end_agent_session,
    mint_session_key,
)


def _hdr(key):
    return {"X-API-Key": key}


def _session_with_key(db, project, agent):
    session = create_agent_session(
        db, project_id=project.id, agent_id=agent.id, started_by_id=None,
    )
    key = mint_session_key(db, agent=agent, session=session)
    db.commit()
    return session, key


def _plan(db, project, *, drafted_by=None, with_entry=True, created_at=None):
    from app.db import models
    plan = TestPlan(
        project_id=project.id, version=1, title="p", description="d",
        status=TestPlanStatus.DRAFT.value,
        agent_session_id=drafted_by.id if drafted_by is not None else None,
    )
    if created_at is not None:
        plan.created_at = created_at
    db.add(plan)
    db.flush()
    if with_entry:
        host = models.Host(project_id=project.id, ip_address="10.0.0.9", state="up")
        db.add(host)
        db.flush()
        db.add(TestPlanEntry(
            test_plan_id=plan.id, host_id=host.id, priority="high",
            test_phase="enumeration", proposed_tests=[{"name": "t0", "command": "true"}],
            rationale="x",
        ))
    db.commit()
    return plan


# ---------------------------------------------------------------------------
# A draft with entries is a finished plan, not an interrupted drafting
# ---------------------------------------------------------------------------

def test_only_an_empty_draft_can_look_interrupted(db_session, test_project):
    from app.api.v1.endpoints.test_plans import _plan_is_stale

    an_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    plan = TestPlan(project_id=test_project.id, version=1, title="p",
                    status=TestPlanStatus.DRAFT.value, created_at=an_hour_ago)
    assert _plan_is_stale(plan, None, entry_count=3) is False
    assert _plan_is_stale(plan, None, entry_count=0) is True
    plan.status = TestPlanStatus.IN_PROGRESS.value
    assert _plan_is_stale(plan, None, entry_count=0) is False


def test_plan_list_does_not_flag_a_waiting_draft(client, db_session, test_project):
    _plan(db_session, test_project, created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    r = client.get(f"/api/v1/projects/{test_project.id}/test-plans/")
    assert r.status_code == 200, r.text
    plans = r.json()["plans"] if isinstance(r.json(), dict) else r.json()
    assert plans and all(p["is_stale"] is False for p in plans)


# ---------------------------------------------------------------------------
# A draft still being written belongs to the session writing it
# ---------------------------------------------------------------------------

def test_another_session_cannot_start_a_draft_its_drafter_is_still_writing(
    client, db_session, test_project, test_agent,
):
    drafter, drafter_key = _session_with_key(db_session, test_project, test_agent)
    _other, other_key = _session_with_key(db_session, test_project, test_agent)
    plan = _plan(db_session, test_project, drafted_by=drafter)

    r = client.post("/api/v1/agent/execution-sessions/start",
                    headers=_hdr(other_key), json={"plan_id": plan.id})
    assert r.status_code == 409, r.text
    assert f"agent session #{drafter.id}" in r.json()["detail"]
    db_session.expire_all()
    assert db_session.get(TestPlan, plan.id).status == "draft"

    # The drafter starts it whenever it likes, and the step is in the history.
    r = client.post("/api/v1/agent/execution-sessions/start",
                    headers=_hdr(drafter_key), json={"plan_id": plan.id})
    assert r.status_code == 201, r.text
    db_session.expire_all()
    assert db_session.get(TestPlan, plan.id).status == "in_progress"
    rows = db_session.query(TestPlanHistory).filter(
        TestPlanHistory.test_plan_id == plan.id,
        TestPlanHistory.field_changed == "status",
    ).all()
    assert [(h.old_value, h.new_value) for h in rows] == [("draft", "in_progress")]


def test_a_draft_whose_drafter_has_ended_is_anyones_to_start(
    client, db_session, test_project, test_agent,
):
    drafter, _key = _session_with_key(db_session, test_project, test_agent)
    _other, other_key = _session_with_key(db_session, test_project, test_agent)
    plan = _plan(db_session, test_project, drafted_by=drafter)
    end_agent_session(db_session, drafter, reason="done")
    db_session.commit()

    r = client.post("/api/v1/agent/execution-sessions/start",
                    headers=_hdr(other_key), json={"plan_id": plan.id})
    assert r.status_code == 201, r.text


def test_ending_a_session_abandons_a_run_another_session_had_paused(
    client, db_session, test_project, test_agent,
):
    """A's run is paused when B takes the plan over; once A ends, nothing
    can continue it, so it is abandoned rather than left paused for good."""
    a, key_a = _session_with_key(db_session, test_project, test_agent)
    _b, key_b = _session_with_key(db_session, test_project, test_agent)
    plan = _plan(db_session, test_project)
    run_a = client.post("/api/v1/agent/execution-sessions/start",
                        headers=_hdr(key_a), json={"plan_id": plan.id}).json()["session_id"]
    assert client.post("/api/v1/agent/execution-sessions/start",
                       headers=_hdr(key_b), json={"plan_id": plan.id}).status_code == 201
    db_session.expire_all()
    assert db_session.get(ExecutionSession, run_a).status == "paused"

    end_agent_session(db_session, db_session.get(AgentSession, a.id), reason="done")
    db_session.commit()
    assert db_session.get(ExecutionSession, run_a).status == "abandoned"


# ---------------------------------------------------------------------------
# Portfolio counts open agent sessions
# ---------------------------------------------------------------------------

def test_open_sessions_count_agent_sessions_not_execution_runs(
    db_session, test_project, test_agent,
):
    """An agent that is only scanning and uploading opens no execution run;
    it still counts as an open session."""
    from app.services.project_signals_service import project_signals

    _session_with_key(db_session, test_project, test_agent)
    ended, _k = _session_with_key(db_session, test_project, test_agent)
    end_agent_session(db_session, ended, reason="done")
    db_session.commit()

    signals = project_signals(db_session, [test_project], datetime.now(timezone.utc))
    assert signals[test_project.id].active_sessions == 1


# ---------------------------------------------------------------------------
# End / Resume are offered only where the routes would accept them
# ---------------------------------------------------------------------------

def _session_row(client, project, session_id):
    body = client.get(f"/api/v1/projects/{project.id}/agent-sessions").json()
    return next(r for r in body["sessions"] if r["kind"] == "project" and r["id"] == session_id)


def test_resume_is_not_offered_past_the_sessions_lifetime(client, db_session, test_project):
    """The Resume route refuses (409) once the session is past its lifetime;
    the flag used to say yes anyway."""
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    sid = r.json()["agent_session_id"]
    assert _session_row(client, test_project, sid)["can_resume"] is True

    session = db_session.get(AgentSession, sid)
    session.started_at = datetime.now(timezone.utc) - timedelta(days=3650)
    db_session.commit()
    row = _session_row(client, test_project, sid)
    assert row["can_resume"] is False
    assert row["can_end"] is True  # ending is still possible, and useful
    resume = client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/resume", json={})
    assert resume.status_code == 409


def test_an_owner_demoted_below_auditor_is_offered_neither(client, db_session, test_project, test_user):
    from app.db.models_auth import UserRole
    from app.db.models_project import ProjectMembership
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    sid = r.json()["agent_session_id"]

    if test_user.role == UserRole.ADMIN:
        test_user.role = UserRole.MEMBER  # a global admin counts as project admin
    membership = db_session.query(ProjectMembership).filter(
        ProjectMembership.project_id == test_project.id,
        ProjectMembership.user_id == test_user.id,
    ).first()
    if membership is None:
        db_session.add(ProjectMembership(project_id=test_project.id, user_id=test_user.id, role="viewer"))
    else:
        membership.role = "viewer"
    db_session.commit()

    row = _session_row(client, test_project, sid)
    assert row["can_end"] is False and row["can_resume"] is False


# ---------------------------------------------------------------------------
# A retired plan status is an error, not an empty list
# ---------------------------------------------------------------------------

def test_the_plan_list_rejects_a_retired_status(client, test_project):
    r = client.get(f"/api/v1/projects/{test_project.id}/test-plans/", params={"status": "approved"})
    assert r.status_code == 422
    ok = client.get(f"/api/v1/projects/{test_project.id}/test-plans/", params={"status": "draft"})
    assert ok.status_code == 200
