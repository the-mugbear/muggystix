"""Scanner observations grouped by issue, and their bulk promotion (v2.386.0).

An issue carried by many hosts could only be seen, and promoted, from one
host's inspector.  The list shows each issue once with its host count and how
many of those a finding already covers; promotion takes several issues at
once, each on every host carrying it or on the hosts the operator ticked.
"""
from datetime import datetime, timezone

import pytest

from app.db import models
from app.db.models import Host
from app.db.models_findings import Finding, FindingHost, FindingStatus
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity


def _host(db, project_id, ip):
    h = Host(project_id=project_id, ip_address=ip, state="up",
             first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc))
    db.add(h)
    db.commit()
    return h


def _vuln(db, host, scan_id, title, severity=VulnerabilitySeverity.MEDIUM, **over):
    v = Vulnerability(host_id=host.id, scan_id=scan_id, title=title, severity=severity,
                      source=over.pop("source", "nessus"), **over)
    db.add(v)
    db.commit()
    return v


@pytest.fixture
def estate(db_session, test_project):
    """SMB signing on 3 hosts, TLS 1.0 on 2, one critical CVE on 1."""
    scan = models.Scan(project_id=test_project.id, filename="n.nessus", scan_type="nessus", tool_name="nessus")
    db_session.add(scan)
    db_session.commit()
    hosts = [_host(db_session, test_project.id, f"10.9.0.{i}") for i in range(1, 4)]
    for h in hosts:
        _vuln(db_session, h, scan.id, "SMB Signing not required")
    for h in hosts[:2]:
        _vuln(db_session, h, scan.id, "TLS Version 1.0 Protocol Detection", severity=VulnerabilitySeverity.LOW)
    # The same issue in another scanner's words: one issue, not two.
    _vuln(db_session, hosts[1], scan.id, "OpenVAS: SMB signing not required!", source="openvas")
    _vuln(db_session, hosts[0], scan.id, "OpenSSH agent RCE", severity=VulnerabilitySeverity.CRITICAL,
          cve_id="CVE-2023-38408")
    return {"hosts": hosts, "scan": scan}


def _url(project, tail=""):
    return f"/api/v1/projects/{project.id}/scanner-observations{tail}"


def test_one_row_per_issue_most_severe_first(client, test_project, estate):
    body = client.get(_url(test_project)).json()
    rows = [(r["title"], r["severity"], r["host_count"], r["judged_host_count"]) for r in body["items"]]
    assert body["total"] == 3
    assert rows[0][1:] == ("critical", 1, 0)
    smb = next(r for r in body["items"] if r["issue_key"] == "title:smb signing not required")
    # The OpenVAS row for the same issue is the same row here, not a fourth host.
    assert (smb["host_count"], sorted(smb["sources"])) == (3, ["nessus", "openvas"])
    assert rows[2][1:] == ("low", 2, 0)


def test_min_hosts_and_search_and_severity(client, test_project, estate):
    shared = client.get(_url(test_project), params={"min_hosts": 2}).json()
    assert {r["severity"] for r in shared["items"]} == {"medium", "low"}
    assert client.get(_url(test_project), params={"search": "CVE-2023"}).json()["total"] == 1
    assert client.get(_url(test_project), params={"severity": "low"}).json()["total"] == 1
    assert client.get(_url(test_project), params={"severity": "bogus"}).status_code == 422


def test_hosts_of_one_issue(client, test_project, estate):
    hosts = client.get(_url(test_project, "/hosts"), params={"issue_key": "title:smb signing not required"}).json()
    assert [h["ip_address"] for h in hosts] == ["10.9.0.1", "10.9.0.2", "10.9.0.3"]
    assert not any(h["judged"] for h in hosts)


def test_bulk_promote_all_hosts_and_a_subset(client, db_session, test_project, estate):
    h1, h2, h3 = estate["hosts"]
    r = client.post(_url(test_project, "/promote"), json={"items": [
        {"issue_key": "title:smb signing not required"},
        {"issue_key": "title:tls version 1.0 protocol detection", "host_ids": [h2.id]},
    ]})
    assert r.status_code == 200, r.text
    results = {o["issue_key"]: o for o in r.json()["results"]}
    smb = db_session.get(Finding, results["title:smb signing not required"]["finding_id"])
    tls = db_session.get(Finding, results["title:tls version 1.0 protocol detection"]["finding_id"])
    assert smb.status == FindingStatus.CONFIRMED.value
    assert {fh.host_id for fh in db_session.query(FindingHost).filter_by(finding_id=smb.id)} == {h1.id, h2.id, h3.id}
    # Only the ticked host is recorded as confirmed.
    assert {fh.host_id for fh in db_session.query(FindingHost).filter_by(finding_id=tls.id)} == {h2.id}

    # The fully covered issue leaves the waiting list; the partly covered one stays, 1 of 2 judged.
    listed = {r["issue_key"]: r for r in client.get(_url(test_project)).json()["items"]}
    assert "title:smb signing not required" not in listed
    tls_row = listed["title:tls version 1.0 protocol detection"]
    assert (tls_row["judged_host_count"], tls_row["finding_id"]) == (1, tls.id)
    everything = client.get(_url(test_project), params={"include_judged": True}).json()
    assert everything["total"] == 3


def test_promoting_again_joins_and_never_changes_status(client, db_session, test_project, estate):
    h1, h2, _ = estate["hosts"]
    key = "title:tls version 1.0 protocol detection"
    first = client.post(_url(test_project, "/promote"), json={"items": [{"issue_key": key, "host_ids": [h1.id]}]})
    fid = first.json()["results"][0]["finding_id"]
    finding = db_session.get(Finding, fid)
    finding.status = FindingStatus.REMEDIATED.value
    db_session.commit()

    again = client.post(_url(test_project, "/promote"), json={"items": [{"issue_key": key}]}).json()["results"][0]
    assert (again["finding_id"], again["created"]) == (fid, False)
    db_session.expire_all()
    assert db_session.get(Finding, fid).status == FindingStatus.REMEDIATED.value
    assert {fh.host_id for fh in db_session.query(FindingHost).filter_by(finding_id=fid)} == {h1.id, h2.id}


def test_the_hosts_of_an_issue_can_be_limited(client, test_project, estate):
    hosts = client.get(_url(test_project, "/hosts"),
                       params={"issue_key": "title:smb signing not required", "limit": 2}).json()
    assert [h["ip_address"] for h in hosts] == ["10.9.0.1", "10.9.0.2"]


def test_a_long_title_keyed_issue_can_be_promoted(client, db_session, test_project, estate):
    """Review 2026-09-23 C5: ``findings.dedup_key`` was 255 wide while the
    issue key it must equal is 600, so a Nikto-style title (URI + message)
    over 255 characters raised StringDataRightTruncation on promote."""
    h1 = estate["hosts"][0]
    title = "/cgi-bin/" + "a" * 280 + ": the server returns a verbose error page"
    _vuln(db_session, h1, estate["scan"].id, title, source="nikto")
    key = next(r["issue_key"] for r in client.get(_url(test_project)).json()["items"]
               if r["title"] == title)
    assert len(key) > 255

    r = client.post(_url(test_project, "/promote"), json={"items": [{"issue_key": key}]})
    assert r.status_code == 200, r.text
    finding = db_session.get(Finding, r.json()["results"][0]["finding_id"])
    # Stored whole, so it still matches its observation: the issue is judged.
    assert finding.dedup_key == key
    listed = {row["issue_key"] for row in client.get(_url(test_project)).json()["items"]}
    assert key not in listed


def test_a_bad_item_changes_nothing(client, db_session, test_project, estate):
    h3 = estate["hosts"][2]
    r = client.post(_url(test_project, "/promote"), json={"items": [
        {"issue_key": "title:smb signing not required"},
        # h3 does not carry TLS 1.0.
        {"issue_key": "title:tls version 1.0 protocol detection", "host_ids": [h3.id]},
    ]})
    assert r.status_code == 422
    assert "do not carry" in r.json()["detail"]
    assert db_session.query(Finding).filter_by(project_id=test_project.id).count() == 0
    unknown = client.post(_url(test_project, "/promote"), json={"items": [{"issue_key": "title:nope"}]})
    assert unknown.status_code == 422


def test_the_issue_filter_lists_exactly_its_hosts(client, test_project, estate):
    """The "all N hosts" link of an issue too long to list under its row."""
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/",
                   params={"q": 'issue:"title:tls version 1.0 protocol detection"'})
    assert r.status_code == 200, r.text
    body = r.json()
    items = body["items"] if isinstance(body, dict) else body
    assert sorted(h["ip_address"] for h in items) == ["10.9.0.1", "10.9.0.2"]
