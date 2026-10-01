"""Regression tests for review 2026-10-01 — findings and work state.

R9   a test result joins a concluded finding without re-judging it
R10  evidence output is capped at the schema and never hashed oversize
R11  a proposed finding's report text and evidence list are bounded
R12  host-note follower notifications go to current members only
N1   notification read times are timezone-aware
N4   note_type "" is a 422; attachment delete (global admin; file after commit)
N8   proposal routes serialize before commit; a same-key result replay; no
     deadlock between two sibling promotions
B9   an assigned test, and a proposal on nobody's finding, notify someone
B13  PATCH /findings/{id}/endpoints — several endpoints, all-or-nothing

Each fails on the code as it stood at 1b08b9c4.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import event, text

from app.core.config import settings
from app.db import models
from app.db.models import Annotation, FollowStatus, HostFollow, NoteAttachment
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost, FindingStatusHistory
from app.db.models_host_tests import HostTest
from app.db.models_project import Notification, ProjectMembership, ProjectRole
from app.db.models_proposals import AgentProposal, EvidenceRecord
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.schemas.host_test_schemas import HostTestResult
from app.services import agent_evidence_service, host_test_service, proposal_service
from app.services.notification_service import NotificationService
from tests.two_connections import two_sessions  # noqa: F401  (fixture)

MB5 = 5 * 1024 * 1024


# --- helpers -----------------------------------------------------------------

@pytest.fixture
def host(db_session, test_project):
    row = models.Host(project_id=test_project.id, ip_address="10.46.0.1", state="up")
    db_session.add(row)
    db_session.commit()
    return row


def _base(project):
    return f"/api/v1/projects/{project.id}"


def _member(db, project, user_id, username, role=ProjectRole.ANALYST, active=True, member=True):
    user = User(
        id=user_id, username=username, email=f"{username}@example.com", full_name=username.title(),
        hashed_password="x", role=UserRole.MEMBER, is_active=active, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    if member:
        db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role.value))
    db.commit()
    return user


def _test_item(host, **extra):
    return dict(request_key=str(uuid.uuid4()), host_id=host.id, tool="curl",
                description="Check response headers", rationale="Validate the observed web service", **extra)


def _create_test(client, project, payload):
    r = client.post(f"{_base(project)}/host-tests", json={"tests": [payload]})
    assert r.status_code == 201, r.text
    return r.json()["items"][0]


def _result(client, project, test, **over):
    body = {"expected_revision": test["revision"], "request_key": str(uuid.uuid4()),
            "outcome": "finding", "summary": "It is there", **over}
    return client.post(f"{_base(project)}/host-tests/{test['id']}/result", json=body), body


def _observation(db, project, host, title="SMB Signing not required"):
    scan = models.Scan(project_id=project.id, filename="n.nessus", tool_name="nessus", scan_type="nessus")
    db.add(scan)
    db.flush()
    vuln = Vulnerability(host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                         severity=VulnerabilitySeverity.MEDIUM, title=title)
    db.add(vuln)
    db.commit()
    return vuln


def _agent(client, project):
    r = client.post(f"{_base(project)}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}, r.json()["agent_session_id"]


def _notes(db, user_id, ntype=None):
    db.expire_all()
    q = db.query(Notification).filter(Notification.user_id == user_id)
    if ntype:
        q = q.filter(Notification.type == ntype)
    return q.order_by(Notification.id).all()


# --- R9 ----------------------------------------------------------------------

@pytest.mark.parametrize("prior, requested, expected", [
    ("accepted_risk", "confirmed", "accepted_risk"),
    ("remediated", "confirmed", "remediated"),
    ("false_positive", "confirmed", "false_positive"),
    ("confirmed", "open", "confirmed"),       # never a downgrade
    ("retest", "open", "retest"),
    ("open", "open", "open"),
    ("open", "confirmed", "confirmed"),       # the one move a result may make
    ("retest", "confirmed", "confirmed"),
])
def test_a_test_result_joins_a_finding_without_rejudging_the_issue(
    client, db_session, test_project, host, prior, requested, expected,
):
    """A result on ONE host used to set the status of the whole issue's
    finding: an accepted risk became confirmed for every host."""
    vuln = _observation(db_session, test_project, host)
    promoted = client.post(f"{_base(test_project)}/vulnerabilities/{vuln.id}/promote",
                           json={"vuln_id": vuln.id, "scope": "issue"})
    assert promoted.status_code == 201, promoted.text
    fid = promoted.json()["id"]
    if prior != "confirmed":
        moved = client.post(f"{_base(test_project)}/findings/{fid}/status",
                            json={"status": prior, "summary": "decided by the team"})
        assert moved.status_code == 200, moved.text
    history_before = db_session.query(FindingStatusHistory).filter_by(finding_id=fid).count()

    test = _create_test(client, test_project, _test_item(host, vulnerability_id=vuln.id))
    result, _ = _result(client, test_project, test)
    evidence_id = result.json()["evidence"]["id"]
    made = client.post(f"{_base(test_project)}/evidence/{evidence_id}/finding", json={"status": requested})
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["joined_issue"] is True and body["finding_id"] == fid
    assert body["status"] == expected            # the finding as it stands, not as asked

    db_session.expire_all()
    assert db_session.get(Finding, fid).status == expected
    assert db_session.get(EvidenceRecord, evidence_id).finding_id == fid
    assert db_session.query(Finding).count() == 1
    statuses = db_session.query(FindingStatusHistory).filter_by(finding_id=fid).count() - history_before
    assert statuses == (1 if expected != prior else 0)


def test_the_promote_click_itself_still_sets_the_status_it_was_given(client, db_session, test_project, host):
    """R9 narrows the evidence path only.  A person who promotes the
    observation again chose a status, and it is honoured as before."""
    vuln = _observation(db_session, test_project, host)
    url = f"{_base(test_project)}/vulnerabilities/{vuln.id}/promote"
    fid = client.post(url, json={"vuln_id": vuln.id, "status": "accepted_risk", "summary": "tolerated"}).json()["id"]
    again = client.post(url, json={"vuln_id": vuln.id, "status": "confirmed"})
    assert again.status_code == 201 and again.json()["id"] == fid and again.json()["status"] == "confirmed"


# --- R10 ---------------------------------------------------------------------

def test_a_persons_result_output_over_the_cap_is_a_413(client, db_session, test_project, host):
    test = _create_test(client, test_project, _test_item(host))
    # 413 from the service's check, never a 422 that echoes the text back.
    too_long, _ = _result(client, test_project, test, raw_output="x" * (MB5 + 1))
    assert too_long.status_code == 413, too_long.text[:300]
    assert len(too_long.text) < 2000
    # Under 5 M characters but over 5 MB once encoded.
    too_big, _ = _result(client, test_project, test, raw_output="é" * (MB5 // 2 + 1))
    assert too_big.status_code == 413, too_big.text[:300]
    assert db_session.query(EvidenceRecord).count() == 0
    ok, _ = _result(client, test_project, test, raw_output="x" * 1000)
    assert ok.status_code == 201, ok.text


def test_an_agents_output_over_the_cap_is_a_413(client, db_session, test_project, host):
    key, _ = _agent(client, test_project)
    body = {"host_id": host.id, "tool": "curl", "outcome": "info", "summary": "big"}
    assert client.post("/api/v1/agent/evidence", headers=key,
                       json={**body, "raw_output": "x" * (MB5 + 1)}).status_code == 413
    assert client.post("/api/v1/agent/evidence", headers=key,
                       json={**body, "raw_output": "é" * (MB5 // 2 + 1)}).status_code == 413
    assert db_session.query(EvidenceRecord).count() == 0


def test_oversize_output_is_refused_before_it_is_hashed(db_session, test_project, host, monkeypatch):
    """The size check ran after ``payload_hash`` had serialised and hashed
    the whole text."""
    hashed = []
    real = host_test_service.payload_hash
    monkeypatch.setattr(host_test_service, "payload_hash", lambda payload: hashed.append(1) or real(payload))
    with pytest.raises(HTTPException) as refused:
        agent_evidence_service.record_evidence(
            db_session, project_id=test_project.id, host_id=host.id, tool="curl", outcome="info",
            summary="big", raw_output="x" * (MB5 + 1), request_key="k-oversize",
        )
    assert refused.value.status_code == 413 and hashed == []
    agent_evidence_service.record_evidence(
        db_session, project_id=test_project.id, host_id=host.id, tool="curl", outcome="info",
        summary="fine", raw_output="x" * 10, request_key="k-fine",
    )
    assert hashed == [1]


def test_nul_in_a_result_is_refused_except_in_the_output(client, db_session, test_project, host):
    test = _create_test(client, test_project, _test_item(host))
    for field in ("request_key", "summary", "command", "observed_ip"):
        refused, _ = _result(client, test_project, test, **{field: "a\x00b"})
        assert refused.status_code == 422, (field, refused.text)
    # Tool output carries NULs; they are removed, not refused.
    kept, _ = _result(client, test_project, test, raw_output="line\x00one")
    assert kept.status_code == 201, kept.text
    evidence_id = kept.json()["evidence"]["id"]
    assert client.get(f"{_base(test_project)}/evidence/{evidence_id}/raw").text == "lineone"
    # The agent's request_key meets the same rule (it reached Postgres: a 500).
    key, _ = _agent(client, test_project)
    r = client.post("/api/v1/agent/evidence", headers=key, json={
        "host_id": host.id, "tool": "curl", "outcome": "info", "summary": "s", "request_key": "a\x00b"})
    assert r.status_code == 422, r.text


# --- R11 ---------------------------------------------------------------------

def test_a_proposed_findings_report_text_meets_the_limit(client, db_session, test_project, host):
    from app.services.report_text import REPORT_TEXT_MAX

    key, _ = _agent(client, test_project)
    body = {"title": "New issue", "severity": "high", "host_ids": [host.id]}
    long = client.post("/api/v1/agent/proposals/finding", headers=key,
                       json={**body, "report_text": {"description": "x" * (REPORT_TEXT_MAX + 1)}})
    assert long.status_code == 422 and "description" in long.text
    assert db_session.query(AgentProposal).count() == 0
    ok = client.post("/api/v1/agent/proposals/finding", headers=key,
                     json={**body, "report_text": {"description": "x" * REPORT_TEXT_MAX}})
    assert ok.status_code == 201, ok.text


def test_a_proposal_cites_at_most_a_hundred_evidence_records(client, db_session, test_project, host):
    key, _ = _agent(client, test_project)
    r = client.post("/api/v1/agent/proposals/finding", headers=key, json={
        "title": "New issue", "severity": "high", "host_ids": [host.id], "evidence_ids": list(range(1, 102))})
    assert r.status_code == 422, r.text
    # The service holds the rule too (the in-app drafter and MCP reach it).
    with pytest.raises(HTTPException) as refused:
        proposal_service._check_evidence(db_session, test_project.id, range(1, 102))
    assert refused.value.status_code == 422


# --- R12 ---------------------------------------------------------------------

def test_follower_note_notifications_go_to_current_active_members_only(db_session, test_project, test_user, host):
    """A follow row outlives a membership: someone removed from the project
    kept receiving the body of every new note on hosts they had reviewed."""
    current = _member(db_session, test_project, 4201, "still-here")
    removed = _member(db_session, test_project, 4202, "removed", member=False)
    inactive = _member(db_session, test_project, 4203, "inactive", active=False)
    for user in (current, removed, inactive):
        db_session.add(HostFollow(host_id=host.id, user_id=user.id, status=FollowStatus.IN_REVIEW))
    note = Annotation(host_id=host.id, user_id=test_user.id, body="domain admin creds in the share")
    db_session.add(note)
    db_session.commit()

    made = NotificationService(db_session).notify_host_followers_of_note(note, test_user, test_project)
    db_session.commit()
    assert [n.user_id for n in made] == [current.id]
    assert _notes(db_session, removed.id) == [] and _notes(db_session, inactive.id) == []


# --- N1 ----------------------------------------------------------------------

def test_read_times_are_written_timezone_aware(db_session, test_project, test_user):
    db_session.add_all([
        Notification(user_id=test_user.id, project_id=test_project.id, type="system", title="one"),
        Notification(user_id=test_user.id, project_id=test_project.id, type="system", title="two"),
    ])
    db_session.commit()
    first = _notes(db_session, test_user.id)[0]
    bound = []

    def capture(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE NOTIFICATIONS"):
            values = params.values() if isinstance(params, dict) else params
            bound.extend(v for v in values if isinstance(v, datetime))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        svc = NotificationService(db_session)
        assert svc.mark_read([first.id], test_user.id) == 1
        assert svc.mark_all_read(test_user.id) == 1
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert len(bound) == 2 and all(v.tzinfo is not None for v in bound), bound


# --- N4 ----------------------------------------------------------------------

def test_an_empty_note_type_is_a_422_not_a_500(client, db_session, test_project, test_user, host):
    note = Annotation(host_id=host.id, user_id=test_user.id, body="note")
    db_session.add(note)
    db_session.commit()
    url = f"{_base(test_project)}/hosts/{host.id}/notes/{note.id}"
    for empty in ("", "   "):
        r = client.patch(url, json={"note_type": empty})
        assert r.status_code == 422, r.text
    assert client.patch(url, json={"note_type": None}).status_code == 200   # null still clears it


def _attachment(db, project, host, uploader, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    stored = tmp_path / "note_attachments" / "p" / "proof.png"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"png")
    note = Annotation(host_id=host.id, user_id=uploader.id, body="proof")
    db.add(note)
    db.flush()
    att = NoteAttachment(
        annotation_id=note.id, project_id=project.id, filename="proof.png", content_type="image/png",
        size_bytes=3, storage_path="p/proof.png", uploaded_by_id=uploader.id,
    )
    db.add(att)
    db.commit()
    return att, stored


def test_a_global_admin_deletes_an_attachment_as_they_may_flag_it(
    client, db_session, test_project, host, tmp_path, monkeypatch,
):
    """The fixture login is a global admin with no membership row: allowed to
    mark the image for the report, refused when deleting it."""
    uploader = _member(db_session, test_project, 4301, "uploader")
    att, stored = _attachment(db_session, test_project, host, uploader, tmp_path, monkeypatch)
    url = f"{_base(test_project)}/hosts/notes/attachments/{att.id}"
    assert client.patch(url, json={"include_in_report": True}).status_code == 200
    assert client.delete(url).status_code == 204
    assert not stored.exists()
    db_session.expire_all()
    assert db_session.get(NoteAttachment, att.id) is None


def test_an_attachments_file_survives_a_delete_that_did_not_commit(
    client, db_session, test_project, host, test_user, tmp_path, monkeypatch,
):
    """The file was unlinked BEFORE the commit, so a failed commit left a row
    pointing at nothing."""
    att, stored = _attachment(db_session, test_project, host, test_user, tmp_path, monkeypatch)
    url = f"{_base(test_project)}/hosts/notes/attachments/{att.id}"

    def boom():
        raise RuntimeError("the commit failed")

    monkeypatch.setattr(db_session, "commit", boom)
    with pytest.raises(RuntimeError):
        client.delete(url)
    monkeypatch.undo()
    assert stored.exists()


# --- N8 ----------------------------------------------------------------------

def _text_proposals(client, db, project, host, key, n):
    ids = []
    for i in range(n):
        f = client.post(f"{_base(project)}/findings",
                        json={"title": f"Finding {i}", "severity": "low", "host_ids": [host.id]}).json()
        r = client.post("/api/v1/agent/proposals/finding-text", headers=key,
                        json={"finding_id": f["id"], "fields": {"impact": f"impact {i}"}})
        assert r.status_code == 201, r.text
        ids.append(r.json()["proposals"][0]["id"])
    return ids


def test_bulk_decide_serializes_nothing(client, db_session, test_project, host, monkeypatch):
    """It built each decided row — about five statements — and threw it away."""
    key, _ = _agent(client, test_project)
    ids = _text_proposals(client, db_session, test_project, host, key, 4)
    calls = []
    real = proposal_service.serialize_many
    monkeypatch.setattr(proposal_service, "serialize_many", lambda db, rows: calls.append(len(rows)) or real(db, rows))
    r = client.post(f"{_base(test_project)}/proposals/bulk", json={"ids": ids, "action": "accept"})
    assert r.status_code == 200, r.text
    assert r.json() == {"decided": ids, "failed": []}
    assert calls == []


def test_a_decided_proposal_is_returned_whole_from_before_the_commit(client, db_session, test_project, host, monkeypatch):
    key, _ = _agent(client, test_project)
    [pid] = _text_proposals(client, db_session, test_project, host, key, 1)
    order = []
    real_commit = db_session.commit
    real_serialize = proposal_service.serialize_many
    monkeypatch.setattr(db_session, "commit", lambda: order.append("commit") or real_commit())
    monkeypatch.setattr(proposal_service, "serialize_many",
                        lambda db, rows: order.append("serialize") or real_serialize(db, rows))
    r = client.post(f"{_base(test_project)}/proposals/{pid}/accept", json={"note": "good"})
    monkeypatch.undo()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "accepted" and body["decided_by"] == "Test Admin"
    assert body["decided_at"] and body["decision_note"] == "good" and body["result_finding_id"]
    assert body["current_value"] == "impact 0"       # the finding as the accept left it
    assert order.index("serialize") < len(order) - 1 and order[-1] == "commit"

    # The agent's own routes too.
    order.clear()
    monkeypatch.setattr(db_session, "commit", lambda: order.append("commit") or real_commit())
    monkeypatch.setattr(proposal_service, "serialize_many",
                        lambda db, rows: order.append("serialize") or real_serialize(db, rows))
    made = client.post("/api/v1/agent/proposals/finding", headers=key,
                       json={"title": "New", "severity": "low", "host_ids": [host.id]})
    assert made.status_code == 201 and made.json()["status"] == "pending" and made.json()["created_at"]
    assert order[-2:] == ["serialize", "commit"]


def _committed_test(pair, project, user):
    def build(db):
        host = models.Host(project_id=project.id, ip_address="10.47.0.1", state="up")
        db.add(host)
        db.flush()
        row = HostTest(
            project_id=project.id, host_id=host.id, tool="curl", description="d", rationale="r",
            source="person", created_by_user_id=user.id, request_key=str(uuid.uuid4()), request_hash="h",
        )
        db.add(row)
        db.flush()
        return row.id
    return pair.commit(build)


def _blocked(pair, thread, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline and thread.is_alive():
        with pair._engine.connect() as probe:
            if probe.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar():
                return True
        time.sleep(0.05)
    return False


def test_a_result_sent_twice_at_once_is_one_record_and_no_409(two_sessions):
    """A double click: both requests see no record under the key; the second
    insert waits on the first, is refused when it commits, and the evidence
    service confirms it is the same result.  It used to go on to take the
    test's revision again — a 409 for a result that had been stored."""
    project, user = two_sessions.project(role="analyst")
    test_id = _committed_test(two_sessions, project, user)
    body = HostTestResult(expected_revision=1, request_key="double-click", outcome="no_finding",
                          summary="Header present", raw_output="HTTP/1.1 200 OK")

    first_test, first_record = host_test_service.record_result(two_sessions.a, project.id, test_id, body, user.id)
    first_id = first_record.id                      # A: flushed, not committed

    outcome = {}

    def second():
        try:
            test, record = host_test_service.record_result(two_sessions.b, project.id, test_id, body, user.id)
            outcome["result"] = (test.status, test.revision, record.id)
            two_sessions.b.commit()
        except BaseException as exc:  # noqa: BLE001
            two_sessions.b.rollback()
            outcome["error"] = exc

    thread = threading.Thread(target=second, daemon=True)
    thread.start()
    assert _blocked(two_sessions, thread), f"the second request did not wait: {outcome}"
    two_sessions.a.commit()
    thread.join(20)
    assert not thread.is_alive()
    assert "error" not in outcome, repr(outcome.get("error"))
    assert outcome["result"] == ("done", 2, first_id)

    db = two_sessions.fresh()
    try:
        assert db.query(EvidenceRecord).filter_by(project_id=project.id).count() == 1
        assert db.get(HostTest, test_id).revision == 2
    finally:
        db.close()


def test_two_results_of_one_issue_promoted_together_do_not_deadlock(two_sessions):
    """Each promotion locked ITS record, then updated its sibling: two people
    promoting the two results of one issue each held the row the other
    needed.  The whole set is now locked in id order."""
    project, user = two_sessions.project(role="analyst")
    for attempt in range(6):
        def build(db, attempt=attempt):
            host = models.Host(project_id=project.id, ip_address=f"10.48.0.{attempt + 1}", state="up")
            scan = models.Scan(project_id=project.id, filename="n.nessus", tool_name="nessus", scan_type="nessus")
            db.add_all([host, scan])
            db.flush()
            vuln = Vulnerability(host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                                 severity=VulnerabilitySeverity.MEDIUM, title=f"Weak cipher {attempt}")
            db.add(vuln)
            db.flush()
            ids = []
            for n in range(2):
                test = HostTest(
                    project_id=project.id, host_id=host.id, tool="nmap", description="d", rationale="r",
                    source="person", created_by_user_id=user.id, request_key=str(uuid.uuid4()),
                    request_hash="h", issue_key=vuln.issue_key, issue_title=vuln.title,
                )
                db.add(test)
                db.flush()
                record = EvidenceRecord(
                    project_id=project.id, host_id=host.id, host_test_id=test.id, tool="nmap",
                    outcome="finding", summary=f"shown {n}", recorded_by_user_id=user.id,
                )
                db.add(record)
                db.flush()
                ids.append(record.id)
            return ids
        one, two = two_sessions.commit(build)

        def promote(evidence_id):
            def run(db):
                finding, _ = agent_evidence_service.create_finding_from_evidence(
                    db, project.id, evidence_id, title=None, severity=None, status="confirmed", actor_id=user.id,
                )
                return finding.id
            return run

        results = two_sessions.race(promote(one), promote(two))
        ids = [r for r in results if isinstance(r, int)]
        refused = [r for r in results if isinstance(r, HTTPException)]
        # One promotion makes the finding and takes both records; the other
        # then finds its record already on that finding (409).  Never a
        # database error, and never two findings.
        assert len(ids) + len(refused) == 2 and ids, results
        assert all(r.status_code == 409 for r in refused), results
        db = two_sessions.fresh()
        try:
            linked = {r.finding_id for r in db.query(EvidenceRecord).filter(EvidenceRecord.id.in_([one, two]))}
            assert linked == {ids[0]}
        finally:
            db.close()


# --- B9 ----------------------------------------------------------------------

def test_a_test_given_to_someone_tells_them_once(client, db_session, test_project, test_user, host):
    ana = _member(db_session, test_project, 4401, "ana")
    ben = _member(db_session, test_project, 4402, "ben")
    url = f"{_base(test_project)}/host-tests"

    # A batch of three for one person is ONE notification.
    batch = client.post(url, json={"tests": [_test_item(host, assigned_to_id=ana.id) for _ in range(3)]})
    assert batch.status_code == 201, batch.text
    [told] = _notes(db_session, ana.id)
    assert (told.type, told.source_type, told.host_id, told.project_id) == ("assignment", "host_test", host.id, test_project.id)
    assert told.source_id == batch.json()["items"][0]["id"] and told.actor_id == test_user.id
    assert "3 tests assigned to you on 10.46.0.1" == told.title and "Test Admin assigned you" in told.body

    # A retry of the same batch tells nobody again; a test you give yourself
    # tells nobody at all.
    client.post(url, json={"tests": [_test_item(host, assigned_to_id=test_user.id)]})
    assert len(_notes(db_session, ana.id)) == 1 and _notes(db_session, test_user.id) == []

    # Handing an existing test to someone else tells them …
    row = batch.json()["items"][0]
    moved = client.patch(f"{url}/{row['id']}", json={"expected_revision": row["revision"], "assigned_to_id": ben.id})
    assert moved.status_code == 200, moved.text
    [given] = _notes(db_session, ben.id)
    assert given.title == "Test assigned to you on 10.46.0.1" and given.source_id == row["id"]
    # … re-sending the same assignee, changing something else, or claiming it
    # yourself does not.
    again = client.patch(f"{url}/{row['id']}", json={"expected_revision": moved.json()["revision"], "assigned_to_id": ben.id})
    assert again.status_code == 200
    claimed = client.patch(f"{url}/{row['id']}", json={
        "expected_revision": again.json()["revision"], "assigned_to_id": test_user.id, "status": "in_progress"})
    assert claimed.status_code == 200, claimed.text
    assert len(_notes(db_session, ben.id)) == 1 and _notes(db_session, test_user.id) == []


def test_a_refused_assignment_notifies_nobody(client, db_session, test_project, host):
    ana = _member(db_session, test_project, 4411, "ana")
    row = _create_test(client, test_project, _test_item(host))
    stale = client.patch(f"{_base(test_project)}/host-tests/{row['id']}",
                         json={"expected_revision": row["revision"] + 5, "assigned_to_id": ana.id})
    assert stale.status_code == 409
    assert _notes(db_session, ana.id) == []


def test_a_proposal_on_nobodys_finding_tells_the_project_admins(client, db_session, test_project, test_user, host):
    """A proposed NEW finding, or a decision on an observation with no
    finding, has no author or owner to tell — it waited unseen."""
    padmin = _member(db_session, test_project, 4501, "padmin", role=ProjectRole.ADMIN)
    analyst = _member(db_session, test_project, 4502, "analyst")
    gone = _member(db_session, test_project, 4503, "ex-admin", role=ProjectRole.ADMIN, active=False)
    key, sid = _agent(client, test_project)          # operator: the fixture admin

    new = client.post("/api/v1/agent/proposals/finding", headers=key, json={
        "title": "Default credentials", "severity": "high", "host_ids": [host.id], "agent_model": "model-a"})
    assert new.status_code == 201, new.text
    [told] = _notes(db_session, padmin.id, "proposal")
    assert (told.source_type, told.source_id, told.finding_id) == ("agent_session_new", sid, None)
    assert told.title == "AI proposed a change that is on no finding yet" and "model-a" in told.body
    assert told.project_id == test_project.id and told.actor_id == test_user.id

    # An observation with no finding joins the same notification.
    vuln = _observation(db_session, test_project, host)
    obs = client.post("/api/v1/agent/proposals/observation", headers=key,
                      json={"vulnerability_id": vuln.id, "action": "dismiss"})
    assert obs.status_code == 201, obs.text
    [told] = _notes(db_session, padmin.id, "proposal")
    assert told.title == "AI proposed 2 changes that are on no finding yet"

    # Not the analyst, not an inactive admin, not the operator whose agent it is.
    assert _notes(db_session, analyst.id) == [] and _notes(db_session, gone.id) == []
    assert _notes(db_session, test_user.id, "proposal") == []

    # Once read, the next one starts a fresh notification.
    told.is_read = True
    db_session.commit()
    client.post("/api/v1/agent/proposals/finding", headers=key,
                json={"title": "Another", "severity": "low", "host_ids": [host.id]})
    assert len(_notes(db_session, padmin.id, "proposal")) == 2


def test_an_observation_that_has_a_finding_is_not_an_admins_notification(client, db_session, test_project, host):
    padmin = _member(db_session, test_project, 4511, "padmin", role=ProjectRole.ADMIN)
    vuln = _observation(db_session, test_project, host)
    assert client.post(f"{_base(test_project)}/vulnerabilities/{vuln.id}/promote",
                       json={"vuln_id": vuln.id}).status_code == 201
    key, _ = _agent(client, test_project)
    r = client.post("/api/v1/agent/proposals/observation", headers=key,
                    json={"vulnerability_id": vuln.id, "action": "dismiss"})
    assert r.status_code == 201, r.text
    assert _notes(db_session, padmin.id, "proposal") == []


# --- B13 ---------------------------------------------------------------------

def _finding_on(db, project, n, net=49):
    finding = Finding(project_id=project.id, title="Weak TLS", severity="medium", status="confirmed", source="manual")
    db.add(finding)
    db.flush()
    rows = []
    for i in range(n):
        h = models.Host(project_id=project.id, ip_address=f"10.{net}.{i // 250}.{i % 250 + 1}", state="up")
        db.add(h)
        db.flush()
        row = FindingHost(finding_id=finding.id, host_id=h.id, host_status="open")
        db.add(row)
        db.flush()
        rows.append(row.id)
    db.commit()
    return finding.id, rows


def test_several_endpoints_change_state_in_one_request(client, db_session, test_project):
    fid, rows = _finding_on(db_session, test_project, 6)
    url = f"{_base(test_project)}/findings/{fid}/endpoints"
    r = client.patch(url, json={"finding_host_ids": rows[:4], "host_status": "remediated", "summary": "retest 1 Oct"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == fid and body["host_count"] == 6 and len(body["hosts"]) == 6
    assert body["endpoint_status_counts"] == {"remediated": 4, "open": 2}
    assert {h["id"] for h in body["hosts"] if h["host_status"] == "remediated"} == set(rows[:4])
    assert body["status"] == "confirmed"          # the finding's status is the issue's

    # The same history line the single route writes, one per endpoint moved.
    history = client.get(f"{_base(test_project)}/findings/{fid}/history").json()
    moved = [h["summary"] for h in history if h["summary"] and h["summary"].startswith("Endpoint ")]
    assert len(moved) == 4 and all(s.endswith("open → remediated — retest 1 Oct") for s in moved)
    single = client.patch(f"{url}/{rows[4]}", json={"host_status": "remediated"})
    assert single.status_code == 200
    latest = client.get(f"{_base(test_project)}/findings/{fid}/history").json()[0]["summary"]
    assert latest.endswith("open → remediated") and latest.startswith("Endpoint 10.49.0.5")

    # Endpoints already in that state write nothing; a repeated id counts once.
    again = client.patch(url, json={"finding_host_ids": rows[:4] + rows[:1], "host_status": "remediated"})
    assert again.status_code == 200
    assert len(client.get(f"{_base(test_project)}/findings/{fid}/history").json()) == len(history) + 1


def test_the_bulk_change_is_all_or_nothing(client, db_session, test_project):
    fid, rows = _finding_on(db_session, test_project, 3)
    other, foreign = _finding_on(db_session, test_project, 1, net=50)
    url = f"{_base(test_project)}/findings/{fid}/endpoints"

    refused = client.patch(url, json={"finding_host_ids": rows + foreign, "host_status": "remediated"})
    assert refused.status_code == 404 and str(foreign[0]) in refused.json()["detail"]
    db_session.expire_all()
    assert {r.host_status for r in db_session.query(FindingHost)} == {"open"}
    assert db_session.query(FindingStatusHistory).filter_by(finding_id=fid).count() == 0

    assert client.patch(url, json={"finding_host_ids": rows, "host_status": "fixed"}).status_code == 422
    assert client.patch(url, json={"finding_host_ids": [], "host_status": "open"}).status_code == 422
    assert client.patch(url, json={"finding_host_ids": list(range(1, 502)), "host_status": "open"}).status_code == 422
    assert client.patch(f"{_base(test_project)}/findings/999999/endpoints",
                        json={"finding_host_ids": rows, "host_status": "open"}).status_code == 404
    db_session.expire_all()
    assert {r.host_status for r in db_session.query(FindingHost)} == {"open"}


def test_the_bulk_change_reads_nothing_per_endpoint(client, db_session, test_project):
    def statements(n, net):
        fid, rows = _finding_on(db_session, test_project, n, net=net)
        db_session.expire_all()
        seen = []

        def count(conn, cursor, statement, params, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                seen.append(statement)

        bind = db_session.get_bind()
        event.listen(bind, "before_cursor_execute", count)
        try:
            r = client.patch(f"{_base(test_project)}/findings/{fid}/endpoints",
                             json={"finding_host_ids": rows, "host_status": "retest"})
        finally:
            event.remove(bind, "before_cursor_execute", count)
        assert r.status_code == 200, r.text
        assert r.json()["endpoint_status_counts"] == {"retest": n}
        return seen

    few, many = statements(3, 51), statements(40, 52)
    assert len(many) == len(few), "\n".join(many)


def test_there_is_no_agent_route_for_the_bulk_change(client, db_session, test_project):
    """An agent's endpoint change stays a proposal."""
    fid, rows = _finding_on(db_session, test_project, 2)
    key, _ = _agent(client, test_project)
    paths = set(client.get("/openapi.json").json()["paths"])
    assert "/api/v1/projects/{project_id}/findings/{finding_id}/endpoints" in paths
    assert not [p for p in paths if p.startswith("/api/v1/agent/") and p.endswith("/endpoints")]
