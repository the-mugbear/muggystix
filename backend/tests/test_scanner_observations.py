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
from app.db.models_project import Project
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from tests.conftest import USING_POSTGRES


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


@pytest.mark.skipif(not USING_POSTGRES, reason="the ordered aggregate is Postgres-only; SQLite keeps min(title)")
def test_title_and_severity_come_from_the_same_row(client, db_session, test_project):
    """v2.402.0: ``min(title)`` beside ``max(severity)`` showed a low row's
    title at another row's medium severity.  The title is the most severe
    row's (tie → lowest id)."""
    scan = models.Scan(project_id=test_project.id, filename="n.nessus", scan_type="nessus", tool_name="nessus")
    db_session.add(scan)
    db_session.commit()
    a, b = _host(db_session, test_project.id, "10.9.1.1"), _host(db_session, test_project.id, "10.9.1.2")
    _vuln(db_session, a, scan.id, "A low wording", severity=VulnerabilitySeverity.LOW, cve_id="CVE-2023-9674")
    _vuln(db_session, b, scan.id, "B medium wording", severity=VulnerabilitySeverity.MEDIUM, cve_id="CVE-2023-9674")
    # A later medium row that sorts first alphabetically: the tie goes to the lowest id.
    _vuln(db_session, a, scan.id, "Ab medium later", severity=VulnerabilitySeverity.MEDIUM, cve_id="CVE-2023-9674")
    row = client.get(_url(test_project)).json()["items"][0]
    assert (row["issue_key"], row["title"], row["severity"], row["cve_id"]) == (
        "cve:CVE-2023-9674", "B medium wording", "medium", "CVE-2023-9674")


def test_min_hosts_and_search_and_severity(client, test_project, estate):
    shared = client.get(_url(test_project), params={"min_hosts": 2}).json()
    assert {r["severity"] for r in shared["items"]} == {"medium", "low"}
    assert client.get(_url(test_project), params={"search": "CVE-2023"}).json()["total"] == 1
    # A wildcard typed into the search is text (review 2026-10-07): "%" used
    # to match every issue and "_" any one character.
    everything = client.get(_url(test_project)).json()["total"]
    assert everything > 1
    assert client.get(_url(test_project), params={"search": "%"}).json()["total"] == 0
    assert client.get(_url(test_project), params={"search": "CVE_2023"}).json()["total"] == 0
    assert client.get(_url(test_project), params={"severity": "low"}).json()["total"] == 1
    assert client.get(_url(test_project), params={"severity": "bogus"}).status_code == 422


def test_hosts_of_one_issue(client, test_project, estate):
    hosts = client.get(_url(test_project, "/hosts"), params={"issue_key": "title:smb signing not required"}).json()
    assert [h["ip_address"] for h in hosts] == ["10.9.0.1", "10.9.0.2", "10.9.0.3"]
    assert not any(h["judged"] for h in hosts)


def test_exploitable_narrows_to_issues_a_scanner_reports_an_exploit_for(client, db_session, test_project, estate):
    """Plan A7.  One exploitable row on ONE of an issue's hosts makes the issue
    exploitable; every row says which it is; the agents' read takes the same
    word and gives the same rows."""
    _vuln(db_session, estate["hosts"][2], estate["scan"].id, "SMB Signing not required", exploitable=True,
          source="openvas")
    everything = client.get(_url(test_project)).json()["items"]
    assert {r["issue_key"]: r["exploitable"] for r in everything} == {
        "title:smb signing not required": True,
        "title:tls version 1.0 protocol detection": False,
        "cve:CVE-2023-38408": False,
    }
    only = client.get(_url(test_project), params={"exploitable": "true"}).json()
    assert only["total"] == 1 and [r["issue_key"] for r in only["items"]] == ["title:smb signing not required"]
    assert only["items"][0]["host_count"] == 3  # the issue's hosts, not only the exploitable row's

    key = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={}).json()["api_key"]
    agents = client.get("/api/v1/agent/assist/scanner-observations", params={"exploitable": "true"},
                        headers={"X-API-Key": key}).json()
    assert agents["total"] == 1 and agents["items"] == only["items"]


def test_each_host_of_an_issue_says_where_testing_of_it_stands(client, db_session, test_project, estate):
    """Plan A7.  Tests that name THIS issue on the host: still to do, and
    results recorded.  A test of another issue, a dismissed test and a failed
    attempt count for nothing."""
    from app.db.models_host_tests import HostTest
    from app.db.models_proposals import EvidenceRecord

    smb = "title:smb signing not required"
    a, b, c = estate["hosts"]

    made = []

    def test_row(host, status, issue=smb):
        made.append(1)
        key = f"a7-{len(made)}"
        t = HostTest(project_id=test_project.id, host_id=host.id, tool="nxc", description="d", command="c",
                     rationale="r", status=status, issue_key=issue, issue_title="t", source="person",
                     request_key=key, request_hash=key)
        db_session.add(t)
        db_session.flush()
        return t

    test_row(a, "proposed")
    test_row(a, "in_progress")
    test_row(a, "dismissed")
    test_row(a, "proposed", issue="title:tls version 1.0 protocol detection")
    done = test_row(b, "done")
    for outcome in ("finding", "failed"):
        db_session.add(EvidenceRecord(project_id=test_project.id, host_id=b.id, host_test_id=done.id, tool="nxc",
                                      outcome=outcome, summary="s", executed_at=datetime.now(timezone.utc)))
    db_session.commit()

    hosts = client.get(_url(test_project, "/hosts"), params={"issue_key": smb}).json()
    assert {h["ip_address"]: (h["tests_to_do"], h["tests_recorded"]) for h in hosts} == {
        "10.9.0.1": (2, 0), "10.9.0.2": (0, 1), "10.9.0.3": (0, 0),
    }


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


def test_paging_through_issues_that_tie_lists_each_once(client, db_session, test_project):
    """The list is ordered by severity, hosts and title — which TIE for issues
    that differ only by CVE.  Without a last, unique sort key the database may
    order tied rows differently for each page, so "Show more" could repeat an
    issue or skip one although nothing changed (v2.474.1: the issue key is the
    last key).  Thirty issues with one title, one severity and one host each,
    read five at a time: every issue exactly once, in the same order as one
    read of them all."""
    scan = models.Scan(project_id=test_project.id, filename="t.nessus", scan_type="nessus", tool_name="nessus")
    db_session.add(scan)
    db_session.commit()
    host = _host(db_session, test_project.id, "10.9.5.1")
    for n in range(30):
        _vuln(db_session, host, scan.id, "Kernel update available", cve_id=f"CVE-2024-{1000 + n}",
              plugin_id=str(200000 + n))

    whole = client.get(_url(test_project), params={"limit": 100}).json()
    assert whole["total"] == 30
    in_one_read = [row["issue_key"] for row in whole["items"]]
    assert len(set(in_one_read)) == 30

    paged = []
    for skip in range(0, 30, 5):
        page = client.get(_url(test_project), params={"limit": 5, "skip": skip}).json()
        assert page["total"] == 30
        paged.extend(row["issue_key"] for row in page["items"])
    assert paged == in_one_read


def test_what_an_observation_says_can_be_read_without_opening_a_host(client, db_session, test_project, estate):
    """The list showed a title, a CVE and a host count; what the scanner SAID
    was only on a host's page (analyst walkthrough 2026-10-10).  The detail is
    the issue's most severe row that has a description, and names the scanner
    whose words they are."""
    hosts, scan = estate["hosts"], estate["scan"]
    # A more severe row of the same issue with nothing written, and a described one.
    _vuln(db_session, hosts[2], scan.id, "Apache Struts RCE", severity=VulnerabilitySeverity.CRITICAL,
          cve_id="CVE-2017-5638")
    _vuln(db_session, hosts[1], scan.id, "Apache Struts RCE", severity=VulnerabilitySeverity.HIGH,
          cve_id="CVE-2017-5638", source="openvas", description="The Jakarta parser mishandles Content-Type.",
          solution="Upgrade Struts.", cvss_score=9.8, cvss_vector="CVSS:3.1/AV:N/AC:L",
          plugin_id="1.3.6.1.4.1.25623.1.0.140180", source_plugin_name="Apache Struts2 Jakarta RCE",
          references='["https://cwe.mitre.org/data/definitions/20.html", "CVE-2017-5638"]')
    key = next(r["issue_key"] for r in client.get(_url(test_project)).json()["items"] if r["cve_id"] == "CVE-2017-5638")

    body = client.get(_url(test_project, "/detail"), params={"issue_key": key})
    assert body.status_code == 200, body.text
    # v2.508.0 — the inspector: the same row's identifiers and references.
    assert body.json() == {
        "issue_key": key, "title": "Apache Struts RCE", "source": "openvas",
        "description": "The Jakarta parser mishandles Content-Type.", "solution": "Upgrade Struts.",
        "cvss_score": 9.8, "cvss_vector": "CVSS:3.1/AV:N/AC:L",
        "cve_id": "CVE-2017-5638", "plugin_id": "1.3.6.1.4.1.25623.1.0.140180",
        "plugin_name": "Apache Struts2 Jakarta RCE",
        "references": ["https://cwe.mitre.org/data/definitions/20.html", "CVE-2017-5638"],
    }

    # Nothing written by any scanner: the row is named, the texts are null.
    bare = next(r["issue_key"] for r in client.get(_url(test_project)).json()["items"] if r["cve_id"] == "CVE-2023-38408")
    answer = client.get(_url(test_project, "/detail"), params={"issue_key": bare}).json()
    assert (answer["title"], answer["description"], answer["solution"]) == ("OpenSSH agent RCE", None, None)

    assert client.get(_url(test_project, "/detail"), params={"issue_key": "title:no such issue"}).status_code == 404


def test_an_observation_is_dismissed_from_the_list_on_the_ticked_hosts_or_on_all(client, db_session, test_project, estate):
    """The list offered Promote alone: a false positive could only be marked
    from one of the observation's hosts (analyst walkthrough 2026-10-10; the
    owner: "a dismissal can apply to all other hosts that have the same issue
    … let's allow it").  It is the host page's own dismissal — this host(s)
    only, or every host carrying it — reached from the list."""
    hosts = estate["hosts"]
    rows = {r["title"]: r for r in client.get(_url(test_project)).json()["items"]}
    smb, tls = rows["SMB Signing not required"], rows["TLS Version 1.0 Protocol Detection"]

    # Two of SMB's three hosts: those endpoints are false positives (on the
    # issue's finding, made for them if there was none) and the third host
    # still waits.
    some = client.post(_url(test_project, "/dismiss"), json={
        "issue_key": smb["issue_key"], "host_ids": [hosts[0].id, hosts[1].id], "reason": "signing is enforced by GPO"})
    assert some.status_code == 200, some.text
    assert some.json()["host_count"] == 2
    finding = db_session.get(Finding, some.json()["finding_id"])
    states = dict(db_session.query(FindingHost.host_id, FindingHost.host_status)
                  .filter(FindingHost.finding_id == finding.id).all())
    assert states == {hosts[0].id: "false_positive", hosts[1].id: "false_positive"}
    # The third host is not on the finding, and its row still waits in the list.
    assert hosts[2].id not in states
    after = {r["title"]: r for r in client.get(_url(test_project)).json()["items"]}
    assert (after["SMB Signing not required"]["host_count"], after["SMB Signing not required"]["judged_host_count"]) == (3, 2)

    # No hosts named = every host carrying it: the issue is a false positive.
    whole = client.post(_url(test_project, "/dismiss"), json={"issue_key": tls["issue_key"]})
    assert whole.status_code == 200, whole.text
    assert whole.json()["host_count"] == 2
    db_session.expire_all()
    assert db_session.get(Finding, whole.json()["finding_id"]).status == FindingStatus.FALSE_POSITIVE.value
    left = {r["title"]: (r["host_count"], r["judged_host_count"]) for r in client.get(_url(test_project)).json()["items"]}
    assert "TLS Version 1.0 Protocol Detection" not in left, left
    # The history says what happened: the reason given, or — none given — that
    # it was a dismissal (it read "Promoted from scanner vulnerability").
    def trail(finding_id):
        return [h["summary"] for h in client.get(
            f"/api/v1/projects/{test_project.id}/findings/{finding_id}/history").json()]
    assert trail(whole.json()["finding_id"]) == ["Dismissed as a false positive (scanner observation)"]
    assert "signing is enforced by GPO" in " ".join(trail(some.json()["finding_id"]))

    # Refused whole: an unknown observation, a host that does not carry it, an empty list.
    for body in ({"issue_key": "title:no such"},
                 {"issue_key": smb["issue_key"], "host_ids": [999999]},
                 {"issue_key": smb["issue_key"], "host_ids": []}):
        assert client.post(_url(test_project, "/dismiss"), json=body).status_code == 422, body


def test_another_projects_observation_has_no_detail_here(client, db_session, test_project, estate):
    other = Project(name="Other", slug="other-obs-detail")
    db_session.add(other)
    db_session.commit()
    host = _host(db_session, other.id, "10.200.0.1")
    theirs = _vuln(db_session, host, None, "Only in the other project", description="secret")
    assert theirs.issue_key
    assert client.get(_url(test_project, "/detail"), params={"issue_key": theirs.issue_key}).status_code == 404


def test_what_a_scanner_printed_for_one_host_is_read_from_the_inspector(client, db_session, test_project, estate):
    """2026-10-16, the Scanner observations inspector: opening a host shows
    that host's rows of the issue as the host page serializes them — port,
    scanner and what it printed THERE — so a reader judges the issue host by
    host without leaving the list.  Another host's, or another issue's, rows
    are not in it."""
    hosts, scan = estate["hosts"], estate["scan"]
    port = models.Port(host_id=hosts[0].id, port_number=445, protocol="tcp", state="open", service_name="microsoft-ds")
    db_session.add(port)
    db_session.commit()
    smb = _vuln(db_session, hosts[0], scan.id, "SMB Signing not required", port_id=port.id,
                plugin_output="Message signing required: false")
    key = smb.issue_key
    rows = client.get(_url(test_project, "/host-output"), params={"issue_key": key, "host_id": hosts[0].id})
    assert rows.status_code == 200, rows.text
    body = rows.json()
    # The fixture's host-level row and the port row, most severe first, host-level first.
    assert [(r["port_number"], r["protocol"], r["plugin_output"]) for r in body] == [
        (None, None, None), (445, "tcp", "Message signing required: false"),
    ]
    assert {r["issue_key"] for r in body} == {key}
    # A host that carries another issue only, and another project's host: nothing.
    tls_only = client.get(_url(test_project, "/host-output"), params={"issue_key": "cve:CVE-2023-38408", "host_id": hosts[2].id})
    assert tls_only.json() == []
    other = Project(name="Other", slug="other-obs-output")
    db_session.add(other)
    db_session.commit()
    theirs = _host(db_session, other.id, "10.201.0.1")
    _vuln(db_session, theirs, None, "SMB Signing not required", plugin_output="theirs")
    assert client.get(_url(test_project, "/host-output"), params={"issue_key": key, "host_id": theirs.id}).json() == []


def test_the_agents_read_the_inspectors_detail_from_the_same_service(client, db_session, test_project, estate):
    """Agent parity (v2.508.0): what the inspector shows about an issue is an
    agent read too — the page's own ``issue_detail``, field for field."""
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    key = {"X-API-Key": r.json()["api_key"]}
    issue = next(i for i in client.get(_url(test_project)).json()["items"] if i["cve_id"] == "CVE-2023-38408")
    page = client.get(_url(test_project, "/detail"), params={"issue_key": issue["issue_key"]}).json()
    agent = client.get("/api/v1/agent/assist/scanner-observations/detail", headers=key,
                       params={"issue_key": issue["issue_key"]})
    assert agent.status_code == 200, agent.text
    assert agent.json() == page
    missing = client.get("/api/v1/agent/assist/scanner-observations/detail", headers=key,
                         params={"issue_key": "title:no such issue"})
    assert missing.status_code == 404


def _whole_issue_reads(db_session, run):
    """The statements ``run`` makes that read scanner rows WHOLE (their plugin
    output) by the issue rather than by row id."""
    from sqlalchemy import event

    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if (statement.lstrip().upper().startswith("SELECT") and "vulnerabilities.plugin_output" in statement
                and "issue_key" in statement.split("WHERE", 1)[-1]):
            seen.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        run()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return seen


def test_promote_and_dismiss_read_only_the_rows_they_act_on_whole(client, db_session, test_project, estate):
    """R5 (2026-10-22): both loaded every row of the issue whole — description
    and plugin output — to use three columns.  The choice is made from
    ``(id, host_id, severity)``; only the evidencing rows are read whole, by
    id.  Fails on the old code: its whole-row read named the issue."""
    hosts = estate["hosts"]
    keys = {r["title"]: r["issue_key"] for r in client.get(_url(test_project)).json()["items"]}

    def promote():
        r = client.post(_url(test_project, "/promote"), json={"items": [
            {"issue_key": keys["SMB Signing not required"]},
            {"issue_key": keys["TLS Version 1.0 Protocol Detection"], "host_ids": [hosts[1].id]},
        ]})
        assert r.status_code == 200, r.text

    def dismiss():
        r = client.post(_url(test_project, "/dismiss"), json={
            "issue_key": keys["OpenSSH agent RCE"], "host_ids": [hosts[0].id]})
        assert r.status_code == 200, r.text

    assert _whole_issue_reads(db_session, promote) == []
    assert _whole_issue_reads(db_session, dismiss) == []


def test_a_host_list_longer_than_the_page_can_send_is_refused_on_both_routes(client, db_session, test_project, estate):
    """R5: ``host_ids`` allowed 10,000 at several statements per host, while
    the page sends at most the inspector's 100.  More is a 422 whole on the
    promotion and the dismissal, and nothing is written."""
    from app.services import scanner_observation_service as svc

    hosts = estate["hosts"]
    smb = "title:smb signing not required"
    too_many = [hosts[0].id] * (svc.HOST_IDS_CAP + 1)
    promote = client.post(_url(test_project, "/promote"), json={"items": [{"issue_key": smb, "host_ids": too_many}]})
    dismiss = client.post(_url(test_project, "/dismiss"), json={"issue_key": smb, "host_ids": too_many})
    assert (promote.status_code, dismiss.status_code) == (422, 422)
    assert db_session.query(Finding).filter_by(project_id=test_project.id).count() == 0
    # The service says so too, for a caller that skips the schema.
    with pytest.raises(svc.ObservationError, match=f"At most {svc.HOST_IDS_CAP} hosts"):
        svc.dismiss_issue(db_session, test_project.id, None, smb, host_ids=too_many)
    with pytest.raises(svc.ObservationError, match=f"At most {svc.HOST_IDS_CAP} hosts"):
        svc.promote_issues(db_session, test_project.id, None, [svc.PromoteItem(issue_key=smb, host_ids=too_many)])
    # Exactly the cap is accepted (a repeated id counts once).
    ok = client.post(_url(test_project, "/promote"), json={"items": [
        {"issue_key": smb, "host_ids": [hosts[0].id] * svc.HOST_IDS_CAP}]})
    assert ok.status_code == 200, ok.text
