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

def test_evidence_is_recorded_directly_with_its_raw_output_in_the_row(client, db_session, test_project, tmp_path):
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

    # The whole output comes back — on both surfaces — and nothing was
    # written to disk (v2.439.0: a file outlived a deleted host or project).
    assert client.get(f"/api/v1/agent/evidence/{rec['id']}/raw", headers=key).text == raw
    assert client.get(f"{_base(test_project)}/evidence/{rec['id']}/raw").text == raw
    listed = client.get(f"{_base(test_project)}/evidence", params={"host_id": host.id}).json()
    assert [e["id"] for e in listed["items"]] == [rec["id"]]
    assert not (tmp_path / "evidence").exists()


def test_evidence_output_goes_with_its_host(client, db_session, test_project):
    """The raw output is part of the row, so deleting the host (or project)
    removes it — no file is left behind."""
    from app.db.models_proposals import EvidenceRecord
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    rid = client.post("/api/v1/agent/evidence", headers=key, json={
        "host_id": host.id, "tool": "curl", "outcome": "info", "summary": "banner",
        "raw_output": "HTTP/1.1 200 OK"}).json()["id"]
    db_session.delete(host)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(EvidenceRecord, rid) is None


def test_evidence_text_with_nul_is_stored_and_short_non_ascii_is_not_cut(client, db_session, test_project):
    """PostgreSQL text rejects NUL, which tool output carries; the preview
    flag compares bytes with bytes (a 100-character accented output is 200
    bytes, and was flagged as cut)."""
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    r = client.post("/api/v1/agent/evidence", headers=key, json={
        "host_id": host.id, "tool": "smbclient", "outcome": "info", "summary": "share\x00list",
        "command": "smbclient -L\x00", "raw_output": "é" * 100 + "\x00"})
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["summary"] == "sharelist" and rec["command"] == "smbclient -L"
    assert rec["raw_output_bytes"] == 200 and rec["raw_output_truncated_in_preview"] is False
    assert client.get(f"/api/v1/agent/evidence/{rec['id']}/raw", headers=key).text == "é" * 100


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


def test_a_proposal_decided_underneath_is_not_applied_again(client, db_session, test_project):
    """Two accepts of one proposed finding each passed the pending check and
    created it twice.  Deciding locks the row and re-reads it: a decision made
    meanwhile (here written behind the session's back, leaving its loaded copy
    stale — the state a second request holds) is seen, and nothing is applied."""
    from sqlalchemy import event, update
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    pid = client.post("/api/v1/agent/proposals/finding", headers=key, json={
        "title": "Twice?", "severity": "low", "host_ids": [host.id]}).json()["id"]
    assert db_session.get(AgentProposal, pid).status == "pending"  # loaded, now stale below
    db_session.execute(
        update(AgentProposal).where(AgentProposal.id == pid).values(status="accepted")
        .execution_options(synchronize_session=False)
    )

    statements = []
    listener = lambda conn, cur, stmt, *a: statements.append(stmt)  # noqa: E731
    event.listen(db_session.bind, "before_cursor_execute", listener)
    try:
        r = client.post(f"{_base(test_project)}/proposals/{pid}/accept", json={})
    finally:
        event.remove(db_session.bind, "before_cursor_execute", listener)
    assert r.status_code == 409, r.text
    assert db_session.query(Finding).filter(Finding.title == "Twice?").count() == 0
    if db_session.bind.dialect.name == "postgresql":
        assert any("FROM agent_proposals" in s and "FOR UPDATE" in s for s in statements)


def test_accepting_a_cvss_4_vector_does_not_keep_the_old_vectors_score(client, db_session, test_project):
    """A 4.0 vector keeps the score it is given, and the finding's score was
    computed from its previous 3.1 vector; a reviewer of a vector proposal
    never sees a score, so it is left empty rather than wrong."""
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    v31 = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
    assert client.patch(f"{_base(test_project)}/findings/{finding['id']}",
                        json={"cvss_vector": v31}).status_code == 200
    db_session.expire_all()
    assert db_session.get(Finding, finding["id"]).cvss_score == 9.8

    v40 = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:L/VI:N/VA:N/SC:N/SI:N/SA:N"
    pid = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"cvss_vector": v40}}).json()["proposals"][0]["id"]
    assert client.post(f"{_base(test_project)}/proposals/{pid}/accept", json={}).status_code == 200
    db_session.expire_all()
    f = db_session.get(Finding, finding["id"])
    assert (f.cvss_vector, f.cvss_score) == (v40, None)

    # A 3.x vector still decides its own score.
    pid = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"cvss_vector": v31}}).json()["proposals"][0]["id"]
    assert client.post(f"{_base(test_project)}/proposals/{pid}/accept", json={}).status_code == 200
    db_session.expire_all()
    assert db_session.get(Finding, finding["id"]).cvss_score == 9.8


def test_a_dismissal_on_a_findings_observation_makes_the_finding_need_review(client, db_session, test_project):
    """Dismissing an observation that evidences a finding drops an endpoint
    from the report, so it counts on the finding: its Proposals list and the
    report's pending-proposal warning (one definition, derived from
    finding_vulnerabilities)."""
    from app.db.models_findings import FindingVulnerability
    from app.services import proposal_service
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    vuln = _vuln(db_session, test_project, host)
    finding = _finding(client, test_project, host)
    db_session.add(FindingVulnerability(finding_id=finding["id"], vuln_id=vuln.id))
    db_session.commit()

    pid = client.post("/api/v1/agent/proposals/observation", headers=key, json={
        "vulnerability_id": vuln.id, "action": "dismiss", "scope": "host"}).json()["id"]
    text = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"impact": "i"}}).json()["proposals"][0]["id"]

    listed = client.get(f"{_base(test_project)}/proposals", params={"finding_id": finding["id"]}).json()
    assert {p["id"] for p in listed["items"]} == {pid, text}
    assert proposal_service.pending_per_finding(db_session, [finding["id"]]) == {finding["id"]: 2}

    # An observation not linked to the finding does not count on it.
    other_host = _host(db_session, test_project, "10.40.0.2")
    stray = _vuln(db_session, test_project, other_host)
    client.post("/api/v1/agent/proposals/observation", headers=key, json={
        "vulnerability_id": stray.id, "action": "promote"})
    assert proposal_service.pending_per_finding(db_session, [finding["id"]]) == {finding["id"]: 2}


def test_bulk_accept_does_not_choose_between_drafts_of_one_field(client, db_session, test_project):
    """Two models' drafts of one section are compared by a person: a bulk
    accept used to apply whichever was listed first and supersede the other."""
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    drafts = [client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"impact": f"Impact {m}"}, "agent_model": m,
    }).json()["proposals"][0]["id"] for m in ("model-a", "model-b")]
    single = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"recommendation": "Require signing."}}).json()["proposals"][0]["id"]

    r = client.post(f"{_base(test_project)}/proposals/bulk",
                    json={"ids": [*drafts, single], "action": "accept"}).json()
    assert r["decided"] == [single]
    assert {f["id"] for f in r["failed"]} == set(drafts)
    assert all(f["status_code"] == 409 and "choose one" in f["detail"] for f in r["failed"])
    db_session.expire_all()
    assert {db_session.get(AgentProposal, d).status for d in drafts} == {"pending"}
    # Rejecting them all together is not a choice between them: allowed.
    r = client.post(f"{_base(test_project)}/proposals/bulk", json={"ids": drafts, "action": "reject"}).json()
    assert sorted(r["decided"]) == sorted(drafts)


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


# ---------------------------------------------------------------------------
# Notifications and the report warning (commit 2)
# ---------------------------------------------------------------------------

def test_the_author_and_owner_get_one_notification_per_agent_session(client, db_session, test_project):
    """Decision 6: author AND owner are told; one notification per person per
    review run, kept current, never one per proposal; the operator whose
    agent it is is not told about their own run."""
    from app.db.models_project import Notification
    key, sid = _start(client, test_project)  # operator: the fixture admin
    host = _host(db_session, test_project)
    alice = _member(db_session, test_project, 421, "prop-alice")
    bob = _member(db_session, test_project, 422, "prop-bob")
    f1 = Finding(project_id=test_project.id, title="First", severity="low", status="open",
                 source="manual", created_by_id=alice.id, owner_id=bob.id)
    f2 = Finding(project_id=test_project.id, title="Second", severity="low", status="open",
                 source="manual", created_by_id=alice.id)
    db_session.add_all([f1, f2])
    db_session.commit()

    for fid in (f1.id, f2.id, f1.id):
        r = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
            "finding_id": fid, "fields": {"impact": "i", "description": "d"}, "agent_model": "model-a"})
        assert r.status_code == 201, r.text

    def notes(uid):
        db_session.expire_all()
        return db_session.query(Notification).filter(
            Notification.user_id == uid, Notification.type == "proposal").all()

    [a] = notes(alice.id)
    assert (a.source_type, a.source_id, a.finding_id) == ("agent_session", sid, None)
    assert "2 of your findings" in a.title and "model-a" in a.body
    [b] = notes(bob.id)  # owner of f1 only
    assert b.finding_id == f1.id and "First" in b.title
    assert notes(1) == []  # the operator's own run

    # Once read, the next proposal from the run starts a fresh notification.
    a.is_read = True
    db_session.commit()
    client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": f2.id, "fields": {"impact": "again"}})
    assert len(notes(alice.id)) == 2


def test_issuing_warns_about_pending_proposals_on_reported_findings(client, db_session, test_project, tmp_path, monkeypatch):
    import json
    folder = tmp_path / "report-templates" / "pentest"
    folder.mkdir(parents=True)
    (folder / "template.json").write_text(json.dumps({"title": "T", "entry": "report.qmd", "formats": ["html"]}))
    (folder / "report.qmd").write_text("---\ntitle: x\n---\n")
    monkeypatch.setattr(settings, "REPORT_TEMPLATES_DIR", str(tmp_path / "report-templates"))
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    f = Finding(project_id=test_project.id, title="Reported", severity="high", status="confirmed",
                source="manual", description="d", impact="i", recommendation="r")
    db_session.add(f)
    db_session.flush()
    db_session.add(FindingHost(finding_id=f.id, host_id=host.id, host_status="open"))
    db_session.commit()
    client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": f.id, "fields": {"impact": "better", "recommendation": "better"}})

    r = client.post(f"{_base(test_project)}/client-reports", json={"kind": "full"})
    assert r.status_code == 201, r.text
    summary = r.json()["summary"]
    assert summary["pending_proposals"] == [{"id": f.id, "ref": "F-01", "title": "Reported", "count": 2}]
    assert summary["agent_images"] == 0
    # A warning, never a block: it still issues.
    assert client.post(f"{_base(test_project)}/client-reports/{r.json()['id']}/issue").status_code == 200


def test_a_rejection_reason_reaches_a_fresh_agent_over_mcp(client, db_session, test_project):
    """Plan-feedback run 2026-09-30: the reviewer's reason (decision_note) is
    what a FRESH agent — a different session, no chat context — reads to
    revise its proposal, through the MCP tool, not only the REST route."""
    key, _ = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    pid = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"description": "Relay proven."}}).json()["proposals"][0]["id"]
    reason = "Remove the relay claim until the raw artifact is verified."
    assert client.post(f"{_base(test_project)}/proposals/{pid}/reject", json={"note": reason}).status_code == 200

    fresh, _ = _start(client, test_project)
    mcp = client.post("/api/v1/mcp", headers=fresh, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "list_proposals", "arguments": {"status": "rejected", "finding_id": finding["id"]}},
    }).json()["result"]
    assert mcp["isError"] is False
    [row] = mcp["structuredContent"]["items"]
    assert (row["id"], row["status"], row["decision_note"]) == (pid, "rejected", reason)
    assert row["decided_by"]


# ---------------------------------------------------------------------------
# Provenance and notification cost (review R2, R3)
# ---------------------------------------------------------------------------

def test_the_findings_history_names_the_proposal_each_accepted_change_came_from(client, db_session, test_project):
    """Decision 4: an accepted change says it came from an agent (or an AI
    draft) on the history row the change itself writes — never a second row.
    Report text had no history at all, so nothing showed an AI wrote it."""
    key, sid = _start(client, test_project)
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    base = _base(test_project)

    def history(fid):
        return [h["summary"] for h in client.get(f"{base}/findings/{fid}/history").json()]

    text = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
        "finding_id": finding["id"], "fields": {"impact": "Relay.", "description": "Signing off."},
        "agent_model": "model-a"}).json()["proposals"]
    impact = next(p["id"] for p in text if p["field"] == "impact")
    desc = next(p["id"] for p in text if p["field"] == "description")
    client.post(f"{base}/proposals/{impact}/accept", json={})
    client.post(f"{base}/proposals/{desc}/accept", json={"edited_value": "Signing is off."})
    summaries = history(finding["id"])
    assert f"Impact set from agent proposal #{impact} (model-a, agent session #{sid})" in summaries
    assert f"Description set (edited on accept) from agent proposal #{desc} (model-a, agent session #{sid})" in summaries

    fh = db_session.query(FindingHost).filter(FindingHost.finding_id == finding["id"]).one()
    ep = client.post("/api/v1/agent/proposals/endpoint-status", headers=key, json={
        "finding_id": finding["id"], "finding_host_id": fh.id, "host_status": "retest"}).json()["id"]
    before = len(history(finding["id"]))
    client.post(f"{base}/proposals/{ep}/accept", json={})
    after = history(finding["id"])
    assert len(after) == before + 1  # one row for one change
    assert any(s.startswith("Endpoint ") and s.endswith(f"from agent proposal #{ep} (model-a, agent session #{sid})")
               for s in after)

    new = client.post("/api/v1/agent/proposals/finding", headers=key, json={
        "title": "Proposed", "severity": "low", "host_ids": [host.id]}).json()["id"]
    created = client.post(f"{base}/proposals/{new}/accept", json={}).json()["result_finding_id"]
    assert history(created) == [f"Created from agent proposal #{new} (model-a, agent session #{sid})"]

    # The activity feed shows what such a row says, not "Marked … <status>".
    items = client.get(f"/api/v1/projects/{test_project.id}/workbench/my-activity",
                       params={"kinds": "finding_status"}).json()["items"]
    assert any(i["summary"].startswith("SMB signing not required: Impact set from agent proposal") for i in items)
    assert not any(i["summary"] == "Marked SMB signing not required open" for i in items)


def test_an_ai_draft_is_named_as_one_in_the_history(client, db_session, test_project, test_user):
    from app.db.models_proposals import ProposalSource
    from app.services import proposal_service
    host = _host(db_session, test_project)
    finding = _finding(client, test_project, host)
    who = proposal_service.Attribution(user_id=test_user.id, source=ProposalSource.LLM_DRAFT.value, model="llama-3")
    [p] = proposal_service.propose_finding_text(db_session, test_project.id, who,
                                                finding_id=finding["id"], fields={"impact": "x"})
    db_session.commit()
    proposal_service.accept_proposal(db_session, p, test_user)
    db_session.commit()
    summaries = [h["summary"] for h in client.get(f"{_base(test_project)}/findings/{finding['id']}/history").json()]
    assert f"Impact set from AI draft #{p.id}, llama-3" in summaries


def test_a_report_text_request_notifies_once_not_once_per_field(client, db_session, test_project):
    """Each notification re-reads the run's proposals; a six-field request
    used to do that six times per recipient."""
    from sqlalchemy import event
    key, _ = _start(client, test_project)
    alice = _member(db_session, test_project, 431, "prop-notify")
    f = Finding(project_id=test_project.id, title="Hers", severity="low", status="open",
                source="manual", created_by_id=alice.id)
    db_session.add(f)
    db_session.commit()

    statements = []
    listener = lambda conn, cur, stmt, *a: statements.append(stmt)  # noqa: E731
    event.listen(db_session.bind, "before_cursor_execute", listener)
    try:
        r = client.post("/api/v1/agent/proposals/finding-text", headers=key, json={
            "finding_id": f.id, "fields": {k: "text" for k in
                                           ("description", "impact", "recommendation", "references", "steps_to_reproduce")}})
    finally:
        event.remove(db_session.bind, "before_cursor_execute", listener)
    assert r.status_code == 201, r.text
    assert sum(1 for s in statements if "FROM notifications" in s) == 1
