"""Deleting an account keeps the record of what its agents did (review
2026-10-10, H21; owner, 2026-10-11: "Keep the logs with user nulled").

``DELETE /users/{id}`` deletes the account's agents (``agents.owner_id``
CASCADE), and ``agent_api_calls.agent_id`` was CASCADE too: every call those
agents had made went with the account — the one trace of a deleted user that
was not kept, while ``/.well-known/networkmapper.json`` said
``audit_trail_recorded: true``.  The foreign key is SET NULL since revision
``a4f8c2e6b0d1`` and the attribution check allows a row whose agent is gone.

Pinned here through the reads of the session's page: after the delete the
session, its calls, its tests, its evidence and its proposals are all still
listed, attributed to nobody.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models_agent import Agent, AgentApiCall, AgentSession
from app.db.models_auth import APIKey, User, UserRole
from app.db.models_host_tests import HostTest
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_proposals import AgentProposal, EvidenceRecord
from app.main import app


def _operator_with_a_worked_session(client, db, project, admin):
    """A project analyst who started an agent session that made two calls,
    proposed two tests, recorded three evidence records and one proposal.
    Returns (operator id, session id)."""
    operator = User(
        id=2,  # the fixture user pins id 1 without advancing the sequence
        username="leaving-analyst", email="leaving@example.com", full_name="Leaving Analyst",
        hashed_password="x", role=UserRole.MEMBER, is_active=True,
    )
    db.add(operator)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=operator.id, role=ProjectRole.ANALYST.value))
    db.commit()
    operator_id = operator.id

    app.dependency_overrides[get_current_user] = lambda: operator
    try:
        started = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": "before leaving"})
    finally:
        app.dependency_overrides[get_current_user] = lambda: admin
    assert started.status_code == 201, started.text
    sid = started.json()["agent_session_id"]

    session = db.get(AgentSession, sid)
    assert session.agent_id is not None and session.started_by_id == operator_id
    key = db.query(APIKey).filter(APIKey.agent_session_id == sid).one()
    host = models.Host(project_id=project.id, ip_address="10.72.0.1", state="up")
    db.add(host)
    db.flush()
    for i in range(2):
        db.add(AgentApiCall(
            agent_id=session.agent_id, api_key_id=key.id, project_id=project.id, agent_session_id=sid,
            method="GET", path=f"/api/v1/agent/hosts/{host.id}", path_template="/agent/hosts/{host_id}",
            status_code=200, duration_ms=4 + i,
        ))
        db.add(HostTest(
            project_id=project.id, host_id=host.id, tool="nxc", description=f"t{i}", rationale="r",
            priority="medium", status="proposed", source="agent", agent_session_id=sid,
            created_by_user_id=operator_id, request_key=f"leaving-{sid}-{i}", request_hash="0" * 64,
        ))
    for i in range(3):
        db.add(EvidenceRecord(
            project_id=project.id, host_id=host.id, tool="nxc", outcome="info", summary=f"e{i}",
            agent_session_id=sid, recorded_by_user_id=operator_id,
        ))
    db.add(AgentProposal(
        project_id=project.id, kind="finding_create", payload={"title": "Open share", "severity": "medium"},
        agent_session_id=sid, proposed_by_user_id=operator_id,
    ))
    db.commit()
    return operator_id, sid


def test_deleting_a_user_keeps_what_their_agent_session_did(client, db_session, test_project, test_user):
    operator_id, sid = _operator_with_a_worked_session(client, db_session, test_project, test_user)
    base = f"/api/v1/projects/{test_project.id}"

    gone = client.delete(f"/api/v1/users/{operator_id}")
    assert gone.status_code == 200, gone.text
    db_session.expire_all()

    # The account, its agent and its key are gone ...
    assert db_session.get(User, operator_id) is None
    assert db_session.query(Agent).filter(Agent.owner_id == operator_id).count() == 0
    assert db_session.query(APIKey).filter(APIKey.agent_session_id == sid).count() == 0

    # ... and the record of what the agent did is not.
    calls = db_session.query(AgentApiCall).filter(AgentApiCall.agent_session_id == sid).all()
    assert len(calls) == 2
    assert {(c.agent_id, c.api_key_id, c.project_id) for c in calls} == {(None, None, test_project.id)}

    # The session's page: its row, with the work and the calls still counted,
    # and nobody named as the operator.
    row = client.get(f"{base}/agent-sessions/{sid}")
    assert row.status_code == 200, row.text
    row = row.json()
    assert (row["call_count"], row["host_test_count"], row["evidence_count"]) == (2, 2, 3)
    assert (row["agent_id"], row["user_id"], row["user_username"]) == (None, None, None)
    listed = client.get(f"{base}/agent-sessions").json()["sessions"]
    assert sid in {r["id"] for r in listed}

    tests = client.get(f"{base}/host-tests", params={"agent_session_id": sid}).json()
    assert tests["total"] == 2
    evidence = client.get(f"{base}/evidence", params={"agent_session_id": sid}).json()
    assert evidence["total"] == 3
    proposals = client.get(f"{base}/proposals", params={"agent_session_id": sid, "status": "pending"}).json()
    assert proposals["total"] == 1

    # What the installation publishes about the record is still true.
    claims = client.get("/.well-known/networkmapper.json").json()["safety_properties"]
    assert claims["audit_trail_recorded"] is True


def test_the_sessions_call_feed_lists_the_calls_of_a_deleted_users_agent(
    client, db_session, test_project, test_user
):
    """The feed on the session's page (``/api-activity``): each call is still
    there, with no agent and no owner."""
    operator_id, sid = _operator_with_a_worked_session(client, db_session, test_project, test_user)
    assert client.delete(f"/api/v1/users/{operator_id}").status_code == 200

    feed = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/api-activity")
    assert feed.status_code == 200, feed.text
    feed = feed.json()
    assert feed["total"] == 2 and len(feed["items"]) == 2
    for item in feed["items"]:
        assert (item["agent_id"], item["agent_name"], item["owner_id"], item["owner_username"]) == (
            None, None, None, None,
        )
        assert item["path_template"] == "/agent/hosts/{host_id}"
    # "My agents only" is about agents that exist: nobody's calls are not mine.
    mine = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/api-activity", params={"mine": "true"},
    ).json()
    assert mine["total"] == 0


def test_a_call_still_names_a_project_or_an_error(db_session, test_project):
    """The relaxed check: no agent is legal, no project and no error is not."""
    db_session.add(AgentApiCall(
        agent_id=None, project_id=test_project.id, method="GET", path="/api/v1/agent/x",
        status_code=200, duration_ms=1,
    ))
    db_session.flush()
    with pytest.raises(IntegrityError) as refused:
        with db_session.begin_nested():
            db_session.add(AgentApiCall(method="GET", path="/api/v1/agent/x", status_code=200, duration_ms=1))
            db_session.flush()
    assert "ck_agent_api_calls_attribution_or_error" in str(refused.value.orig)
