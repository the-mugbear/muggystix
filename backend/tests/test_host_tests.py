"""Host-test identity, retries, work state and evidence invariants."""
import uuid

import pytest

from app.db import models
from app.db.models_host_tests import HostTest
from app.db.models_project import Project
from app.db.models_proposals import EvidenceRecord
from app.services.dns_name_service import record_observation


@pytest.fixture
def host(db_session, test_project):
    row = models.Host(project_id=test_project.id, ip_address="10.45.0.1", state="up")
    db_session.add(row)
    db_session.commit()
    return row


def item(host, **extra):
    return dict(request_key=str(uuid.uuid4()), host_id=host.id, tool="curl",
                description="Check response headers", rationale="Validate the observed web service", **extra)


def base(project):
    return f"/api/v1/projects/{project.id}/host-tests"


def create(client, project, payload):
    response = client.post(base(project), json={"tests": [payload]})
    assert response.status_code == 201, response.text
    return response.json()["items"][0]


def agent_headers(client, project):
    response = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert response.status_code == 201, response.text
    return {"X-API-Key": response.json()["api_key"]}


def test_retry_is_stable_but_changed_payload_conflicts(client, db_session, test_project, host):
    payload = item(host)
    first = create(client, test_project, payload)
    assert create(client, test_project, payload)["id"] == first["id"]
    payload["description"] = "A different test"
    assert client.post(base(test_project), json={"tests": [payload]}).status_code == 409
    assert db_session.query(HostTest).count() == 1


def test_batch_is_atomic_on_unknown_host(client, db_session, test_project, host):
    valid = item(host)
    invalid = {**item(host), "host_id": 999999}
    assert client.post(base(test_project), json={"tests": [valid, invalid]}).status_code == 404
    assert db_session.query(HostTest).count() == 0


def test_stale_patch_cannot_undo_dismissal(client, test_project, host):
    row = create(client, test_project, item(host))
    url = f"{base(test_project)}/{row['id']}"
    response = client.patch(url, json={"expected_revision": row["revision"], "status": "dismissed", "dismissed_reason": "Out of this engagement"})
    assert response.status_code == 200, response.text
    assert client.patch(url, json={"expected_revision": row["revision"], "status": "in_progress"}).status_code == 409
    assert client.get(url).json()["status"] == "dismissed"


def test_done_without_evidence_requires_explanation(client, test_project, host):
    row = create(client, test_project, item(host))
    url = f"{base(test_project)}/{row['id']}"
    assert client.patch(url, json={"expected_revision": 1, "status": "done"}).status_code == 422
    assert client.patch(url, json={"expected_revision": 1, "status": "done", "tester_summary": "Operator completed outside the recorded session"}).status_code == 200


def test_agent_tests_visible_to_humans_and_evidence_retries(client, db_session, test_project, host):
    headers = agent_headers(client, test_project)
    response = client.post("/api/v1/agent/host-tests", headers=headers, json={"tests": [item(host)]})
    assert response.status_code == 201, response.text
    row = response.json()["items"][0]
    assert row["source"] == "agent" and row["agent_session_id"]
    assert client.get(base(test_project), params={"host_id": host.id}).json()["items"][0]["id"] == row["id"]
    evidence = dict(host_test_id=row["id"], host_id=host.id, request_key="attempt-1", tool="curl",
                    outcome="no_finding", summary="Headers passed", raw_output="ok")
    first = client.post("/api/v1/agent/evidence", headers=headers, json=evidence)
    assert first.status_code == 201, first.text
    second = client.post("/api/v1/agent/evidence", headers=headers, json=evidence)
    assert second.json()["id"] == first.json()["id"]
    assert db_session.query(EvidenceRecord).count() == 1
    listing = client.get(f"/api/v1/projects/{test_project.id}/evidence", params={"host_test_id": row["id"]}).json()
    assert listing["total"] == 1
    assert "raw_output" not in listing["items"][0]


def test_evidence_cannot_link_another_host(client, db_session, test_project, host):
    row = create(client, test_project, item(host))
    other = models.Host(project_id=test_project.id, ip_address="10.45.0.2")
    db_session.add(other)
    db_session.commit()
    response = client.post("/api/v1/agent/evidence", headers=agent_headers(client, test_project), json={
        "host_id": other.id, "host_test_id": row["id"], "request_key": "mismatch", "tool": "curl",
        "outcome": "finding", "summary": "Must not attach to the other host",
    })
    assert response.status_code == 422, response.text


def test_named_evidence_records_actual_binding_once(client, db_session, test_project, host):
    record_observation(db_session, project_id=test_project.id, name="app.example.org", record_type="A", value=host.ip_address)
    db_session.commit()
    row = create(client, test_project, item(host, target_fqdn="app.example.org", command="curl https://{fqdn}/"))
    headers = agent_headers(client, test_project)
    payload = dict(host_id=host.id, host_test_id=row["id"], request_key="named-result", tool="curl",
                   outcome="inconclusive", summary="Reached the named endpoint", observed_ip="10.45.0.9")
    for _ in range(2):
        r = client.post("/api/v1/agent/evidence", headers=headers, json=payload)
        assert r.status_code == 201, r.text
    records = db_session.query(models.DNSRecord).filter(models.DNSRecord.record_type == "TESTED").all()
    assert len(records) == 1 and records[0].value == "10.45.0.9"
    assert records[0].evidence_record_id == r.json()["id"]


def test_cross_project_host_is_rejected(client, db_session, test_project, host):
    project = Project(name="Different project", slug="host-test-other")
    db_session.add(project)
    db_session.flush()
    other = models.Host(project_id=project.id, ip_address="10.90.0.1")
    db_session.add(other)
    db_session.commit()
    assert client.post(base(test_project), json={"tests": [item(other)]}).status_code == 404


# ---------------------------------------------------------------------------
# Who may write, and whose tests a project can see
# ---------------------------------------------------------------------------

def _demote(db_session, project, user, role):
    """Make the fixture user (a global admin) an ordinary member holding
    ``role`` in the project."""
    from app.db.models_auth import UserRole
    from app.db.models_project import ProjectMembership

    user.role = UserRole.MEMBER
    db_session.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
    db_session.commit()


@pytest.mark.parametrize("role", ["auditor", "viewer"])
def test_a_read_only_role_cannot_write_host_tests_but_reads_them(
    client, db_session, test_project, test_user, host, role,
):
    row = create(client, test_project, item(host))  # written while still an admin
    _demote(db_session, test_project, test_user, role)

    assert client.post(base(test_project), json={"tests": [item(host)]}).status_code == 403
    patch = client.patch(f"{base(test_project)}/{row['id']}",
                         json={"expected_revision": row["revision"], "status": "in_progress"})
    assert patch.status_code == 403, patch.text
    db_session.expire_all()
    stored = db_session.query(HostTest).one()
    assert (stored.status, stored.revision) == ("proposed", row["revision"])
    # Reading is theirs.
    listing = client.get(base(test_project))
    assert listing.status_code == 200 and listing.json()["total"] == 1
    assert client.get(f"{base(test_project)}/{row['id']}").status_code == 200


def test_an_auditors_agent_cannot_write_host_tests_or_their_evidence(
    client, db_session, test_project, test_user, host,
):
    """The agent key carries its operator's role, re-checked per request: the
    same 403 as the page, on the propose, the update and the evidence write."""
    row = create(client, test_project, item(host))
    _demote(db_session, test_project, test_user, "auditor")
    headers = agent_headers(client, test_project)  # auditors may start a session

    assert client.post("/api/v1/agent/host-tests", headers=headers,
                       json={"tests": [item(host)]}).status_code == 403
    assert client.patch(f"/api/v1/agent/host-tests/{row['id']}", headers=headers,
                        json={"expected_revision": row["revision"], "status": "done",
                              "tester_summary": "x"}).status_code == 403
    assert client.post("/api/v1/agent/evidence", headers=headers, json={
        "host_id": host.id, "host_test_id": row["id"], "request_key": "aud-1", "tool": "curl",
        "outcome": "finding", "summary": "must not land",
    }).status_code == 403
    db_session.expire_all()
    assert db_session.query(HostTest).count() == 1
    assert db_session.query(EvidenceRecord).count() == 0
    assert client.get("/api/v1/agent/host-tests", headers=headers).json()["total"] == 1


def test_a_viewers_agent_writes_nothing(client, db_session, test_project, test_user, host):
    """A viewer cannot start an agent session at all (auditor floor), so there
    is no key to write with."""
    _demote(db_session, test_project, test_user, "viewer")
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert r.status_code == 403, r.text


def test_another_projects_host_test_is_not_found(client, db_session, test_project, host):
    """A test id from project A is a 404 through project B's page routes and
    through a project-B agent key — read, update and as an evidence target —
    and B's lists never include it."""
    row = create(client, test_project, item(host))
    other = Project(name="Different project", slug="host-test-b")
    db_session.add(other)
    db_session.flush()
    other_host = models.Host(project_id=other.id, ip_address="10.90.0.9", state="up")
    db_session.add(other_host)
    db_session.commit()

    assert client.get(f"{base(other)}/{row['id']}").status_code == 404
    assert client.patch(f"{base(other)}/{row['id']}",
                        json={"expected_revision": row["revision"], "status": "in_progress"}).status_code == 404
    assert client.get(base(other)).json() == {"items": [], "total": 0, "has_more": False}
    assert client.get(base(other), params={"host_id": host.id}).json()["total"] == 0

    headers = agent_headers(client, other)
    assert client.get(f"/api/v1/agent/host-tests/{row['id']}", headers=headers).status_code == 404
    assert client.patch(f"/api/v1/agent/host-tests/{row['id']}", headers=headers,
                        json={"expected_revision": row["revision"], "status": "in_progress"}).status_code == 404
    assert client.get("/api/v1/agent/host-tests", headers=headers).json()["total"] == 0
    linked = client.post("/api/v1/agent/evidence", headers=headers, json={
        "host_id": other_host.id, "host_test_id": row["id"], "request_key": "xproj", "tool": "curl",
        "outcome": "finding", "summary": "must not attach across projects",
    })
    assert linked.status_code in (404, 422), linked.text
    db_session.expire_all()
    stored = db_session.get(HostTest, row["id"])
    assert (stored.status, stored.revision) == ("proposed", row["revision"])
    assert db_session.query(EvidenceRecord).filter(EvidenceRecord.host_test_id == row["id"]).count() == 0


def test_mcp_propose_round_trips_through_the_loopback(client, db_session, test_project, host):
    """``host_tests_propose`` over MCP reaches the real route with the caller's
    key: the row is the agent's, in the key's project, and the page lists it."""
    start = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert start.status_code == 201, start.text
    session = start.json()
    spec = item(host, command="curl -sI https://{ip}/", priority="high", label="Headers")
    r = client.post("/api/v1/mcp", headers={"X-API-Key": session["api_key"]}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "host_tests_propose", "arguments": {"tests": [spec]}},
    })
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["isError"] is False, result
    proposed = result["structuredContent"]["items"][0]
    assert (proposed["host_id"], proposed["source"], proposed["priority"], proposed["label"]) == (
        host.id, "agent", "high", "Headers",
    )
    stored = db_session.query(HostTest).one()
    assert (stored.id, stored.project_id, stored.agent_session_id, stored.command) == (
        proposed["id"], test_project.id, session["agent_session_id"], "curl -sI https://{ip}/",
    )
    page = client.get(base(test_project), params={"host_id": host.id}).json()
    assert [t["id"] for t in page["items"]] == [stored.id]

    # An unknown field is refused by the schema the tool advertises (the
    # endpoint's own), not silently dropped.
    bad = client.post("/api/v1/mcp", headers={"X-API-Key": session["api_key"]}, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "host_tests_propose",
                   "arguments": {"tests": [{**item(host), "test_phase": "enumeration"}]}},
    }).json()
    assert "error" in bad or bad["result"]["isError"] is True, bad
    assert db_session.query(HostTest).count() == 1


def test_q_lists_the_tests_on_the_hosts_a_hosts_query_matches(client, db_session, test_project, host):
    """``q`` is the Hosts page's query language selecting HOSTS, not a text
    search over tests: a test is listed when its host matches."""
    other = models.Host(project_id=test_project.id, ip_address="10.45.0.2", state="up")
    db_session.add(other)
    db_session.commit()
    mine = create(client, test_project, item(host))
    theirs = create(client, test_project, item(other))

    def ids(q):
        response = client.get(base(test_project), params={"q": q})
        assert response.status_code == 200, response.text
        return {t["id"] for t in response.json()["items"]}

    assert ids("ip:10.45.0.1") == {mine["id"]}
    assert ids("ip:10.45.0.2") == {theirs["id"]}
    assert ids("has:planned") == {mine["id"], theirs["id"]}
    # The test's own words are not what q searches.
    assert ids('hostname:"Check response headers"') == set()


# --- a person records a result and promotes it (v2.443.0) --------------------

def _result(client, project, test, **over):
    body = {"expected_revision": test["revision"], "request_key": str(uuid.uuid4()),
            "outcome": "finding", "summary": "X-Frame-Options is missing", **over}
    return client.post(f"{base(project)}/{test['id']}/result", json=body), body


def test_a_persons_result_is_evidence_closes_the_test_and_marks_the_host_tested(client, db_session, test_project, host):
    row = create(client, test_project, item(host, command="curl -sI http://{ip}/"))
    response, body = _result(client, test_project, row, raw_output="HTTP/1.1 200 OK")
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["test"]["status"] == "done" and data["test"]["evidence_count"] == 1
    record = db_session.query(EvidenceRecord).one()
    assert (record.host_test_id, record.outcome, record.agent_session_id) == (row["id"], "finding", None)
    assert record.recorded_by_user_id is not None
    assert record.command == "curl -sI http://10.45.0.1/"          # the test's command, placeholders filled
    hosts = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": "has:tested"}).json()
    assert [h["id"] for h in (hosts["items"] if isinstance(hosts, dict) else hosts)] == [host.id]
    # A retry of the same call stores nothing new.
    again = client.post(f"{base(test_project)}/{row['id']}/result", json=body)
    assert again.status_code == 201 and again.json()["evidence"]["id"] == record.id
    assert db_session.query(EvidenceRecord).count() == 1


@pytest.mark.parametrize("outcome", ["inconclusive", "failed"])
def test_an_unsettled_result_leaves_the_test_open(client, test_project, host, outcome):
    row = create(client, test_project, item(host))
    response, _ = _result(client, test_project, row, outcome=outcome)
    assert response.status_code == 201, response.text
    assert response.json()["test"]["status"] == "in_progress"


def test_a_result_on_a_stale_revision_writes_nothing(client, db_session, test_project, host):
    row = create(client, test_project, item(host))
    response, _ = _result(client, test_project, row, expected_revision=row["revision"] + 5)
    assert response.status_code == 409
    db_session.expire_all()
    assert db_session.query(EvidenceRecord).count() == 0
    assert db_session.get(HostTest, row["id"]).status == "proposed"


def test_finding_evidence_is_promoted_once_and_only_when_it_showed_an_issue(client, db_session, test_project, host):
    from app.db.models_findings import Finding, FindingHost

    row = create(client, test_project, item(host))
    response, _ = _result(client, test_project, row)
    evidence_id = response.json()["evidence"]["id"]
    url = f"/api/v1/projects/{test_project.id}/evidence/{evidence_id}/finding"
    made = client.post(url, json={"title": "Clickjacking", "severity": "low"})
    assert made.status_code == 201, made.text
    assert made.json()["joined_issue"] is False
    finding = db_session.get(Finding, made.json()["finding_id"])
    assert (finding.title, finding.severity, finding.status, finding.source) == ("Clickjacking", "low", "confirmed", "execution")
    assert [fh.host_id for fh in db_session.query(FindingHost).filter_by(finding_id=finding.id)] == [host.id]
    db_session.expire_all()
    assert db_session.get(EvidenceRecord, evidence_id).finding_id == finding.id
    # Not twice.
    assert client.post(url, json={"title": "Again", "severity": "low"}).status_code == 409
    assert db_session.query(Finding).count() == 1
    # And not from a result that showed nothing.
    other = create(client, test_project, item(host))
    clean, _ = _result(client, test_project, other, outcome="no_finding", summary="header present")
    refused = client.post(
        f"/api/v1/projects/{test_project.id}/evidence/{clean.json()['evidence']['id']}/finding",
        json={"title": "Nope", "severity": "low"},
    )
    assert refused.status_code == 422


# --- a test confirms a weakness; both roads to a finding meet (v2.445.0) -----

def _observation(db_session, project, host, title="SMB Signing not required"):
    from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource

    scan = models.Scan(project_id=project.id, filename="n.nessus", tool_name="nessus", scan_type="nessus")
    db_session.add(scan)
    db_session.flush()
    vuln = Vulnerability(host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                         severity=VulnerabilitySeverity.MEDIUM, title=title)
    db_session.add(vuln)
    db_session.commit()
    return vuln


def test_a_test_is_linked_to_the_observation_it_confirms_by_issue(client, db_session, test_project, host):
    vuln = _observation(db_session, test_project, host)
    row = create(client, test_project, item(host, vulnerability_id=vuln.id))
    assert row["issue_key"] == vuln.issue_key and row["issue_title"] == "SMB Signing not required"
    # An observation on ANOTHER host is not this host's weakness.
    other = models.Host(project_id=test_project.id, ip_address="10.45.0.9", state="up")
    db_session.add(other)
    db_session.commit()
    elsewhere = _observation(db_session, test_project, other, title="Something else")
    refused = client.post(base(test_project), json={"tests": [item(host, vulnerability_id=elsewhere.id)]})
    assert refused.status_code == 422
    # The link survives the scanner row being replaced by a later scan.
    db_session.delete(db_session.get(type(vuln), vuln.id))
    db_session.commit()
    assert client.get(f"{base(test_project)}/{row['id']}").json()["issue_title"] == "SMB Signing not required"


def test_a_finding_made_from_an_unlinked_result_is_owned_by_who_made_it(
    client, db_session, test_project, test_user, host,
):
    """Walkthrough 2026-10-02: created unowned, a finding a person had just
    confirmed from their own test — with no report text yet — was on nobody's
    Operations list.  A promoted observation's finding already goes to the
    promoter (``promote_vulnerability``)."""
    from app.db.models_findings import Finding

    row = create(client, test_project, item(host))
    result, _ = _result(client, test_project, row, summary="anonymous login accepted")
    evidence_id = result.json()["evidence"]["id"]
    made = client.post(f"/api/v1/projects/{test_project.id}/evidence/{evidence_id}/finding",
                       json={"title": "Anonymous FTP login allowed", "severity": "medium"})
    assert made.status_code == 201, made.text
    finding = db_session.get(Finding, made.json()["finding_id"])
    assert finding.owner_id == test_user.id == finding.created_by_id


def test_promoting_a_linked_result_joins_the_issues_finding_instead_of_making_a_second(
    client, db_session, test_project, host,
):
    from app.db.models_findings import Finding

    vuln = _observation(db_session, test_project, host)
    row = create(client, test_project, item(host, vulnerability_id=vuln.id))
    result, _ = _result(client, test_project, row, summary="signing:False")
    evidence_id = result.json()["evidence"]["id"]
    made = client.post(f"/api/v1/projects/{test_project.id}/evidence/{evidence_id}/finding",
                       json={"title": "ignored for a linked test", "severity": "low"})
    assert made.status_code == 201, made.text
    assert made.json()["joined_issue"] is True
    finding = db_session.get(Finding, made.json()["finding_id"])
    assert finding.source == "scanner" and finding.title == "SMB Signing not required"
    # The observation rates the finding; the severity sent is the test's
    # priority, which nobody chose as a severity.
    assert finding.severity == "medium"
    # Promoting the observation itself afterwards is the SAME finding.
    again = client.post(f"/api/v1/projects/{test_project.id}/vulnerabilities/{vuln.id}/promote",
                        json={"vuln_id": vuln.id, "scope": "host"})
    assert again.status_code == 201, again.text
    assert again.json()["id"] == finding.id
    assert db_session.query(Finding).count() == 1


def test_promoting_the_observation_takes_the_results_that_showed_it(client, db_session, test_project, host):
    vuln = _observation(db_session, test_project, host)
    linked = create(client, test_project, item(host, vulnerability_id=vuln.id))
    unrelated = create(client, test_project, item(host))
    shown, _ = _result(client, test_project, linked, summary="signing:False")
    other, _ = _result(client, test_project, unrelated, summary="something else entirely")
    promoted = client.post(f"/api/v1/projects/{test_project.id}/vulnerabilities/{vuln.id}/promote",
                           json={"vuln_id": vuln.id, "scope": "host"})
    assert promoted.status_code == 201, promoted.text
    db_session.expire_all()
    assert db_session.get(EvidenceRecord, shown.json()["evidence"]["id"]).finding_id == promoted.json()["id"]
    # A result for a test about something else is not swept along.
    assert db_session.get(EvidenceRecord, other.json()["evidence"]["id"]).finding_id is None


def test_evidence_can_be_listed_without_the_records_that_answer_a_test(client, db_session, test_project, host):
    row = create(client, test_project, item(host))
    _result(client, test_project, row)
    db_session.add(EvidenceRecord(project_id=test_project.id, host_id=host.id, tool="curl",
                                  outcome="info", summary="banner"))
    db_session.commit()
    url = f"/api/v1/projects/{test_project.id}/evidence"
    assert client.get(url, params={"host_id": host.id}).json()["total"] == 2
    only = client.get(url, params={"host_id": host.id, "unlinked": True}).json()
    assert [e["summary"] for e in only["items"]] == ["banner"]


# --- review 2026-10-01: replays, revisions, the finished-test rule ----------

def test_a_retried_result_is_the_same_record_and_a_changed_one_is_refused(client, db_session, test_project, host):
    row = create(client, test_project, item(host))
    first, body = _result(client, test_project, row, raw_output="HTTP/1.1 200 OK")
    assert first.status_code == 201, first.text
    again = client.post(f"{base(test_project)}/{row['id']}/result", json=body)
    assert again.status_code == 201, again.text
    assert again.json()["evidence"]["id"] == first.json()["evidence"]["id"]
    for change in ({"outcome": "no_finding"}, {"summary": "Actually the header is present"},
                   {"raw_output": "HTTP/1.1 404"}):
        refused = client.post(f"{base(test_project)}/{row['id']}/result", json={**body, **change})
        assert refused.status_code == 409, (change, refused.text)
    db_session.expire_all()
    assert db_session.query(EvidenceRecord).count() == 1


def test_every_result_takes_its_revision_even_when_the_status_stays(client, db_session, test_project, host):
    row = create(client, test_project, item(host))
    first, _ = _result(client, test_project, row, outcome="inconclusive")
    snapshot = first.json()["test"]
    second, _ = _result(client, test_project, snapshot, outcome="inconclusive")
    assert second.status_code == 201, second.text
    assert second.json()["test"]["revision"] == snapshot["revision"] + 1
    # A copy read before that result is stale, though the status never moved.
    stale, _ = _result(client, test_project, snapshot, outcome="inconclusive")
    assert stale.status_code == 409, stale.text
    db_session.expire_all()
    assert db_session.query(EvidenceRecord).count() == 2


def test_a_result_racing_a_dismissal_is_refused_by_the_update_itself(client, db_session, test_project, host, monkeypatch):
    """The copy read at the start of the request can be current while the row
    no longer is; only the UPDATE's WHERE sees that."""
    from app.services import agent_evidence_service

    row = create(client, test_project, item(host))
    started, _ = _result(client, test_project, row, outcome="inconclusive")
    current = started.json()["test"]
    # ``record_result`` stores through ``record_evidence_once`` (review
    # 2026-10-01 N8: it needs to know whether THIS call stored the record).
    real = agent_evidence_service.record_evidence_once

    def dismissed_meanwhile(db, **kwargs):
        stored = real(db, **kwargs)
        db.execute(HostTest.__table__.update().where(HostTest.id == current["id"]).values(
            status="dismissed", dismissed_reason="out of scope", revision=HostTest.revision + 1))
        return stored

    monkeypatch.setattr(agent_evidence_service, "record_evidence_once", dismissed_meanwhile)
    late, _ = _result(client, test_project, current, outcome="inconclusive")
    assert late.status_code == 409, late.text


def test_a_finished_test_cannot_lose_its_only_explanation(client, test_project, host):
    row = create(client, test_project, item(host))
    url = f"{base(test_project)}/{row['id']}"
    done = client.patch(url, json={"expected_revision": 1, "status": "done", "tester_summary": "Not needed"})
    assert done.status_code == 200, done.text
    revision = done.json()["revision"]
    for body in ({"tester_summary": None}, {"status": "done", "tester_summary": None}):
        assert client.patch(url, json={"expected_revision": revision, **body}).status_code == 422, body
    # Reopening it is a different matter.
    reopened = client.patch(url, json={"expected_revision": revision, "status": "proposed", "tester_summary": None})
    assert reopened.status_code == 200, reopened.text


@pytest.mark.parametrize("count", [1, 10])
def test_serializing_tests_is_a_fixed_number_of_statements(db_session, test_project, test_user, host, count):
    from sqlalchemy import event
    from app.schemas.host_test_schemas import HostTestCreate
    from app.services import host_test_service
    from app.services.proposal_service import Attribution

    rows = host_test_service.create_tests(
        db_session, test_project.id,
        [HostTestCreate(**item(host)) for _ in range(count)], Attribution(user_id=test_user.id))
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        # As the route does it: built before the commit expires the rows.
        assert len(host_test_service.serialize_many(db_session, rows)) == count
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert len(statements) <= 2, statements


def test_promoting_an_observation_no_test_names_links_nothing(client, db_session, test_project, host):
    vuln = _observation(db_session, test_project, host)
    unrelated = create(client, test_project, item(host))
    other, _ = _result(client, test_project, unrelated)
    promoted = client.post(f"/api/v1/projects/{test_project.id}/vulnerabilities/{vuln.id}/promote",
                           json={"vuln_id": vuln.id, "scope": "host"})
    assert promoted.status_code == 201, promoted.text
    db_session.expire_all()
    assert db_session.get(EvidenceRecord, other.json()["evidence"]["id"]).finding_id is None



def test_a_test_is_assigned_to_someone_who_can_work_it(client, db_session, test_project, host):
    """A global admin writes to every project without a membership row, so
    they can take a test; an account with no place in the project cannot."""
    from datetime import datetime, timezone
    from app.db.models_auth import User, UserRole

    def account(name, role, uid):
        # Explicit ids: the fixture user is inserted with one, so the sequence lags.
        user = User(id=uid, username=name, email=f"{name}@example.com", hashed_password="x", role=role,
                    is_active=True, is_verified=True, created_at=datetime.now(timezone.utc))
        db_session.add(user)
        db_session.commit()
        return user

    admin = account("roaming-admin", UserRole.ADMIN, 4101)
    outsider = account("no-membership", UserRole.MEMBER, 4102)
    taken = client.post(base(test_project), json={"tests": [item(host, assigned_to_id=admin.id)]})
    assert taken.status_code == 201, taken.text
    assert taken.json()["items"][0]["assigned_to_id"] == admin.id
    refused = client.post(base(test_project), json={"tests": [item(host, assigned_to_id=outsider.id)]})
    assert refused.status_code == 422, refused.text
