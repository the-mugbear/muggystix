"""A session row says what the session left behind (v2.442.0).

Until v2.442.0 a row carried ``target_label`` (the scope's ranges or the plan's
title) and ``phases`` (the runs it opened).  Plans and runs are gone; what a
session leaves now is the host tests it proposed and the evidence it recorded,
and the row counts both — ``host_test_count`` / ``evidence_count`` — so the
Agent Sessions page can tell a session that did work from one that only
connected.

Pinned here: the counts are the session's own (not the project's), the retired
fields are absent, and the counts cost no query per row.
"""
import uuid

from app.db import models
from app.db.models_agent import AgentSession, AgentSessionWorkflow
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord


def _session(db, project, agent, user):
    base = AgentSession(
        workflow=AgentSessionWorkflow.PROJECT.value,
        project_id=project.id, agent_id=agent.id, started_by_id=user.id,
        status="active",
    )
    db.add(base)
    db.commit()
    return base


def _host(db, project, ip="10.47.0.1"):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


def _test(db, project, host, session):
    key = str(uuid.uuid4())
    row = HostTest(
        project_id=project.id, host_id=host.id, tool="nmap", description="svc detect",
        rationale="open ports", priority="medium", status="proposed", source="agent",
        agent_session_id=session.id, request_key=key, request_hash=key,
    )
    db.add(row)
    db.commit()
    return row


def _evidence(db, project, host, session, test=None):
    row = EvidenceRecord(
        project_id=project.id, host_id=host.id, tool="nmap", outcome="no_finding",
        summary="nothing unexpected", agent_session_id=session.id,
        host_test_id=test.id if test is not None else None,
    )
    db.add(row)
    db.commit()
    return row


def _rows(client, project):
    body = client.get(f"/api/v1/projects/{project.id}/agent-sessions").json()
    return {r["id"]: r for r in body["sessions"] if r["kind"] == "project"}


def test_a_row_counts_its_own_sessions_tests_and_evidence(
    client, db_session, test_project, test_agent, test_user
):
    host = _host(db_session, test_project)
    worked = _session(db_session, test_project, test_agent, test_user)
    idle = _session(db_session, test_project, test_agent, test_user)
    first = _test(db_session, test_project, host, worked)
    _test(db_session, test_project, host, worked)
    _evidence(db_session, test_project, host, worked, first)

    rows = _rows(client, test_project)
    assert (rows[worked.id]["host_test_count"], rows[worked.id]["evidence_count"]) == (2, 1)
    # Another session of the same project is not credited with that work.
    assert (rows[idle.id]["host_test_count"], rows[idle.id]["evidence_count"]) == (0, 0)

    # The session's own page reads the same row.
    detail = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{worked.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["host_test_count"] == 2 and detail.json()["evidence_count"] == 1


def test_the_retired_plan_and_phase_fields_are_gone(
    client, db_session, test_project, test_agent, test_user
):
    session = _session(db_session, test_project, test_agent, test_user)
    row = _rows(client, test_project)[session.id]
    for retired in ("target_label", "test_plan_id", "phases", "scope_id"):
        assert retired not in row, retired


def test_work_counts_do_not_add_a_query_per_row(
    client, db_session, test_project, test_agent, test_user
):
    """This runs on a list, so a per-row lookup would put the timeline back
    into N+1.

    The property is "adding sessions that share an agent and an operator adds
    no queries" — not a constant total.  The timeline resolves agent and user
    names once per *distinct* object via the identity map, so its cost is
    bounded by how many people work a project, not by page size.
    """
    from sqlalchemy import event
    from tests.conftest import engine

    counter = {"n": 0}

    def _count(conn, cursor, statement, params, context, executemany):
        counter["n"] += 1

    def _measure():
        counter["n"] = 0
        db_session.expire_all()
        event.listen(engine, "before_cursor_execute", _count)
        try:
            resp = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions")
            assert resp.status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        return counter["n"]

    host = _host(db_session, test_project)

    def _working_session():
        session = _session(db_session, test_project, test_agent, test_user)
        test = _test(db_session, test_project, host, session)
        _evidence(db_session, test_project, host, session, test)

    _working_session()
    one = _measure()

    # Five more sessions, same agent, same operator, each with its own work.
    for _ in range(5):
        _working_session()
    six = _measure()

    assert six == one, (
        f"{one} queries for one session but {six} for six — something is "
        "being resolved per row"
    )
