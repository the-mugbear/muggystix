"""The Operations terrain (v2.426.0): hosts by address block, counted by how
far the team has taken them.  The stages are exclusive and add up to the
block's hosts, and every count is the count its drill-down returns."""
from __future__ import annotations

from app.db import models
from app.db.models_agent import (
    ExecutionSession, TestExecutionResult, TestExecutionStatus, TestPlan, TestPlanEntry,
)
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


def _seed(db_session, project, agent, user):
    def host(ip):
        h = models.Host(project_id=project.id, ip_address=ip, state="up")
        db_session.add(h)
        db_session.flush()
        return h

    scan = models.Scan(project_id=project.id, filename="v.nessus", tool_name="nessus")
    db_session.add(scan)
    db_session.flush()

    def critical(h):
        db_session.add(Vulnerability(host_id=h.id, scan_id=scan.id, title="c",
                                     severity=VulnerabilitySeverity.CRITICAL,
                                     source=VulnerabilitySource.NESSUS))

    plan = TestPlan(project_id=project.id, title="p", agent_id=agent.id, created_by_user_id=user.id)
    db_session.add(plan)
    db_session.flush()

    def entry(h):
        e = TestPlanEntry(test_plan_id=plan.id, host_id=h.id, priority="high",
                          test_phase="enumeration", proposed_tests=[], rationale="r")
        db_session.add(e)
        db_session.flush()
        return e

    run = ExecutionSession(test_plan_id=plan.id, agent_id=agent.id)
    db_session.add(run)
    db_session.flush()

    # 10.1.1.0/24: one of each stage; the untouched one and the tested one critical.
    tested = host("10.1.1.1")
    db_session.add(TestExecutionResult(execution_session_id=run.id, entry_id=entry(tested).id,
                                       test_index=0, status=TestExecutionStatus.EXECUTED.value))
    critical(tested)
    entry(host("10.1.1.2"))                           # planned
    skipped = host("10.1.1.3")                        # a skipped test is not a test
    db_session.add(TestExecutionResult(execution_session_id=run.id, entry_id=entry(skipped).id,
                                       test_index=0, status=TestExecutionStatus.SKIPPED.value))
    reviewed = host("10.1.1.4")
    db_session.add(models.HostFollow(host_id=reviewed.id, user_id=user.id,
                                     status=models.FollowStatus.IN_REVIEW))
    noted = host("10.1.1.5")
    db_session.add(models.Annotation(host_id=noted.id, user_id=user.id, body="n"))
    critical(host("10.1.1.6"))                        # untouched, critical
    # 10.1.2.0/24 and an IPv6 /64.
    host("10.1.2.9")
    host("2001:db8::5")
    host("2001:db8::6")
    db_session.flush()


def test_terrain_counts_each_block_by_stage(client, db_session, test_project, test_agent, test_user):
    _seed(db_session, test_project, test_agent, test_user)
    resp = client.get(f"/api/v1/projects/{test_project.id}/workbench/terrain")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by = {b["cidr"]: b for b in body["blocks"]}
    # Address order, IPv4 before IPv6.
    assert [b["cidr"] for b in body["blocks"]] == ["10.1.1.0/24", "10.1.2.0/24", "2001:db8::/64"]
    a = by["10.1.1.0/24"]
    assert (a["hosts"], a["tested"], a["planned"], a["worked"], a["untouched"]) == (6, 1, 2, 2, 1)
    assert (a["critical"], a["critical_untouched"]) == (2, 1)
    assert by["2001:db8::/64"]["untouched"] == 2
    for b in body["blocks"]:
        assert b["tested"] + b["planned"] + b["worked"] + b["untouched"] == b["hosts"]
    assert body["total_hosts"] == 9 and body["unplaced_hosts"] == 0 and body["truncated"] is False


def test_terrain_counts_match_their_drilldowns(client, db_session, test_project, test_agent, test_user):
    """The tower opens `subnet:"<cidr>"`; its tested / critical counts open
    `has:tested` / `has:critical` within it — the same hosts."""
    _seed(db_session, test_project, test_agent, test_user)
    pid = test_project.id
    block = next(b for b in client.get(f"/api/v1/projects/{pid}/workbench/terrain").json()["blocks"]
                 if b["cidr"] == "10.1.1.0/24")

    def total(q):
        r = client.get(f"/api/v1/projects/{pid}/hosts/", params={"q": q, "limit": 1})
        assert r.status_code == 200, r.text
        return r.json()["total"]

    assert total('subnet:"10.1.1.0/24"') == block["hosts"]
    assert total('subnet:"10.1.1.0/24" has:tested') == block["tested"]
    assert total('subnet:"10.1.1.0/24" has:critical') == block["critical"]
    assert total('subnet:"10.1.1.0/24" has:planned') == block["tested"] + block["planned"]
    assert total('subnet:"10.1.1.0/24" has:planned AND NOT has:tested') == block["planned"]
    assert total('subnet:"10.1.1.0/24" has:untouched') == block["untouched"]
    assert total('subnet:"10.1.1.0/24" AND NOT has:untouched AND NOT has:planned') == block["worked"]
    assert total('subnet:"10.1.1.0/24" has:untouched has:critical') == block["critical_untouched"]
