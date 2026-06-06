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
    assert counts["closed_ports"] == 1       # p_close

    assert {r["ip_address"] for r in body["new_hosts"]} == {"10.0.0.3"}
    assert {r["ip_address"] for r in body["dropped_hosts"]} == {"10.0.0.2"}
    assert body["host_state_changes"][0]["ip_address"] == "10.0.0.4"
    assert body["host_state_changes"][0]["state_a"] == "down"
    assert body["host_state_changes"][0]["state_b"] == "up"

    # Side stats reflect each scan's own observations.
    assert body["scan_a"]["total_hosts"] == 3
    assert body["scan_a"]["up_hosts"] == 2   # common + dropped up; flip down
    assert body["scan_a"]["open_ports"] == 2  # p_stable + p_close
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

def _api_call(db, *, project_id, agent_id, status, created_at, test_plan_id=None):
    db.add(AgentApiCall(
        agent_id=agent_id,
        project_id=project_id,
        test_plan_id=test_plan_id,
        method="GET",
        path="/api/v1/agent/x",
        status_code=status,
        duration_ms=5,
        created_at=created_at,
    ))


def test_agent_activity_summary_aggregates(client, db_session, test_project, test_agent, test_plan):
    pid = test_project.id
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    # 4 "plan" calls (have a test_plan_id) + 1 "other" call (no session FK).
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200, created_at=yesterday, test_plan_id=test_plan.id)
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200, created_at=yesterday, test_plan_id=test_plan.id)
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=404, created_at=yesterday, test_plan_id=test_plan.id)
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=500, created_at=now, test_plan_id=test_plan.id)
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200, created_at=now)
    # Out of window — must be excluded.
    _api_call(db_session, project_id=pid, agent_id=test_agent.id, status=200, created_at=now - timedelta(days=120))
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

    by_wf = {row["workflow"]: row["calls"] for row in body["by_workflow"]}
    assert by_wf.get("plan") == 4
    assert by_wf.get("other") == 1

    assert sum(d["calls"] for d in body["daily"]) == 5
    assert sum(d["errors"] for d in body["daily"]) == 2

    busiest = body["busiest_sessions"]
    assert busiest, "expected at least one busiest session"
    top = busiest[0]
    assert top["workflow"] == "plan"
    assert top["session_id"] == test_plan.id
    assert top["calls"] == 4


def test_agent_activity_summary_empty_window(client, db_session, test_project):
    resp = client.get(f"/api/v1/projects/{test_project.id}/agent-activity/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_calls"] == 0
    assert body["by_workflow"] == []
    assert body["busiest_sessions"] == []
