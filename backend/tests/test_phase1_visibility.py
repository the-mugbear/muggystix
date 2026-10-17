"""Phase 1 (visibility) feature tests.

Covers the two new read endpoints:
  - GET /projects/{id}/scans/compare      (attack-surface delta)
  - GET /projects/{id}/agent-activity/summary  (agent API-call analytics)

Both reconstruct their answers from existing tables (HostScanHistory /
PortScanHistory and agent_api_calls respectively), so the tests seed
those tables directly and assert the aggregation maths.
"""
from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models_agent import AgentApiCall


# ---------------------------------------------------------------------------
# Scan-diff
# ---------------------------------------------------------------------------

def _mk_scan(db, project_id, filename):
    scan = models.Scan(
        project_id=project_id,
        filename=filename,
        tool_name="nmap",
        scan_type="nmap_xml",
    )
    db.add(scan)
    db.flush()
    return scan


def _mk_host(db, project_id, ip):
    host = models.Host(ip_address=ip, state="up", project_id=project_id)
    db.add(host)
    db.flush()
    return host


def _seen_host(db, host, scan, state):
    db.add(models.HostScanHistory(
        host_id=host.id,
        scan_id=scan.id,
        state_at_scan=state,
        discovered_at=datetime.now(timezone.utc),
    ))


def _mk_port(db, host, number):
    port = models.Port(host_id=host.id, port_number=number, protocol="tcp", state="open")
    db.add(port)
    db.flush()
    return port


def _seen_port(db, port, scan, state):
    db.add(models.PortScanHistory(
        port_id=port.id,
        scan_id=scan.id,
        state_at_scan=state,
        discovered_at=datetime.now(timezone.utc),
    ))


def test_scan_compare_reports_host_and_port_deltas(client, db_session, test_project):
    pid = test_project.id
    scan_a = _mk_scan(db_session, pid, "baseline.xml")
    scan_b = _mk_scan(db_session, pid, "later.xml")

    h_common = _mk_host(db_session, pid, "10.0.0.1")   # in both, no state change
    h_dropped = _mk_host(db_session, pid, "10.0.0.2")  # only in A
    h_new = _mk_host(db_session, pid, "10.0.0.3")      # only in B
    h_flip = _mk_host(db_session, pid, "10.0.0.4")     # in both, down -> up

    _seen_host(db_session, h_common, scan_a, "up")
    _seen_host(db_session, h_common, scan_b, "up")
    _seen_host(db_session, h_dropped, scan_a, "up")
    _seen_host(db_session, h_new, scan_b, "up")
    _seen_host(db_session, h_flip, scan_a, "down")
    _seen_host(db_session, h_flip, scan_b, "up")

    # Ports on the common host.
    p_stable = _mk_port(db_session, h_common, 22)   # open -> open
    p_open = _mk_port(db_session, h_common, 80)     # closed -> open (newly open)
    p_close = _mk_port(db_session, h_common, 443)   # open -> closed
    p_newhost = _mk_port(db_session, h_new, 3389)   # only seen in B, open

    _seen_port(db_session, p_stable, scan_a, "open")
    _seen_port(db_session, p_stable, scan_b, "open")
    _seen_port(db_session, p_open, scan_a, "closed")
    _seen_port(db_session, p_open, scan_b, "open")
    _seen_port(db_session, p_close, scan_a, "open")
    _seen_port(db_session, p_close, scan_b, "closed")
    _seen_port(db_session, p_newhost, scan_b, "open")
    # v2.332.0 — open in A, NEVER probed by B: unknown, not closed.
    p_unseen = _mk_port(db_session, h_common, 8443)
    _seen_port(db_session, p_unseen, scan_a, "open")
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{pid}/scans/compare",
        params={"a": scan_a.id, "b": scan_b.id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    counts = body["counts"]
    assert counts["new_hosts"] == 1
    assert counts["dropped_hosts"] == 1
    assert counts["host_state_changes"] == 1
    assert counts["newly_open_ports"] == 2   # p_open + p_newhost
    assert counts["closed_ports"] == 1       # p_close — B tested it, found it closed
    # p_unseen has no B observation: it must NOT read as closed.  Absence is
    # not remediation evidence.
    assert counts["not_observed_ports"] == 1
    assert {r["port_number"] for r in body["closed_ports"]} == {443}
    assert {r["port_number"] for r in body["not_observed_ports"]} == {8443}
    assert body["not_observed_ports"][0]["state_b"] is None

    assert {r["ip_address"] for r in body["new_hosts"]} == {"10.0.0.3"}
    assert {r["ip_address"] for r in body["dropped_hosts"]} == {"10.0.0.2"}
    assert body["host_state_changes"][0]["ip_address"] == "10.0.0.4"
    assert body["host_state_changes"][0]["state_a"] == "down"
    assert body["host_state_changes"][0]["state_b"] == "up"

    # Side stats reflect each scan's own observations.
    assert body["scan_a"]["total_hosts"] == 3
    assert body["scan_a"]["up_hosts"] == 2   # common + dropped up; flip down
    assert body["scan_a"]["open_ports"] == 3  # p_stable + p_close + p_unseen
    assert body["scan_b"]["total_hosts"] == 3
    assert body["scan_b"]["up_hosts"] == 3
    assert body["scan_b"]["open_ports"] == 3  # p_stable + p_open + p_newhost


def test_scan_compare_missing_scan_returns_404(client, db_session, test_project):
    pid = test_project.id
    scan_a = _mk_scan(db_session, pid, "only.xml")
    db_session.commit()
    resp = client.get(
        f"/api/v1/projects/{pid}/scans/compare",
        params={"a": scan_a.id, "b": 999999},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Agent-activity analytics summary
# ---------------------------------------------------------------------------

def _api_call(db, *, project_id, agent_id, status, created_at, agent_session_id=None):
    db.add(AgentApiCall(
        agent_id=agent_id,
        project_id=project_id,
        agent_session_id=agent_session_id,
        method="GET",
        path="/api/v1/agent/x",
        status_code=status,
        duration_ms=5,
        created_at=created_at,
    ))


def test_agent_activity_summary_aggregates(client, db_session, test_project, test_agent):
    from app.db.models_agent import AgentSession, AgentSessionWorkflow

    pid = test_project.id
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    session = AgentSession(
        workflow=AgentSessionWorkflow.PROJECT.value, project_id=pid,
        agent_id=test_agent.id, started_by_id=test_agent.owner_id, status="active",
    )
    db_session.add(session)
    db_session.flush()

    # 4 "session" calls (made under an agent session) + 1 "other" call (no
    # session FK).  Until v2.442.0 the first bucket was "plan" calls.
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200, created_at=yesterday, agent_session_id=session.id)
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200, created_at=yesterday, agent_session_id=session.id)
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=404, created_at=yesterday, agent_session_id=session.id)
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=500, created_at=now, agent_session_id=session.id)
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200, created_at=now)
    # Out of window — must be excluded.
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200, created_at=now - timedelta(days=120), agent_session_id=session.id)
    db_session.commit()

    resp = client.get(f"/api/v1/projects/{pid}/agent-activity/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_calls"] == 5
    assert body["distinct_agents"] == 1

    sb = body["status_breakdown"]
    assert sb["success"] == 3
    assert sb["client_error"] == 1
    assert sb["server_error"] == 1

    assert sum(d["calls"] for d in body["daily"]) == 5
    assert sum(d["errors"] for d in body["daily"]) == 2

    busiest = body["busiest_sessions"]
    assert len(busiest) == 1
    top = busiest[0]
    assert top["session_id"] == session.id
    assert top["calls"] == 4


def test_agent_activity_summary_has_no_kinds_of_session(
    client, db_session, test_project, test_agent,
):
    """2.489.0 — a session has no kind, so the summary splits nothing by one
    (``by_workflow`` and each busiest row's ``workflow`` label are gone).
    Every call is counted once, and a session is ranked once under its one id
    whatever ``agent_sessions.workflow`` it is stored with — so long as it is a
    session the list has: a busiest row opens ``/agent-sessions/{id}``, and a
    row stored as ``execution`` has no page to open.  (Until 2.489.0 such a
    row was ranked and labelled "session", and its link was a 404.)"""
    from app.db.models_agent import AgentSession

    pid = test_project.id
    now = datetime.now(timezone.utc)
    sessions = {}
    for workflow in ("project", "assist", "execution"):
        s = AgentSession(
            workflow=workflow, project_id=pid, agent_id=test_agent.id,
            started_by_id=test_agent.owner_id, status="ended",
        )
        db_session.add(s)
        db_session.flush()
        sessions[workflow] = s.id
    for _ in range(3):
        _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200,
                  created_at=now, agent_session_id=sessions["assist"])
    for _ in range(2):
        _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200,
                  created_at=now, agent_session_id=sessions["project"])
    for _ in range(5):
        _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200,
                  created_at=now, agent_session_id=sessions["execution"])
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200, created_at=now)
    db_session.commit()

    body = client.get(f"/api/v1/projects/{pid}/agent-activity/summary").json()
    assert body["total_calls"] == 11
    assert "by_workflow" not in body
    assert body["busiest_sessions"] == [
        {"session_id": sessions["assist"], "calls": 3, "last_activity": body["busiest_sessions"][0]["last_activity"]},
        {"session_id": sessions["project"], "calls": 2, "last_activity": body["busiest_sessions"][1]["last_activity"]},
    ]
    # Each busiest row is a session the list has, and its page answers.
    listed = {s["id"] for s in client.get(f"/api/v1/projects/{pid}/agent-sessions").json()["sessions"]}
    assert {s["session_id"] for s in body["busiest_sessions"]} == listed
    for s in body["busiest_sessions"]:
        assert client.get(f"/api/v1/projects/{pid}/agent-sessions/{s['session_id']}").status_code == 200
    assert client.get(
        f"/api/v1/projects/{pid}/agent-sessions/{sessions['execution']}"
    ).status_code == 404


def test_agent_activity_summary_empty_window(client, db_session, test_project):
    resp = client.get(f"/api/v1/projects/{test_project.id}/agent-activity/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_calls"] == 0
    assert "by_workflow" not in body
    assert body["busiest_sessions"] == []


def test_new_and_dropped_hosts_are_the_lists_their_links_open(client, db_session, test_project):
    """Comprehensive review 2026-10-16, C1: "View in Inventory" opened every
    host of the scan.  The page now links New hosts to the Hosts query
    `scan:<B> AND NOT scan:<A>` and Dropped to `scan:<A> AND NOT scan:<B>`, and the
    compare counts with that same condition — so each count IS the total of
    the list it opens, past the sample's cap too."""
    pid = test_project.id
    scan_a = _mk_scan(db_session, pid, "a.xml")
    scan_b = _mk_scan(db_session, pid, "b.xml")
    earlier = _mk_scan(db_session, pid, "earlier.xml")
    for i in range(1, 8):           # 7 hosts only in B — 3 of them first seen by an EARLIER scan
        h = _mk_host(db_session, pid, f"10.7.0.{i}")
        _seen_host(db_session, h, scan_b, "up")
        if i <= 3:
            db_session.add(models.HostScanHistory(
                host_id=h.id, scan_id=earlier.id, state_at_scan="up",
                discovered_at=datetime.now(timezone.utc) - timedelta(days=1),
            ))
    for i in range(1, 5):           # 4 hosts only in A
        _seen_host(db_session, _mk_host(db_session, pid, f"10.7.1.{i}"), scan_a, "up")
    both = _mk_host(db_session, pid, "10.7.2.1")
    _seen_host(db_session, both, scan_a, "up")
    _seen_host(db_session, both, scan_b, "up")
    db_session.commit()

    body = client.get(f"/api/v1/projects/{pid}/scans/compare", params={"a": scan_a.id, "b": scan_b.id}).json()

    def total(q):
        r = client.get(f"/api/v1/projects/{pid}/hosts/", params={"q": q, "limit": 1})
        assert r.status_code == 200, r.text
        return r.json()["total"]

    assert body["counts"]["new_hosts"] == 7 == total(f"scan:{scan_b.id} AND NOT scan:{scan_a.id}")
    assert body["counts"]["dropped_hosts"] == 4 == total(f"scan:{scan_a.id} AND NOT scan:{scan_b.id}")
    # The review's suggested `first_seen_in_scan` would have missed the three
    # an earlier scan found first: "new since A" is not "new to the project".
    first_seen = client.get(f"/api/v1/projects/{pid}/hosts/",
                            params={"scan_ids": str(scan_b.id), "first_seen_in_scan": "true", "limit": 1}).json()
    assert first_seen["total"] != body["counts"]["new_hosts"]


def test_scan_compare_counts_past_the_cap_and_lists_the_first_by_address(db_session, test_project):
    """External review 2026-10-16, A5: counts are SQL (exact past the cap) and
    each list is the FIRST ``row_cap`` by address, then port — it was an
    unordered ``list(set)[:cap]`` over every row loaded into Python."""
    from app.services.scan_diff_service import compute_scan_diff

    pid = test_project.id
    scan_a = _mk_scan(db_session, pid, "a.xml")
    scan_b = _mk_scan(db_session, pid, "b.xml")
    # Twelve hosts new in B, inserted out of address order (.12 … .1).
    for i in range(12, 0, -1):
        h = _mk_host(db_session, pid, f"10.9.9.{i}")
        _seen_host(db_session, h, scan_b, "up")
        for port in (443, 22):
            _seen_port(db_session, _mk_port(db_session, h, port), scan_b, "open")
    db_session.commit()

    diff = compute_scan_diff(db_session, pid, scan_a.id, scan_b.id, row_cap=5)
    assert (diff.counts.new_hosts, diff.counts.newly_open_ports) == (12, 24)
    # By address as an address: .1 .2 .3 .4 .5 — never .10 before .2.
    assert [r.ip_address for r in diff.new_hosts] == [f"10.9.9.{i}" for i in range(1, 6)]
    assert [(r.ip_address, r.port_number) for r in diff.newly_open_ports] == [
        ("10.9.9.1", 22), ("10.9.9.1", 443), ("10.9.9.2", 22), ("10.9.9.2", 443), ("10.9.9.3", 22),
    ]
    assert all(r.state_a is None and r.state_b == "open" for r in diff.newly_open_ports)
