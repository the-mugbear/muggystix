"""Evidence records and agent proposals (v2.436.0).

The rule (the user, 2026-09-30): an agent's change is a PROPOSAL when it
alters what the team has concluded or what the client report says — a
finding's report text, a new finding, promoting or dismissing a scanner
observation, an endpoint's status.  Everything else is direct and
attributed.  What the agent DID (ran X against Y, got Z) is an evidence
record: direct, immutable, no plan needed.

Accepting runs the SAME code a person's click runs, as the person accepting,
so the authored-content rule (only the author or a project admin writes a
finding's report text) still decides who may accept report text.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import settings
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Project, ProjectMembership, ProjectRole
from app.db.models_proposals import AgentProposal
from app.main import app


@pytest.fixture(autouse=True)
def _evidence_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))


def _start(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"X-API-Key": body["api_key"]}, body["agent_session_id"]


def _host(db, project, ip="10.40.0.1"):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


def _member(db, project, user_id, username, role=ProjectRole.ANALYST):
    user = User(
        id=user_id, username=username, email=f"{user_id}@example.com", full_name=username,
        hashed_password="x", role=UserRole.MEMBER, is_active=True, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db.commit()
    return user


def _finding(client, project, host, title="SMB signing not required"):
    r = client.post(f"/api/v1/projects/{project.id}/findings",
                    json={"title": title, "severity": "medium", "host_ids": [host.id]})
    assert r.status_code == 201, r.text
    return r.json()


def _vuln(db, project, host):
    from app.db.models import Scan
    from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
    scan = Scan(project_id=project.id, filename="n.nessus", tool_name="nessus")
    db.add(scan)
    db.flush()
    v = Vulnerability(host_id=host.id, scan_id=scan.id, plugin_id="57608", title="SMB Signing not required",
                      severity=VulnerabilitySeverity.MEDIUM, source=VulnerabilitySource.NESSUS)
    db.add(v)
    db.commit()
    return v


def _base(project):
    return f"/api/v1/projects/{project.id}"


# ---------------------------------------------------------------------------
# Evidence records
# ---------------------------------------------------------------------------

def test_evidence_is_recorded_directly_with_its_raw_output_as_a_file(client, db_session, test_project):
    key, sid = _start(client, test_project)
    host = _host(db_session, test_project)
    raw = "Host script results:\n| smb2-security-mode:\n|_    Message signing enabled but not required\n" * 100
    r = client.post("/api/v1/agent/evidence", headers=key, json={
        "host_id": host.id, "tool": "nmap", "command": "nmap -p445 --script smb2-security-mode 10.40.0.1",
        "outcome": "finding", "summary": "Signing not required on 445.", "raw_output": raw,
        "agent_model": "claude-opus-5-5",
    })
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["agent_session_id"] == sid and rec["agent_model"] == "claude-opus-5-5"
    assert rec["raw_output_bytes"] == len(raw.encode())
    assert len(rec["raw_output_preview"]) == 2000 and rec["raw_output_truncated_in_preview"] is True

    # The whole output comes back from the file — on both surfaces.
    assert client.get(f"/api/v1/agent/evidence/{rec['id']}/raw", headers=key).text == raw
    assert client.get(f"{_base(test_project)}/evidence/{rec['id']}/raw").text == raw
    listed = client.get(f"{_base(test_project)}/evidence", params={"host_id": host.id}).json()
    assert [e["id"] for e in listed["items"]] == [rec["id"]]


def test_evidence_output_over_the_cap_is_refused(client, db_session, test_project):
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    r = client.post("/api/v1/agent/evidence", headers=key, json={
        "host_id": host.id, "tool": "curl", "outcome": "info", "summary": "big",
        "raw_output": "x" * (5 * 1024 * 1024 + 1),
    })
    assert r.status_code == 413, r.text


def test_evidence_outcome_is_an_enum_and_the_host_must_be_in_the_project(client, db_session, test_project):
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    bad = client.post("/api/v1/agent/evidence", headers=key, json={
        "host_id": host.id, "tool": "nmap", "outcome": "pwned", "summary": "s"})
    assert bad.status_code == 422
    other = Project(name="other", slug="other-ev")
    db_session.add(other)
    db_session.commit()
    elsewhere = _host(db_session, other, "10.99.0.1")
    r = client.post("/api/v1/agent/evidence", headers=key, json={
        "host_id": elsewhere.id, "tool": "nmap", "outcome": "info", "summary": "s"})
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Report text
# ---------------------------------------------------------------------------

def test_report_text_is_proposed_not_written_and_accepting_applies_it(client, db_session, test_project):
    key, sid = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)

    r = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"description": "Signing is not required.",
                                                "impact": "Relay attacks."},
        "rationale": "Filled the empty sections.", "agent_model": "model-a",
    })
    assert r.status_code == 201, r.text
    rows = r.json()["proposals"]
    assert {p["field"] for p in rows} == {"description", "impact"}
    assert all(p["status"] == "pending" and p["agent_session_id"] == sid and p["agent_model"] == "model-a"
               for p in rows)
    db_session.expire_all()
    assert db_session.get(Finding, finding["id"]).description in (None, "")  # nothing written yet

    summary = client.get(f"{_base(test_project)}/proposals/summary").json()
    assert summary == {"pending": 2, "by_kind": {"finding_text": 2}}

    desc = next(p for p in rows if p["field"] == "description")
    acc = client.post(f"{_base(test_project)}/proposals/{desc['id']}/accept", json={})
    assert acc.status_code == 200, acc.text
    assert acc.json()["status"] == "accepted"
    db_session.expire_all()
    assert db_session.get(Finding, finding["id"]).description == "Signing is not required."


def test_several_proposals_per_field_stand_side_by_side_until_one_is_accepted(client, db_session, test_project):
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)

    ids = []
    for model in ("model-a", "model-b", "model-c"):
        r = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
            "finding_id": finding["id"], "fields": {"impact": f"Impact per {model}"}, "agent_model": model})
        ids.append(r.json()["proposals"][0]["id"])
    listed = client.get(f"{_base(test_project)}/proposals", params={"finding_id": finding["id"]}).json()
    assert listed["total"] == 3
    assert {p["agent_model"] for p in listed["items"]} == {"model-a", "model-b", "model-c"}

    # Rejecting one leaves the others pending.
    assert client.post(f"{_base(test_project)}/proposals/{ids[0]}/reject", json={"note": "vague"}).status_code == 200
    # Accepting one — with an edit — supersedes the rest of that field.
    acc = client.post(f"{_base(test_project)}/proposals/{ids[1]}/accept", json={"edited_value": "Edited impact."})
    assert acc.status_code == 200, acc.text
    assert acc.json()["payload"]["accepted_value"] == "Edited impact."
    db_session.expire_all()
    assert db_session.get(Finding, finding["id"]).impact == "Edited impact."
    states = {p.id: p.status for p in db_session.query(AgentProposal).filter(AgentProposal.id.in_(ids))}
    assert states == {ids[0]: "rejected", ids[1]: "accepted", ids[2]: "superseded"}
    # A decided proposal is not decided twice.
    assert client.post(f"{_base(test_project)}/proposals/{ids[2]}/accept", json={}).status_code == 409


def test_only_the_author_or_a_project_admin_accepts_report_text(client, db_session, test_project):
    """The authored-content rule decides, through the same code as an edit.
    The refusal leaves the proposal pending, with no error recorded (it is
    about who asked, not about the proposal)."""
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)  # authored by the admin
    pid = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"description": "x"}}).json()["proposals"][0]["id"]

    analyst = _member(db_session, test_project, 411, "prop-analyst")
    viewer = _member(db_session, test_project, 412, "prop-viewer", ProjectRole.VIEWER)
    try:
        app.dependency_overrides[get_current_user] = lambda: analyst
        assert client.post(f"{_base(test_project)}/proposals/{pid}/accept", json={}).status_code == 403
        app.dependency_overrides[get_current_user] = lambda: viewer
        assert client.post(f"{_base(test_project)}/proposals/{pid}/reject", json={}).status_code == 403
        assert client.get(f"{_base(test_project)}/proposals").status_code == 200  # any member reads
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    db_session.expire_all()
    row = db_session.get(AgentProposal, pid)
    assert (row.status, row.error) == ("pending", None)


def test_unknown_fields_and_bad_cvss_are_refused_when_proposed(client, db_session, test_project):
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    url = "/api/v1/agent/proposals/finding-text"
    assert client.post(url, headers=key, json={"finding_id": finding["id"], "fields": {"title": "x"}}).status_code == 422
    assert client.post(url, headers=key, json={"finding_id": finding["id"], "fields": {"cvss_vector": "nope"}}).status_code == 422
    assert client.post(url, headers=key, json={"finding_id": finding["id"], "fields": {"impact": "  "}}).status_code == 422
    assert client.post(url, headers=key, json={"finding_id": 999999, "fields": {"impact": "x"}}).status_code == 404


# ---------------------------------------------------------------------------
# New findings, observations, endpoints
# ---------------------------------------------------------------------------

def test_a_proposed_finding_is_created_by_the_person_who_accepts_it(client, db_session, test_project, test_user):
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    ev = client.post("/api/v1/agent/evidence", headers=key, json={
        "host_id": host.id, "tool": "nxc", "outcome": "finding", "summary": "signing off"}).json()
    r = client.post("/api/v1/agent/proposals/finding", headers=key, json={
        "title": "SMB signing not required", "severity": "medium", "host_ids": [host.id],
        "report_text": {"description": "Signing off."}, "evidence_ids": [ev["id"]]})
    assert r.status_code == 201, r.text
    assert r.json()["evidence_ids"] == [ev["id"]]
    assert db_session.query(Finding).filter(Finding.project_id == test_project.id).count() == 0

    acc = client.post(f"{_base(test_project)}/proposals/{r.json()['id']}/accept", json={}).json()
    finding = db_session.get(Finding, acc["result_finding_id"])
    assert finding.created_by_id == test_user.id and finding.description == "Signing off."


def test_a_proposal_cannot_cite_evidence_from_another_project(client, db_session, test_project):
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    r = client.post("/api/v1/agent/proposals/finding", headers=key, json={
        "title": "t", "severity": "low", "host_ids": [host.id], "evidence_ids": [987654]})
    assert r.status_code in (404, 422), r.text


def test_dismissing_an_observation_is_a_proposal_about_this_host(client, db_session, test_project):
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    vuln = _vuln(db_session, test_project, host)
    r = client.post("/api/v1/agent/proposals/observation", headers=key, json={
        "vulnerability_id": vuln.id, "action": "dismiss", "scope": "host",
        "summary": "Backported fix", "agent_model": "model-a"})
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "observation_dismiss"

    acc = client.post(f"{_base(test_project)}/proposals/{r.json()['id']}/accept", json={}).json()
    finding = db_session.get(Finding, acc["result_finding_id"])
    fh = db_session.query(FindingHost).filter(FindingHost.finding_id == finding.id).one()
    assert fh.host_id == host.id and fh.host_status == "false_positive"


def test_endpoint_status_is_proposed_and_applied_on_accept(client, db_session, test_project):
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    fh = db_session.query(FindingHost).filter(FindingHost.finding_id == finding["id"]).one()
    r = client.post("/api/v1/agent/proposals/endpoint-status", headers=key, json={
        "finding_id": finding["id"], "finding_host_id": fh.id, "host_status": "remediated"})
    assert r.status_code == 201, r.text
    db_session.expire_all()
    assert db_session.get(FindingHost, fh.id).host_status == "open"
    assert client.post(f"{_base(test_project)}/proposals/{r.json()['id']}/accept", json={}).status_code == 200
    db_session.expire_all()
    assert db_session.get(FindingHost, fh.id).host_status == "remediated"


def test_a_target_that_is_gone_keeps_the_proposal_pending_with_the_reason(client, db_session, test_project):
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    fh = db_session.query(FindingHost).filter(FindingHost.finding_id == finding["id"]).one()
    pid = client.post("/api/v1/agent/proposals/endpoint-status", headers=key, json={
        "finding_id": finding["id"], "finding_host_id": fh.id, "host_status": "retest"}).json()["id"]
    # Someone sets an invalid state underneath it: simulate with a bad payload.
    row = db_session.get(AgentProposal, pid)
    row.payload = {"host_status": "not-a-status"}
    db_session.commit()
    r = client.post(f"{_base(test_project)}/proposals/{pid}/accept", json={})
    assert r.status_code in (400, 409, 422), r.text
    db_session.expire_all()
    row = db_session.get(AgentProposal, pid)
    assert row.status == "pending" and row.error


def test_bulk_decides_each_on_its_own(client, db_session, test_project):
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    ids = [p["id"] for p in client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"impact": "i", "recommendation": "r"}}).json()["proposals"]]
    client.post(f"{_base(test_project)}/proposals/{ids[0]}/reject", json={})
    r = client.post(f"{_base(test_project)}/proposals/bulk", json={"ids": ids, "action": "accept"}).json()
    assert r["decided"] == [ids[1]]
    assert [f["id"] for f in r["failed"]] == [ids[0]] and r["failed"][0]["status_code"] == 409


def test_the_agent_reads_what_happened_to_its_proposals(client, db_session, test_project):
    key, _ = _start(client, test_project)
    other_key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    mine = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"impact": "i"}}).json()["proposals"][0]["id"]
    client.post("/api/v1/agent/proposals/finding-text", headers=other_key, json={
        "finding_id": finding["id"], "fields": {"impact": "j"}})
    client.post(f"{_base(test_project)}/proposals/{mine}/reject", json={"note": "too thin"})

    r = client.get("/api/v1/agent/proposals", headers=key, params={"mine": True}).json()
    assert [(p["id"], p["status"], p["decision_note"]) for p in r["items"]] == [(mine, "rejected", "too thin")]
    assert client.get("/api/v1/agent/proposals", headers=key).json()["total"] == 2


def test_proposals_do_not_cross_projects(client, db_session, test_project):
    key, _ = _start(client, test_project)
    other = Project(name="other", slug="other-prop")
    db_session.add(other)
    db_session.commit()
    host = _host(db_session, other, "10.98.0.1")
    finding = Finding(project_id=other.id, title="elsewhere", severity="low", status="open", source="manual")
    db_session.add(finding)
    db_session.commit()
    r = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding.id, "fields": {"impact": "x"}})
    assert r.status_code == 404
    r = client.post("/api/v1/agent/proposals/finding", headers=key, json={
        "title": "t", "severity": "low", "host_ids": [host.id]})
    assert r.status_code == 404
