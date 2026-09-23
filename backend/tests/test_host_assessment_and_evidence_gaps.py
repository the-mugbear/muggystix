"""Evidence freshness beside the assertion (v2.348.0; design review item 4).

* The host detail carries an ``assessment`` block: per domain, when evidence
  was last gathered, or that it never was, or that it does not apply.
* Open ports the latest sweep did not see are counted, so "open" is not
  taken to be as fresh as the host's newest observation.
* A coverage gap on the Evidence page opens into the affected hosts with the
  ports that made them eligible and the step that closes the gap.
"""
from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models_agent import (
    TestExecutionResult, TestExecutionStatus, TestPlan, TestPlanEntry, ExecutionSession,
)
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.evidence_service import evidence_gap_hosts
from app.services.host_assessment_service import host_assessment


def _host(db, project_id, ip, last_seen, ports=()):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    h.last_seen = last_seen
    db.add(h)
    db.flush()
    for number, seen in ports:
        p = models.Port(host_id=h.id, port_number=number, protocol="tcp", state="open")
        p.last_seen = seen
        db.add(p)
    db.flush()
    db.refresh(h)
    return h


def test_assessment_says_not_assessed_and_counts_ports_missed_by_the_latest_sweep(db_session, test_project):
    now = datetime.now(timezone.utc)
    h = _host(db_session, test_project.id, "10.8.0.1", now, ports=[
        (443, now),                          # seen by the latest sweep
        (8080, now - timedelta(days=12)),    # not seen since
        (445, now - timedelta(minutes=10)),  # same sweep, parser stamped later
    ])
    db_session.commit()

    a = host_assessment(db_session, h)
    assert a["last_observed_at"] is not None
    assert a["vuln_assessed"] is False and a["last_vuln_assessed_at"] is None
    assert a["web_eligible"] is True and a["web_assessed"] is False
    assert a["auth_eligible"] is True and a["auth_assessed"] is False
    assert a["tests_executed"] == 0 and a["last_tested_at"] is None
    assert a["conflicts"] == 0
    assert a["open_ports_not_in_latest_scan"] == 1


def test_assessment_dates_each_domain_from_its_own_evidence(db_session, test_project, test_user, test_agent):
    now = datetime.now(timezone.utc)
    h = _host(db_session, test_project.id, "10.8.0.2", now, ports=[(22, now)])
    scan = models.Scan(project_id=test_project.id, filename="v.nessus", tool_name="nessus")
    db_session.add(scan)
    db_session.flush()
    v = Vulnerability(host_id=h.id, scan_id=scan.id, title="v", severity=VulnerabilitySeverity.HIGH,
                      source=VulnerabilitySource.NESSUS)
    v.last_seen = now - timedelta(days=5)
    db_session.add(v)
    h.smb_signing = "disabled"
    plan = TestPlan(project_id=test_project.id, title="p", agent_id=test_agent.id, created_by_user_id=test_user.id)
    db_session.add(plan)
    db_session.flush()
    entry = TestPlanEntry(test_plan_id=plan.id, host_id=h.id, priority="high", test_phase="enumeration",
                          proposed_tests=[], rationale="r")
    db_session.add(entry)
    db_session.flush()
    session = ExecutionSession(test_plan_id=plan.id, agent_id=test_agent.id)
    db_session.add(session)
    db_session.flush()
    res = TestExecutionResult(execution_session_id=session.id, entry_id=entry.id, test_index=0,
                              status=TestExecutionStatus.EXECUTED.value)
    res.executed_at = now - timedelta(days=90)
    db_session.add(res)
    db_session.commit()
    db_session.refresh(h)

    a = host_assessment(db_session, h)
    assert a["vuln_assessed"] is True
    assert abs((a["last_vuln_assessed_at"].replace(tzinfo=timezone.utc) - (now - timedelta(days=5))).total_seconds()) < 5
    assert a["web_eligible"] is False and a["auth_eligible"] is False
    assert a["auth_assessed"] is True  # smb_signing recorded
    assert a["tests_executed"] == 1
    assert abs((a["last_tested_at"].replace(tzinfo=timezone.utc) - (now - timedelta(days=90))).total_seconds()) < 5


def test_host_detail_carries_the_assessment(client, db_session, test_project):
    h = _host(db_session, test_project.id, "10.8.0.3", datetime.now(timezone.utc), ports=[(80, datetime.now(timezone.utc))])
    db_session.commit()
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{h.id}")
    assert r.status_code == 200, r.text
    a = r.json()["assessment"]
    assert a["web_eligible"] is True and a["web_assessed"] is False
    assert a["vuln_assessed"] is False
    # The port's own observation window rides on the port row so the table
    # can say whether the latest sweep saw it (the response model used to
    # strip both timestamps).
    (port,) = r.json()["ports"]
    assert port["last_seen"] is not None and port["first_seen"] is not None


def test_evidence_gap_lists_eligible_unassessed_hosts_with_their_ports(client, db_session, test_project):
    now = datetime.now(timezone.utc)
    web_gap = _host(db_session, test_project.id, "10.8.1.1", now, ports=[(443, now), (8443, now), (22, now)])
    _host(db_session, test_project.id, "10.8.1.2", now, ports=[(22, now)])  # not eligible: no web port
    web_done = _host(db_session, test_project.id, "10.8.1.3", now, ports=[(80, now)])
    scan = models.Scan(project_id=test_project.id, filename="h.json", tool_name="httpx")
    db_session.add(scan)
    db_session.flush()
    db_session.add(models.WebInterface(project_id=test_project.id, host_id=web_done.id, scan_id=scan.id,
                                       url="http://10.8.1.3/", source="httpx"))
    db_session.commit()

    gap = evidence_gap_hosts(db_session, test_project.id, "web_tls")
    assert gap["label"] == "Web / TLS"
    assert gap["total"] == 1
    assert gap["items"] == [{"host_id": web_gap.id, "ip_address": "10.8.1.1", "hostname": None, "ports": [443, 8443]}]
    assert gap["action"]["kind"] == "collect"
    assert "httpx" in gap["action"]["text"]

    assert evidence_gap_hosts(db_session, test_project.id, "nope") is None

    r = client.get(f"/api/v1/projects/{test_project.id}/posture/evidence/web_tls/gaps")
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["ip_address"] == "10.8.1.1"
    assert client.get(f"/api/v1/projects/{test_project.id}/posture/evidence/nope/gaps").status_code == 404


def test_evidence_matrix_locates_the_gap_and_a_cell_opens_exactly_its_hosts(client, db_session, test_project):
    """v2.374.0 — the project total says web/TLS is 1-of-3 covered; the matrix
    says WHERE the other two are, including a host outside every scoped subnet
    (Evidence covers all hosts, so it is a column, not an omission)."""
    from app.services.evidence_service import compute_evidence_coverage

    now = datetime.now(timezone.utc)
    scope = models.Scope(project_id=test_project.id, name="s")
    db_session.add(scope)
    db_session.flush()
    a = models.Subnet(scope_id=scope.id, cidr="10.8.3.0/24")
    b = models.Subnet(scope_id=scope.id, cidr="10.8.4.0/24")
    db_session.add_all([a, b])
    db_session.flush()

    def web_host(ip, subnet=None):
        h = _host(db_session, test_project.id, ip, now, ports=[(443, now)])
        if subnet is not None:
            db_session.add(models.HostSubnetMapping(host_id=h.id, subnet_id=subnet.id))
        return h

    done = web_host("10.8.3.1", a)
    web_host("10.8.3.2", a)
    web_host("192.168.9.9")                       # outside every scoped subnet
    _host(db_session, test_project.id, "10.8.4.1", now, ports=[(22, now)])  # b: no web port -> n/a
    db_session.add(models.HostSubnetMapping(
        host_id=db_session.query(models.Host).filter_by(ip_address="10.8.4.1").one().id, subnet_id=b.id))
    scan = models.Scan(project_id=test_project.id, filename="h.json", tool_name="httpx")
    db_session.add(scan)
    db_session.flush()
    db_session.add(models.WebInterface(project_id=test_project.id, host_id=done.id, scan_id=scan.id,
                                       url="https://10.8.3.1/", source="httpx"))
    db_session.commit()

    out = compute_evidence_coverage(db_session, test_project.id)
    m = out["matrix"]
    assert m["group_by"] == "subnet"          # no sites defined
    assert [s["label"] for s in m["segments"]] == ["10.8.3.0/24", "10.8.4.0/24", "Outside scoped subnets"]
    web = next(r for r in m["rows"] if r["domain"] == "web_tls")
    assert [(c["eligible"], c["assessed"], c["gap"]) for c in web["cells"]] == [(2, 1, 1), (0, 0, 0), (1, 0, 1)]
    # The columns are disjoint and complete: they sum to the project figure.
    total = next(d for d in out["domains"] if d["key"] == "web_tls")["coverage"]
    assert sum(c["eligible"] for c in web["cells"]) == total["denominator"] == 3
    assert sum(c["assessed"] for c in web["cells"]) == total["numerator"] == 1

    base = f"/api/v1/projects/{test_project.id}/posture/evidence/web_tls/gaps"
    r = client.get(base, params={"segment": m["segments"][0]["key"]})
    assert r.status_code == 200, r.text
    assert [i["ip_address"] for i in r.json()["items"]] == ["10.8.3.2"]
    assert r.json()["segment_label"] == "10.8.3.0/24"
    assert r.json()["action"]["kind"] == "collect"
    r = client.get(base, params={"segment": "unmapped"})
    assert [i["ip_address"] for i in r.json()["items"]] == ["192.168.9.9"]
    # v2.374.3 — the project has a declared scope and this host is outside it:
    # the server does not advise running a tool against it.
    assert r.json()["action"]["kind"] == "confirm_scope"
    assert "httpx" not in r.json()["action"]["text"]
    assert client.get(base, params={"segment": "subnet:999999"}).status_code == 404


def test_scope_advice_comes_from_the_declared_scope_not_the_matrix_columns(db_session, test_project):
    """2.374.4 review H7: the advice was read off the matrix — only the
    "unmapped" column, only when other columns existed — so a domain-only
    scope, a host reached through an in-scope name, or a whole-project list
    got collection advice about hosts nobody confirmed are authorized."""
    from app.services import dns_name_service

    now = datetime.now(timezone.utc)
    scope = models.Scope(project_id=test_project.id, name="s")
    db_session.add(scope)
    db_session.flush()
    # Domain-only scope: no subnet at all, so the matrix has only "unmapped".
    db_session.add(models.ScopeDomain(scope_id=scope.id, domain="example.com", include_subdomains=True))
    db_session.flush()
    _host(db_session, test_project.id, "203.0.113.10", now, ports=[(443, now)])   # via www.example.com
    _host(db_session, test_project.id, "198.51.100.10", now, ports=[(443, now)])  # out of scope
    dns_name_service.record_observation(
        db_session, project_id=test_project.id, name="www.example.com", record_type="A", value="203.0.113.10",
    )
    db_session.commit()

    # Whole project: one of the two is outside — collect advice, with the caution.
    gap = evidence_gap_hosts(db_session, test_project.id, "web_tls")
    assert (gap["project_has_scope"], gap["outside_scope"], gap["action"]["kind"]) == (True, 1, "collect")
    assert gap["scope_caution"].startswith("1 of these 2 hosts are outside the declared scope")

    # The unmapped column holds both: the name-scoped host is NOT out of scope,
    # so the advice stays "collect" with the same caution — not "confirm" for all.
    cell = evidence_gap_hosts(db_session, test_project.id, "web_tls", segment="unmapped")
    assert (cell["outside_scope"], cell["action"]["kind"]) == (1, "collect")


def test_every_listed_host_outside_the_scope_gets_no_collection_advice(db_session, test_project):
    now = datetime.now(timezone.utc)
    scope = models.Scope(project_id=test_project.id, name="s")
    db_session.add(scope)
    db_session.flush()
    db_session.add(models.ScopeDomain(scope_id=scope.id, domain="example.com", include_subdomains=False))
    _host(db_session, test_project.id, "198.51.100.11", now, ports=[(443, now)])
    db_session.commit()
    gap = evidence_gap_hosts(db_session, test_project.id, "web_tls")
    assert gap["action"]["kind"] == "confirm_scope" and gap["scope_caution"] is None


def test_a_clean_vulnerability_scan_is_an_assessment(db_session, test_project):
    """v2.372.0 — "assessed" used to mean "has a vulnerability row", so a host
    Nessus covered and found clean read as never assessed (and with severity-0
    plugins skipped at ingest a clean host has no rows at all).  The scanner's
    run over the host counts; a port scan's does not."""
    now = datetime.now(timezone.utc)
    clean = _host(db_session, test_project.id, "10.8.2.1", now, ports=[(22, now)])
    only_nmap = _host(db_session, test_project.id, "10.8.2.2", now, ports=[(22, now)])
    # "Nessus" is what the importer stores — the match must not be case-sensitive.
    nessus = models.Scan(project_id=test_project.id, filename="n.nessus", tool_name="Nessus")
    nmap = models.Scan(project_id=test_project.id, filename="n.xml", tool_name="nmap")
    db_session.add_all([nessus, nmap])
    db_session.flush()
    seen = now - timedelta(days=3)
    db_session.add_all([
        models.HostScanHistory(host_id=clean.id, scan_id=nessus.id, discovered_at=seen),
        models.HostScanHistory(host_id=clean.id, scan_id=nmap.id, discovered_at=now),
        models.HostScanHistory(host_id=only_nmap.id, scan_id=nmap.id, discovered_at=now),
    ])
    db_session.commit()

    a = host_assessment(db_session, clean)
    assert a["vuln_assessed"] is True
    # Dated by the scanner's run, not by the later port scan.
    assert a["last_vuln_assessed_at"] == seen
    assert host_assessment(db_session, only_nmap)["vuln_assessed"] is False

    gap = evidence_gap_hosts(db_session, test_project.id, "vuln_assessment")
    assert [i["ip_address"] for i in gap["items"]] == ["10.8.2.2"]
