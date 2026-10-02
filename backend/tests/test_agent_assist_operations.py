"""The Operations / Evidence / scan-compare reads for agents (v2.428.0).

Each agent read wraps the service its page calls, so the property worth
pinning is equality: for the same project and the same person, the agent and
the page return the same answer.  Plus the two things the page does not have
to worry about: an agent's read never acknowledges "since last visit", and the
MCP tools reach these routes with their parameters in the right places.

The ``client`` fixture authenticates as ``test_user`` (id=1); a session started
through ``/assist/start`` acts for that same user, so the personal sections
are comparable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models_vulnerability import (
    Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
)


def _start(client, project_id):
    resp = client.post(
        f"/api/v1/projects/{project_id}/assist/start", json={"purpose": "operations parity"},
    )
    assert resp.status_code == 201, resp.text
    return {"X-API-Key": resp.json()["api_key"]}


def _ui(pid, suffix):
    return f"/api/v1/projects/{pid}{suffix}"


def _host(db, pid, ip, ports=(), first_seen=None):
    h = models.Host(project_id=pid, ip_address=ip, state="up")
    if first_seen is not None:
        h.first_seen = first_seen
    db.add(h)
    db.flush()
    for n in ports:
        db.add(models.Port(host_id=h.id, port_number=n, protocol="tcp", state="open"))
    db.flush()
    return h


def _seed(db, pid):
    """A little of everything the Operations sections read."""
    scan = models.Scan(project_id=pid, filename="ops.xml", tool_name="nmap")
    db.add(scan)
    db.flush()
    crit = _host(db, pid, "10.60.0.1", ports=(445, 443))
    db.add(Vulnerability(
        title="crit", severity=VulnerabilitySeverity.CRITICAL,
        source=VulnerabilitySource.MANUAL, host_id=crit.id, scan_id=scan.id,
        exploitable=True,
    ))
    high = _host(db, pid, "10.60.0.2", ports=(80,))
    db.add(Vulnerability(
        title="high", severity=VulnerabilitySeverity.HIGH,
        source=VulnerabilitySource.MANUAL, host_id=high.id, scan_id=scan.id,
        exploitable=True,  # tier 3, "exploit available" — a second queue row
    ))
    reviewed = _host(db, pid, "10.60.1.3", ports=(22,))
    db.add(models.HostFollow(
        host_id=reviewed.id, user_id=1, status=models.FollowStatus.REVIEWED,
        review_conclusion="needs_evidence", reviewed_at=datetime.now(timezone.utc),
    ))
    mine = _host(db, pid, "10.60.1.4", ports=(3389,))
    db.add(models.HostFollow(host_id=mine.id, user_id=1, status=models.FollowStatus.IN_REVIEW))
    db.commit()
    return scan


def _strip_times(body):
    """``since_last_visit.as_of`` is taken at request time — the one field two
    reads of the same snapshot legitimately disagree on."""
    body = dict(body)
    slv = dict(body["since_last_visit"])
    slv.pop("as_of", None)
    body["since_last_visit"] = slv
    return body


# ---------------------------------------------------------------------------
# Workbench
# ---------------------------------------------------------------------------

def test_agent_workbench_equals_the_operators_page(client, db_session, test_project):
    pid = test_project.id
    _seed(db_session, pid)
    headers = _start(client, pid)

    agent = client.get("/api/v1/agent/assist/workbench", headers=headers)
    assert agent.status_code == 200, agent.text
    page = client.get(_ui(pid, "/workbench"), params={"include_investigate": "false"})
    assert page.status_code == 200, page.text
    assert _strip_times(agent.json()) == _strip_times(page.json())
    # The personal sections are the operator's: the in-review host is theirs.
    assert agent.json()["my_queue"]["in_review_count"] >= 1
    assert agent.json()["investigate"] is None  # paged separately by default


def test_agent_reading_the_workbench_never_marks_it_seen(client, db_session, test_project):
    pid = test_project.id
    _seed(db_session, pid)
    headers = _start(client, pid)
    assert client.get("/api/v1/agent/assist/workbench", headers=headers).status_code == 200
    assert client.get("/api/v1/agent/assist/workbench/investigate", headers=headers).status_code == 200
    cursor = (
        db_session.query(models.OperationsCursor)
        .filter_by(user_id=1, project_id=pid)
        .first()
    )
    assert cursor is None, "an agent read acknowledged the operator's 'since last visit'"
    page = client.get(_ui(pid, "/workbench"), params={"include_investigate": "false"}).json()
    assert page["since_last_visit"]["is_first_visit"] is True


def test_agent_investigate_equals_the_page_and_pages(client, db_session, test_project):
    pid = test_project.id
    _seed(db_session, pid)
    headers = _start(client, pid)

    agent = client.get("/api/v1/agent/assist/workbench/investigate", headers=headers)
    page = client.get(_ui(pid, "/workbench/investigate"))
    assert agent.status_code == 200 and page.status_code == 200
    assert agent.json() == page.json()
    total = agent.json()["queue_total"]
    assert total >= 2  # the two untouched hosts with observations

    # Paging: the second row alone, with whole-queue totals.
    second = client.get(
        "/api/v1/agent/assist/workbench/investigate",
        params={"limit": 1, "offset": 1}, headers=headers,
    ).json()
    assert [r["host_id"] for r in second["items"]] == [agent.json()["items"][1]["host_id"]]
    assert second["queue_total"] == total
    assert second["tier_counts"] == agent.json()["tier_counts"]

    # Past the end: no rows, totals still whole.
    past = client.get(
        "/api/v1/agent/assist/workbench/investigate",
        params={"offset": 500}, headers=headers,
    ).json()
    assert past["items"] == [] and past["queue_total"] == total
    assert past["tier_counts"] == agent.json()["tier_counts"]


def test_agent_terrain_equals_the_page_and_sorts(client, db_session, test_project):
    pid = test_project.id
    _seed(db_session, pid)
    headers = _start(client, pid)

    page = client.get(_ui(pid, "/workbench/terrain")).json()
    agent = client.get("/api/v1/agent/assist/workbench/terrain", headers=headers)
    assert agent.status_code == 200, agent.text
    body = agent.json()
    assert body["blocks"] == page["blocks"]
    assert body["blocks_total"] == len(page["blocks"])
    assert body["total_hosts"] == page["total_hosts"]

    worst = client.get(
        "/api/v1/agent/assist/workbench/terrain",
        params={"sort": "untouched", "limit": 1}, headers=headers,
    ).json()
    assert len(worst["blocks"]) == 1 and worst["limited"] is True
    assert worst["blocks"][0]["untouched"] == max(b["untouched"] for b in page["blocks"])


# ---------------------------------------------------------------------------
# Evidence gaps
# ---------------------------------------------------------------------------

def test_agent_evidence_gaps_equal_the_page(client, db_session, test_project):
    pid = test_project.id
    _seed(db_session, pid)
    headers = _start(client, pid)

    page = client.get(_ui(pid, "/posture/evidence/web_tls/gaps"))
    agent = client.get(
        "/api/v1/agent/assist/evidence/gaps", params={"domain": "web_tls"}, headers=headers,
    )
    assert page.status_code == 200 and agent.status_code == 200, agent.text
    assert agent.json() == page.json()
    assert agent.json()["total"] >= 1

    bad_domain = client.get(
        "/api/v1/agent/assist/evidence/gaps", params={"domain": "nope"}, headers=headers,
    )
    assert bad_domain.status_code == 404
    assert bad_domain.json()["detail"]["error"] == "unknown_domain"
    assert "web_tls" in bad_domain.json()["detail"]["accepted"]
    # MCP acceptance feedback #15: a CIDR where a matrix key belongs — the
    # refusal names the keys that would work.
    bad_seg = client.get(
        "/api/v1/agent/assist/evidence/gaps",
        params={"domain": "web_tls", "segment": "10.10.1.0/24"}, headers=headers,
    )
    assert bad_seg.status_code == 404
    detail = bad_seg.json()["detail"]
    assert detail["error"] == "unknown_segment"
    coverage = client.get("/api/v1/agent/assist/coverage", headers=headers).json()
    keys = [s["key"] for s in coverage["matrix"]["segments"]]
    assert [a["key"] for a in detail["accepted"]] == keys[:50]


# ---------------------------------------------------------------------------
# Scan compare
# ---------------------------------------------------------------------------

def _history(db, host, scan, state="up"):
    db.add(models.HostScanHistory(
        host_id=host.id, scan_id=scan.id, state_at_scan=state,
        discovered_at=datetime.now(timezone.utc),
    ))


def test_agent_scan_compare_equals_the_page(client, db_session, test_project):
    pid = test_project.id
    a = models.Scan(project_id=pid, filename="a.xml")
    b = models.Scan(project_id=pid, filename="b.xml")
    db_session.add_all([a, b])
    db_session.flush()
    kept = _host(db_session, pid, "10.61.0.1")
    gone = _host(db_session, pid, "10.61.0.2")
    new = _host(db_session, pid, "10.61.0.3")
    _history(db_session, kept, a)
    _history(db_session, kept, b)
    _history(db_session, gone, a)
    _history(db_session, new, b)
    db_session.commit()
    headers = _start(client, pid)

    params = {"a": a.id, "b": b.id}
    page = client.get(_ui(pid, "/scans/compare"), params=params)
    agent = client.get("/api/v1/agent/assist/scans/compare", params=params, headers=headers)
    assert page.status_code == 200 and agent.status_code == 200, agent.text
    body, ui = agent.json(), page.json()
    assert body["counts"] == ui["counts"]
    assert body["new_hosts"] == ui["new_hosts"] and body["dropped_hosts"] == ui["dropped_hosts"]
    assert body["counts"]["new_hosts"] == 1 and body["counts"]["dropped_hosts"] == 1
    assert body["row_cap"] == 200  # the agent's default page, not the page's 500

    missing = client.get(
        "/api/v1/agent/assist/scans/compare", params={"a": a.id, "b": 999_999}, headers=headers,
    )
    assert missing.status_code == 404
    assert "999999" in missing.text


def test_agent_scan_hosts_equal_the_scan_pages_as_scanned_table(client, db_session, test_project):
    """Review 2026-10-01: the scan page's "As scanned" rows — and whether the
    scan authenticated to each host — had no agent read."""
    pid = test_project.id
    scan = models.Scan(project_id=pid, filename="auth.nessus", tool_name="nessus")
    db_session.add(scan)
    db_session.flush()
    for ip, credentialed in (("10.62.0.1", True), ("10.62.0.2", False), ("10.62.0.3", None)):
        host = _host(db_session, pid, ip)
        db_session.add(models.HostScanHistory(
            host_id=host.id, scan_id=scan.id, state_at_scan="up", credentialed=credentialed,
            discovered_at=datetime.now(timezone.utc),
        ))
    db_session.commit()
    headers = _start(client, pid)

    page = client.get(_ui(pid, f"/scans/{scan.id}/host-snapshots"))
    agent = client.get(f"/api/v1/agent/assist/scans/{scan.id}/hosts", headers=headers)
    assert page.status_code == 200 and agent.status_code == 200, agent.text
    assert agent.json() == page.json()
    assert [r["credentialed"] for r in agent.json()["items"]] == [True, False, None]

    # Another project's scan is not found, not empty.
    from app.db.models_project import Project
    other = Project(name="Other", slug="other-scan-hosts")
    db_session.add(other)
    db_session.flush()
    foreign = models.Scan(project_id=other.id, filename="theirs.xml")
    db_session.add(foreign)
    db_session.commit()
    assert client.get(
        f"/api/v1/agent/assist/scans/{foreign.id}/hosts", headers=headers,
    ).status_code == 404

    # The MCP tool puts scan_id in the path.
    result = client.post("/api/v1/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_list_scan_hosts", "arguments": {"scan_id": scan.id, "limit": 2}},
    }, headers=headers).json()["result"]
    assert result["isError"] is False, result
    assert result["structuredContent"]["total"] == 3
    assert len(result["structuredContent"]["items"]) == 2


def test_agent_scan_list_carries_the_scanned_port_list(client, db_session, test_project):
    """nmap's <scaninfo> — what the scan was asked to probe — was stored and
    read by nothing.  The scan page shows it; the agent's scan rows carry it."""
    pid = test_project.id
    scan = models.Scan(project_id=pid, filename="ports.xml", tool_name="nmap")
    plain = models.Scan(project_id=pid, filename="other.txt", tool_name="netexec")
    db_session.add_all([scan, plain])
    db_session.flush()
    db_session.add(models.ScanInfo(
        scan_id=scan.id, type="syn", protocol="tcp", numservices=1000, services="1-1000",
    ))
    db_session.commit()
    headers = _start(client, pid)

    rows = {r["id"]: r for r in client.get("/api/v1/agent/assist/scans", headers=headers).json()["items"]}
    assert rows[scan.id]["scan_info"] == [
        {"type": "syn", "protocol": "tcp", "numservices": 1000, "services": "1-1000"},
    ]
    assert rows[plain.id]["scan_info"] == []
    page = client.get(_ui(pid, f"/scans/{scan.id}")).json()
    assert [
        {k: i[k] for k in ("type", "protocol", "numservices", "services")} for i in page["scan_info"]
    ] == rows[scan.id]["scan_info"]


# ---------------------------------------------------------------------------
# Gating and MCP
# ---------------------------------------------------------------------------

def test_a_viewers_agent_can_read_them(client, db_session, test_project):
    """The pages are viewer-level; so are these."""
    from app.db.models_project import ProjectRole
    from tests.test_agent_role_route_matrix import _key_for, _member

    _seed(db_session, test_project.id)
    viewer = _member(db_session, test_project, ProjectRole.VIEWER)
    headers = {"X-API-Key": _key_for(db_session, test_project, viewer)}
    for path in (
        "/api/v1/agent/assist/workbench",
        "/api/v1/agent/assist/workbench/investigate",
        "/api/v1/agent/assist/workbench/terrain",
        "/api/v1/agent/assist/evidence/gaps?domain=web_tls",
    ):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, (path, r.text)


def test_the_mcp_tools_round_trip(client, db_session, test_project):
    pid = test_project.id
    scan = _seed(db_session, pid)
    other = models.Scan(project_id=pid, filename="other.xml")
    db_session.add(other)
    db_session.commit()
    headers = _start(client, pid)

    def call(name, args=None, rid=1):
        return client.post("/api/v1/mcp", json={
            "jsonrpc": "2.0", "id": rid, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        }, headers=headers).json()["result"]

    wb = call("assist_get_workbench", rid=1)
    assert wb["isError"] is False, wb
    assert "since_last_visit" in wb["structuredContent"]

    q = call("assist_list_worth_a_look", {"tier": 1, "limit": 5}, rid=2)
    assert q["isError"] is False, q
    assert all(r["tier"] == 1 for r in q["structuredContent"]["items"])

    # The domain must reach the endpoint: a known one answers; an unknown one
    # is refused by the advertised enum before any call is made.
    gaps = call("assist_list_evidence_gaps", {"domain": "web_tls"}, rid=3)
    assert gaps["isError"] is False, gaps
    assert gaps["structuredContent"]["domain"] == "web_tls"
    bad = client.post("/api/v1/mcp", json={
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "assist_list_evidence_gaps", "arguments": {"domain": "nope"}},
    }, headers=headers).json()
    refused = bad.get("error") or bad["result"]
    assert "error" in bad or refused["isError"] is True, bad
    assert "web_tls" in str(bad), "the refusal names the accepted domains"

    diff = call("assist_compare_scans", {"a": scan.id, "b": other.id}, rid=5)
    assert diff["isError"] is False, diff
    assert diff["structuredContent"]["row_cap"] == 100  # the MCP default page

    terrain = call("assist_get_terrain", {"sort": "untouched", "limit": 1}, rid=6)
    assert terrain["isError"] is False, terrain
    assert len(terrain["structuredContent"]["blocks"]) <= 1
