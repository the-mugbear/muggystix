"""The changelog's lines for triage: scanner observations, host tests,
evidence and proposals.

Behaviour, as ``test_project_changelog.py``: each action through its real
route leaves ONE line — who, what, to what, how many — and never the text
someone wrote (a reason, a result's summary, a proposed section).
"""
from __future__ import annotations

import uuid

from app.db import models
from app.db.models_project import ProjectEvent
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity


def _lines(db, project, action=None):
    db.expire_all()
    q = db.query(ProjectEvent).filter(ProjectEvent.project_id == project.id)
    if action:
        q = q.filter(ProjectEvent.action == action)
    return q.order_by(ProjectEvent.id).all()


def _actions(db, project):
    return [line.action for line in _lines(db, project)]


def _said(lines):
    """Everything a set of lines says, to look for leaked text in."""
    return " | ".join(f"{line.subject_label or ''} {line.summary or ''}" for line in lines)


def _host(db, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


def _scan(db, project):
    scan = models.Scan(project_id=project.id, filename="n.nessus", scan_type="nessus", tool_name="nessus")
    db.add(scan)
    db.commit()
    return scan


def _vuln(db, host, scan, title):
    vuln = Vulnerability(
        host_id=host.id, scan_id=scan.id, title=title, severity=VulnerabilitySeverity.MEDIUM, source="nessus",
    )
    db.add(vuln)
    db.commit()
    return vuln


def _agent(client, project):
    started = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert started.status_code == 201, started.text
    return {"X-API-Key": started.json()["api_key"]}


def _base(project):
    return f"/api/v1/projects/{project.id}"


def _test(host, **extra):
    return {"request_key": str(uuid.uuid4()), "host_id": host.id, "tool": "curl",
            "description": "Check the response headers for HSTS", "rationale": "The web service answers", **extra}


# --- scanner observations ----------------------------------------------------

def test_promoting_one_observation_from_its_host_is_a_line_about_it(client, db_session, test_project, test_user):
    host = _host(db_session, test_project, "10.61.0.1")
    vuln = _vuln(db_session, host, _scan(db_session, test_project), "SMB Signing not required")
    answer = client.post(
        f"{_base(test_project)}/vulnerabilities/{vuln.id}/promote",
        json={"vuln_id": vuln.id, "status": "confirmed", "scope": "host", "summary": "relayed it in the lab"},
    )
    assert answer.status_code == 201, answer.text

    (line,) = _lines(db_session, test_project, "observation_promoted")
    assert (line.subject_type, line.subject_id, line.subject_label) == (
        "observation", vuln.id, "SMB Signing not required",
    )
    assert (line.summary, line.count, line.by_agent, line.actor_user_id) == (
        "confirmed on this host", 1, False, test_user.id,
    )
    assert "relayed" not in _said(_lines(db_session, test_project))
    assert _lines(db_session, test_project, "observation_dismissed") == []


def test_dismissing_one_observation_is_a_dismissal_and_keeps_the_reason_out(client, db_session, test_project):
    host = _host(db_session, test_project, "10.61.1.1")
    scan = _scan(db_session, test_project)
    here = _vuln(db_session, host, scan, "TLS Version 1.0 Protocol Detection")
    everywhere = _vuln(db_session, host, scan, "SSH Weak Algorithms Supported")
    url = f"{_base(test_project)}/vulnerabilities"
    assert client.post(f"{url}/{here.id}/promote", json={
        "vuln_id": here.id, "status": "false_positive", "summary": "the listener is a honeypot",
    }).status_code == 201
    assert client.post(f"{url}/{everywhere.id}/promote", json={
        "vuln_id": everywhere.id, "status": "accepted_risk", "summary": "signed off by the client",
    }).status_code == 201

    lines = _lines(db_session, test_project, "observation_dismissed")
    assert [(line.subject_id, line.subject_label, line.summary) for line in lines] == [
        (here.id, "TLS Version 1.0 Protocol Detection", "false positive on this host"),
        (everywhere.id, "SSH Weak Algorithms Supported", "accepted risk"),
    ]
    assert _lines(db_session, test_project, "observation_promoted") == []
    said = _said(_lines(db_session, test_project))
    assert "honeypot" not in said and "signed off" not in said


def test_a_bulk_promotion_is_one_line_with_its_count(client, db_session, test_project):
    scan = _scan(db_session, test_project)
    hosts = [_host(db_session, test_project, f"10.61.2.{n}") for n in range(1, 4)]
    for host in hosts:
        _vuln(db_session, host, scan, "SMB Signing not required")
    for host in hosts[:2]:
        _vuln(db_session, host, scan, "TLS Version 1.0 Protocol Detection")
    url = f"{_base(test_project)}/scanner-observations"

    answer = client.post(f"{url}/promote", json={"items": [
        {"issue_key": "title:smb signing not required"},
        {"issue_key": "title:tls version 1.0 protocol detection", "host_ids": [hosts[0].id]},
    ]})
    assert answer.status_code == 200, answer.text
    (line,) = _lines(db_session, test_project, "observation_promoted")
    assert (line.count, line.subject_id, line.subject_label, line.summary) == (
        2, None, None, "2 scanner observations → findings",
    )

    # One issue from the list names it, and how many hosts.
    again = client.post(f"{url}/promote", json={"items": [
        {"issue_key": "title:tls version 1.0 protocol detection", "host_ids": [hosts[1].id]},
    ]})
    assert again.status_code == 200, again.text
    one = _lines(db_session, test_project, "observation_promoted")[-1]
    assert (one.count, one.subject_label, one.summary) == (
        1, "TLS Version 1.0 Protocol Detection", "to a finding on 1 host",
    )
    assert one.subject_id is not None


def test_the_lists_dismissal_is_one_line_however_many_hosts_it_names(client, db_session, test_project):
    scan = _scan(db_session, test_project)
    hosts = [_host(db_session, test_project, f"10.61.3.{n}") for n in range(1, 4)]
    for host in hosts:
        _vuln(db_session, host, scan, "SMB Signing not required")
    url = f"{_base(test_project)}/scanner-observations/dismiss"

    some = client.post(url, json={
        "issue_key": "title:smb signing not required", "host_ids": [hosts[0].id, hosts[1].id],
        "reason": "signing is enforced by GPO",
    })
    assert some.status_code == 200, some.text
    (line,) = _lines(db_session, test_project, "observation_dismissed")
    assert (line.count, line.subject_type, line.subject_label, line.summary) == (
        1, "observation", "SMB Signing not required", "false positive on 2 hosts",
    )

    everywhere = client.post(url, json={"issue_key": "title:smb signing not required"})
    assert everywhere.status_code == 200, everywhere.text
    lines = _lines(db_session, test_project, "observation_dismissed")
    assert [line.summary for line in lines] == ["false positive on 2 hosts", "false positive on 3 hosts"]
    assert "GPO" not in _said(_lines(db_session, test_project))
    assert _lines(db_session, test_project, "observation_promoted") == []

    # A refused request changes nothing and says nothing.
    refused = client.post(url, json={"issue_key": "title:no such issue"})
    assert refused.status_code == 422
    assert len(_lines(db_session, test_project, "observation_dismissed")) == 2


# --- host tests --------------------------------------------------------------

def test_a_tests_life_is_a_line_per_action_and_a_result_is_not_also_evidence(client, db_session, test_project):
    host = _host(db_session, test_project, "10.61.4.1")
    url = f"{_base(test_project)}/host-tests"
    made = client.post(url, json={"tests": [_test(host)]})
    assert made.status_code == 201, made.text
    test = made.json()["items"][0]

    claimed = client.patch(f"{url}/{test['id']}", json={
        "expected_revision": test["revision"], "status": "in_progress"})
    assert claimed.status_code == 200, claimed.text
    noted = client.patch(f"{url}/{test['id']}", json={
        "expected_revision": claimed.json()["revision"], "tester_summary": "the proxy strips the header"})
    assert noted.status_code == 200, noted.text
    result = client.post(f"{url}/{test['id']}/result", json={
        "expected_revision": noted.json()["revision"], "request_key": "result-1", "outcome": "finding",
        "summary": "HSTS is missing on the login page", "raw_output": "HTTP/1.1 200 OK",
    })
    assert result.status_code == 201, result.text

    lines = _lines(db_session, test_project)
    assert [(line.action, line.summary) for line in lines] == [
        ("host_test_added", None),
        ("host_test_updated", "proposed → in_progress"),
        ("host_test_updated", "tester summary"),
        ("host_test_result_recorded", "finding"),
    ]
    assert {(line.subject_type, line.subject_id, line.subject_label, line.count) for line in lines} == {
        ("host_test", test["id"], "curl test on 10.61.4.1", 1),
    }
    said = _said(lines)
    for written in ("HSTS", "proxy", "web service", "HTTP/1.1"):
        assert written not in said

    # The same result sent again stores nothing and says nothing; neither
    # does an update that changes nothing.
    replay = client.post(f"{url}/{test['id']}/result", json={
        "expected_revision": noted.json()["revision"], "request_key": "result-1", "outcome": "finding",
        "summary": "HSTS is missing on the login page", "raw_output": "HTTP/1.1 200 OK",
    })
    assert replay.status_code == 201, replay.text
    now = client.get(f"{url}/{test['id']}").json()
    same = client.patch(f"{url}/{test['id']}", json={"expected_revision": now["revision"], "status": "done"})
    assert same.status_code == 200, same.text
    assert len(_lines(db_session, test_project)) == 4


def test_several_tests_in_one_request_are_one_line_and_a_replay_is_none(client, db_session, test_project):
    a, b = _host(db_session, test_project, "10.61.5.1"), _host(db_session, test_project, "10.61.5.2")
    url = f"{_base(test_project)}/host-tests"
    batch = [_test(a), _test(a), _test(b)]
    assert client.post(url, json={"tests": batch}).status_code == 201

    (line,) = _lines(db_session, test_project, "host_test_added")
    assert (line.count, line.subject_id, line.subject_label, line.summary) == (
        3, None, None, "3 tests on 2 hosts",
    )
    assert client.post(url, json={"tests": batch}).status_code == 201          # the same request again
    assert len(_lines(db_session, test_project, "host_test_added")) == 1


def test_an_agents_test_and_evidence_are_the_agents_lines(client, db_session, test_project, test_user):
    host = _host(db_session, test_project, "10.61.6.1")
    key = _agent(client, test_project)
    made = client.post("/api/v1/agent/host-tests", headers=key, json={"tests": [_test(host, tool="nmap")]})
    assert made.status_code == 201, made.text
    body = {
        "host_id": host.id, "tool": "nmap", "outcome": "no_finding", "request_key": "ev-1",
        "summary": "port 8443 is filtered from here", "command": "nmap -p8443 10.61.6.1",
        "raw_output": "8443/tcp filtered",
    }
    recorded = client.post("/api/v1/agent/evidence", headers=key, json=body)
    assert recorded.status_code == 201, recorded.text
    assert client.post("/api/v1/agent/evidence", headers=key, json=body).status_code == 201      # a retry

    (added,) = _lines(db_session, test_project, "host_test_added")
    (evidence,) = _lines(db_session, test_project, "evidence_recorded")
    assert (evidence.subject_type, evidence.subject_id, evidence.subject_label, evidence.summary) == (
        "evidence", recorded.json()["id"], "nmap", "no_finding",
    )
    for line in (added, evidence):
        assert line.by_agent is True and line.agent_session_id is not None
        assert line.actor_user_id == test_user.id
    said = _said(_lines(db_session, test_project))
    assert "filtered" not in said and "-p8443" not in said


def test_a_finding_made_from_a_result_is_a_line_about_the_finding(client, db_session, test_project):
    host = _host(db_session, test_project, "10.61.7.1")
    url = f"{_base(test_project)}/host-tests"
    test = client.post(url, json={"tests": [_test(host)]}).json()["items"][0]
    result = client.post(f"{url}/{test['id']}/result", json={
        "expected_revision": test["revision"], "request_key": "result-7", "outcome": "finding",
        "summary": "the header is missing",
    })
    assert result.status_code == 201, result.text
    made = client.post(
        f"{_base(test_project)}/evidence/{result.json()['evidence']['id']}/finding",
        json={"title": "HSTS not enforced", "severity": "low"},
    )
    assert made.status_code == 201, made.text

    (line,) = _lines(db_session, test_project, "evidence_promoted")
    assert (line.subject_type, line.subject_id, line.subject_label, line.summary) == (
        "finding", made.json()["finding_id"], "HSTS not enforced", "new finding",
    )
    assert _lines(db_session, test_project, "evidence_recorded") == []


# --- proposals ---------------------------------------------------------------

def _finding(client, project, host, title="SMB signing not required"):
    answer = client.post(f"{_base(project)}/findings", json={"title": title, "severity": "medium", "host_ids": [host.id]})
    assert answer.status_code == 201, answer.text
    return answer.json()


def _propose_text(client, key, finding, fields):
    answer = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": fields, "rationale": "the sections were empty"})
    assert answer.status_code == 201, answer.text
    return [row["id"] for row in answer.json()["proposals"]]


def test_an_agents_proposal_is_one_line_however_many_sections_and_never_their_text(
    client, db_session, test_project, test_user,
):
    host = _host(db_session, test_project, "10.61.8.1")
    finding = _finding(client, test_project, host)
    key = _agent(client, test_project)
    ids = _propose_text(client, key, finding, {
        "description": "Signing is negotiated, never required.", "impact": "An attacker can relay."})
    (single,) = _propose_text(client, key, finding, {"recommendation": "Require signing by policy."})

    several, one = _lines(db_session, test_project, "proposal_made")
    assert (several.count, several.subject_type, several.subject_id) == (len(ids), "proposal", None)
    assert (several.subject_label, several.summary) == (
        "report text for SMB signing not required", "Description, Impact",
    )
    assert (one.count, one.subject_id, one.subject_label, one.summary) == (
        1, single, "report text for SMB signing not required", "Recommendation",
    )
    for line in (several, one):
        assert line.by_agent is True and line.agent_session_id is not None
        assert line.actor_user_id == test_user.id
    said = _said(_lines(db_session, test_project))
    for written in ("negotiated", "relay", "policy", "empty"):
        assert written not in said


def test_each_kind_of_proposal_is_named_by_what_it_is_about(client, db_session, test_project):
    host = _host(db_session, test_project, "10.61.9.1")
    vuln = _vuln(db_session, host, _scan(db_session, test_project), "SMB Signing not required")
    key = _agent(client, test_project)
    new = client.post("/api/v1/agent/proposals/finding", headers=key, json={
        "title": "Default credentials on the console", "severity": "high", "host_ids": [host.id]})
    assert new.status_code == 201, new.text
    dismissal = client.post("/api/v1/agent/proposals/observation", headers=key, json={
        "vulnerability_id": vuln.id, "action": "dismiss", "scope": "host", "summary": "the fix is backported"})
    assert dismissal.status_code == 201, dismissal.text

    lines = _lines(db_session, test_project, "proposal_made")
    assert [(line.subject_id, line.subject_label, line.summary) for line in lines] == [
        (new.json()["id"], "a new finding", None),
        (dismissal.json()["id"], "dismissing scanner observation SMB Signing not required", None),
    ]
    said = _said(lines)
    assert "Default credentials" not in said and "backported" not in said


def test_accepting_and_rejecting_are_the_deciders_lines(client, db_session, test_project, test_user):
    host = _host(db_session, test_project, "10.61.10.1")
    finding = _finding(client, test_project, host)
    key = _agent(client, test_project)
    accepted, rejected = _propose_text(client, key, finding, {
        "description": "Signing is negotiated, never required.", "impact": "An attacker can relay."})
    url = f"{_base(test_project)}/proposals"
    assert client.post(f"{url}/{accepted}/accept", json={"note": "reads well"}).status_code == 200
    assert client.post(f"{url}/{rejected}/reject", json={"note": "too vague"}).status_code == 200
    # Decided already: refused, and no second line.
    assert client.post(f"{url}/{accepted}/accept", json={}).status_code == 409

    (yes,) = _lines(db_session, test_project, "proposal_accepted")
    (no,) = _lines(db_session, test_project, "proposal_rejected")
    assert (yes.subject_type, yes.subject_id, yes.subject_label, yes.summary) == (
        "proposal", accepted, "report text for SMB signing not required", "Description",
    )
    assert (no.subject_id, no.summary) == (rejected, "Impact")
    # The accepter's own action, by hand — and the edit it made has its line.
    for line in (yes, no):
        assert line.by_agent is False and line.actor_user_id == test_user.id
    (edit,) = _lines(db_session, test_project, "finding_edited")
    assert (edit.summary, edit.by_agent) == ("Description", False)
    said = _said(_lines(db_session, test_project))
    for written in ("negotiated", "relay", "reads well", "vague"):
        assert written not in said


def test_a_bulk_decision_is_one_line_for_what_it_decided(client, db_session, test_project):
    host = _host(db_session, test_project, "10.61.11.1")
    key = _agent(client, test_project)
    findings = [_finding(client, test_project, host, title=f"Finding {n}") for n in range(3)]
    ids = [_propose_text(client, key, finding, {"impact": f"Impact {n}."})[0] for n, finding in enumerate(findings)]
    url = f"{_base(test_project)}/proposals"
    assert client.post(f"{url}/{ids[0]}/reject", json={}).status_code == 200

    answer = client.post(f"{url}/bulk", json={"ids": ids, "action": "accept"}).json()
    assert answer["decided"] == ids[1:] and [f["id"] for f in answer["failed"]] == [ids[0]]
    (line,) = _lines(db_session, test_project, "proposal_accepted")
    assert (line.count, line.subject_id, line.subject_label, line.summary) == (2, None, None, "2 proposals")
    assert len(_lines(db_session, test_project, "proposal_rejected")) == 1          # the single one, above

    # Nothing decided, nothing said.
    nothing = client.post(f"{url}/bulk", json={"ids": ids, "action": "reject"}).json()
    assert nothing["decided"] == []
    assert len(_lines(db_session, test_project, "proposal_rejected")) == 1


def test_a_proposal_replaced_by_its_proposer_is_no_decision(client, db_session, test_project):
    host = _host(db_session, test_project, "10.61.12.1")
    finding = _finding(client, test_project, host)
    key = _agent(client, test_project)
    (first,) = _propose_text(client, key, finding, {"impact": "A first draft."})
    second = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"impact": "A better draft."}, "replaces": [first]})
    assert second.status_code == 201, second.text

    assert len(_lines(db_session, test_project, "proposal_made")) == 2
    assert _lines(db_session, test_project, "proposal_accepted") == []
    assert _lines(db_session, test_project, "proposal_rejected") == []


def test_an_accepted_promotion_is_the_observations_line_too(client, db_session, test_project):
    host = _host(db_session, test_project, "10.61.13.1")
    vuln = _vuln(db_session, host, _scan(db_session, test_project), "SMB Signing not required")
    key = _agent(client, test_project)
    proposal = client.post("/api/v1/agent/proposals/observation", headers=key, json={
        "vulnerability_id": vuln.id, "action": "promote", "scope": "host"})
    assert proposal.status_code == 201, proposal.text
    accepted = client.post(f"{_base(test_project)}/proposals/{proposal.json()['id']}/accept", json={})
    assert accepted.status_code == 200, accepted.text

    # (The session's own start is another line, not this test's.)
    assert [action for action in _actions(db_session, test_project) if action != "agent_session_started"] == [
        "proposal_made", "observation_promoted", "proposal_accepted",
    ]
    (promoted,) = _lines(db_session, test_project, "observation_promoted")
    assert (promoted.subject_id, promoted.by_agent) == (vuln.id, False)        # the accepter did it
