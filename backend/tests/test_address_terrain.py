"""The Operations terrain (v2.426.0): hosts by address block, counted by how
far the team has taken them.  The stages are exclusive and add up to the
block's hosts, and every count is the count its drill-down returns."""
from __future__ import annotations

from app.db import models
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


def _seed(db_session, project, user):
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

    def test(h, status, **extra):
        t = HostTest(project_id=project.id, host_id=h.id, tool="nmap", description="d", rationale="r",
                     priority="high", status=status, source="person",
                     request_key=f"terrain-{h.ip_address}-{status}", request_hash="0" * 64, **extra)
        db_session.add(t)
        db_session.flush()
        return t

    def evidence(h, outcome, t=None):
        db_session.add(EvidenceRecord(project_id=project.id, host_id=h.id, tool="nmap", outcome=outcome,
                                      summary="s", host_test_id=t.id if t is not None else None))

    # 10.1.1.0/24: every way into each stage; one tested and one untouched host critical.
    tested = host("10.1.1.1")                         # tested: the test is done, its result recorded
    evidence(tested, "no_finding", test(tested, "done"))
    critical(tested)
    test(host("10.1.1.2"), "proposed")                # planned
    failed = host("10.1.1.3")                         # a failed attempt is not a test: still planned
    evidence(failed, "failed", test(failed, "in_progress"))
    reviewed = host("10.1.1.4")                       # worked: someone has it
    db_session.add(models.HostFollow(host_id=reviewed.id, user_id=user.id,
                                     status=models.FollowStatus.IN_REVIEW))
    noted = host("10.1.1.5")                          # worked: a note
    db_session.add(models.Annotation(host_id=noted.id, user_id=user.id, body="n"))
    critical(host("10.1.1.6"))                        # untouched, critical
    test(host("10.1.1.7"), "dismissed", dismissed_reason="not this engagement")  # untouched: only a dismissed test
    test(host("10.1.1.8"), "done", tester_summary="ran it by hand, nothing recorded")  # worked: closed, no result
    evidence(host("10.1.1.9"), "info")                # worked: context recorded, not a test
    still = host("10.1.1.10")                         # tested, with another test still to do
    evidence(still, "inconclusive")
    test(still, "proposed")
    # 10.1.2.0/24 and an IPv6 /64.
    host("10.1.2.9")
    host("2001:db8::5")
    host("2001:db8::6")
    db_session.flush()


def test_terrain_counts_each_block_by_stage(client, db_session, test_project, test_user):
    _seed(db_session, test_project, test_user)
    resp = client.get(f"/api/v1/projects/{test_project.id}/workbench/terrain")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by = {b["cidr"]: b for b in body["blocks"]}
    # Address order, IPv4 before IPv6.
    assert [b["cidr"] for b in body["blocks"]] == ["10.1.1.0/24", "10.1.2.0/24", "2001:db8::/64"]
    a = by["10.1.1.0/24"]
    assert (a["hosts"], a["tested"], a["planned"], a["worked"], a["untouched"]) == (10, 2, 2, 4, 2)
    assert (a["critical"], a["critical_untouched"]) == (2, 1)
    assert by["2001:db8::/64"]["untouched"] == 2
    for b in body["blocks"]:
        assert b["tested"] + b["planned"] + b["worked"] + b["untouched"] == b["hosts"]
    assert body["total_hosts"] == 13 and body["unplaced_hosts"] == 0 and body["truncated"] is False


def test_terrain_counts_match_their_drilldowns(client, db_session, test_project, test_user):
    """The tower opens `subnet:"<cidr>"`; its tested / critical counts open
    `has:tested` / `has:critical` within it — the same hosts."""
    _seed(db_session, test_project, test_user)
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
    # The stages are exclusive, tested first: a tested host that still has a
    # test to do (10.1.1.10) is `has:planned` but counted once, as tested.
    assert total('subnet:"10.1.1.0/24" has:planned') == block["planned"] + 1
    assert total('subnet:"10.1.1.0/24" has:planned AND NOT has:tested') == block["planned"]
    assert total('subnet:"10.1.1.0/24" has:untouched') == block["untouched"]
    assert total(
        'subnet:"10.1.1.0/24" AND NOT has:untouched AND NOT has:planned AND NOT has:tested'
    ) == block["worked"]
    assert total('subnet:"10.1.1.0/24" has:untouched has:critical') == block["critical_untouched"]
