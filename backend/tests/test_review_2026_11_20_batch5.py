"""Review 2026-11-20, batch 5 — reads that grew with the data.

* C15 — the agent's host detail returned every stored port of every state,
  with every script, uncapped;
* C16 — two of the three tables that store a client's User-Agent kept it at
  any length, one of them for requests with no accepted key;
* C17 — the Agent Sessions summary read the window's calls four times for
  figures one statement gives;
* C18 — Oversight's period basis ran the whole engagement read a second time
  for a figure the first already held.
"""
from __future__ import annotations

from sqlalchemy import event

from app.api.v1.endpoints import agent_assist
from app.core.security import AUDIT_USER_AGENT_MAX, stored_user_agent
from app.db import models
from app.db.models_agent import AgentApiCall
from app.services import mcp_telemetry_service


def _assist(client, project_id):
    body = client.post(f"/api/v1/projects/{project_id}/assist/start", json={}).json()
    return {"X-API-Key": body["api_key"]}


# --- C15: the ports an agent is handed -----------------------------------------

def _swept_host(db, project, *, open_ports, closed_ports):
    host = models.Host(project_id=project.id, ip_address="10.80.0.1", state="up")
    db.add(host)
    db.flush()
    for n in range(closed_ports):                   # a full-range sweep that recorded what did not answer
        db.add(models.Port(host_id=host.id, port_number=1000 + n, protocol="tcp", state="closed"))
    for n in range(open_ports):                     # the open ones, numbered ABOVE the closed
        db.add(models.Port(host_id=host.id, port_number=40000 + n, protocol="tcp", state="open"))
    db.commit()
    return host


def test_the_host_detail_caps_its_ports_and_says_what_the_whole_is(client, db_session, test_project, monkeypatch):
    monkeypatch.setattr(agent_assist, "_PORT_CAP", 50)
    host = _swept_host(db_session, test_project, open_ports=12, closed_ports=300)

    answer = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=_assist(client, test_project.id))
    assert answer.status_code == 200, answer.text
    body = answer.json()

    assert len(body["ports"]) == 50
    assert (body["ports_total"], body["ports_truncated"]) == (312, True)
    assert body["port_states"] == {"open": 12, "closed": 300}
    assert body["open_port_count"] == 12
    # Every open port is there, though each is numbered above every closed one…
    assert sum(1 for p in body["ports"] if p["state"] == "open") == 12
    # …and what is returned is in port order.
    numbers = [p["port_number"] for p in body["ports"]]
    assert numbers == sorted(numbers)


def test_an_ordinary_host_is_returned_whole(client, db_session, test_project):
    host = _swept_host(db_session, test_project, open_ports=3, closed_ports=2)

    body = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=_assist(client, test_project.id)).json()

    assert len(body["ports"]) == 5
    assert (body["ports_total"], body["ports_truncated"]) == (5, False)


# --- C16: one cut for every stored User-Agent ----------------------------------

def test_a_user_agent_is_stored_cut_wherever_it_is_stored():
    long = "x" * 5000
    assert len(stored_user_agent(long)) == AUDIT_USER_AGENT_MAX
    assert stored_user_agent(None) is None and stored_user_agent("curl/8") == "curl/8"

    row = mcp_telemetry_service.build_event(rpc_method="tools/list", outcome="ok", user_agent=long)
    assert len(row["user_agent"]) == AUDIT_USER_AGENT_MAX


def test_the_agent_call_log_stores_it_cut_too(client, db_session, test_project):
    headers = {**_assist(client, test_project.id), "User-Agent": "agent/" + "y" * 5000}
    assert client.get("/api/v1/agent/identity", headers=headers).status_code == 200

    stored = [ua for (ua,) in db_session.query(AgentApiCall.user_agent)
              .filter(AgentApiCall.project_id == test_project.id) if ua and ua.startswith("agent/")]
    assert stored and all(len(ua) <= AUDIT_USER_AGENT_MAX for ua in stored)


# --- C17: the summary's totals are one statement -------------------------------

def _statements(db_session):
    seen: list = []

    def note(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", note)
    return seen, lambda: event.remove(db_session.get_bind(), "before_cursor_execute", note)


def test_the_activity_summary_reads_the_call_log_three_times_not_six(client, db_session, test_project):
    for status in (200, 200, 404, 503, 302):
        db_session.add(AgentApiCall(project_id=test_project.id, method="GET", path="/api/v1/agent/identity",
                                    path_template="/agent/identity", status_code=status, duration_ms=2))
    db_session.commit()

    seen, stop = _statements(db_session)
    try:
        answer = client.get(f"/api/v1/projects/{test_project.id}/agent-activity/summary")
    finally:
        stop()
    assert answer.status_code == 200, answer.text
    body = answer.json()

    assert body["total_calls"] == 5
    assert body["status_breakdown"] == {"success": 2, "client_error": 1, "server_error": 1, "other": 1}
    assert body["first_call_at"] and body["last_call_at"]
    # The totals, the daily buckets, the busiest sessions.
    reads = [s for s in seen if "FROM agent_api_calls" in s]
    assert len(reads) == 3, reads


def test_a_project_with_no_calls_still_answers(client, test_project):
    body = client.get(f"/api/v1/projects/{test_project.id}/agent-activity/summary").json()
    assert body["total_calls"] == 0 and body["daily"] == []
