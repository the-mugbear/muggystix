"""Branch review of ``fix/review-2026-10-01`` — the findings / work-state list
(S1, M1, M2, M3, M5, M6) and the session-id item m1.

Each test fails on the code as it was reviewed.

* S1 — the misconfiguration backfill re-keyed a second scanner finding onto a
  catalog check's key, which ``uq_finding_scanner_issue`` refuses: the run
  failed with a raw ``IntegrityError`` and could never complete.
* M1 — the promote click inserted the finding and then locked the issue's
  evidence; promoting a test's result locked the evidence and then inserted.
  Opposite orders: a deadlock.
* M2 — the bulk promotion decided the status to pass from a read made before
  its loop, so a finding created in between was re-statused and reported as
  created.
* M3 — a finding's author / owner was told about proposals after leaving the
  project or being deactivated.
* M5 — an agent proposing N tests for someone in N calls sent N notifications.
* M6 — the admins' "on no finding yet" count included observation proposals
  whose observation HAS a finding.
* m1 — the start response's deprecated ``assist_session_id`` was not accepted
  by the deprecated routes for a session started after v2.449.0.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.db import models
from app.db.models_agent import AgentSession
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_host_tests import HostTest
from app.db.models_project import Notification, Project, ProjectMembership, ProjectRole
from app.db.models_proposals import EvidenceRecord
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.parsers.parser_utils import upsert_vulnerability
from app.services import agent_evidence_service, scanner_observation_service
from app.services.finding_actions import promote_or_dismiss_vulnerability
from app.services.finding_service import FindingService
from app.services.misconfig_backfill import UNMERGED_KEY, backfill_misconfigs
from tests.two_connections import two_sessions  # noqa: F401  (fixture)


# --- helpers -----------------------------------------------------------------

@pytest.fixture
def host(db_session, test_project):
    row = models.Host(project_id=test_project.id, ip_address="10.52.0.1", state="up")
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


def _scan(db, project, tool="nessus"):
    scan = models.Scan(project_id=project.id, filename="old", tool_name=tool, scan_type=tool)
    db.add(scan)
    db.flush()
    return scan


def _observation(db, project, host, title="SMB Signing not required"):
    vuln = Vulnerability(host_id=host.id, scan_id=_scan(db, project).id, source=VulnerabilitySource.NESSUS,
                         severity=VulnerabilitySeverity.MEDIUM, title=title)
    db.add(vuln)
    db.commit()
    return vuln


# --- S1 ----------------------------------------------------------------------

def _two_tools_one_check(db, project, host):
    """A Nikto row and a testssl row for HSTS, stored before the catalog."""
    scan = _scan(db, project, "nikto")
    nikto = upsert_vulnerability(
        db=db, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NIKTO,
        title="/: Suggested security header missing: strict-transport-security",
        severity=VulnerabilitySeverity.LOW, plugin_id="013587", key_on_title=True)
    testssl = upsert_vulnerability(
        db=db, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.TESTSSL,
        title="HSTS not set", severity=VulnerabilitySeverity.LOW, plugin_id="HSTS")
    db.flush()
    return nikto, testssl


def _scanner_finding(db, project, vuln, key=None):
    finding = Finding(project_id=project.id, title=vuln.title[:200], severity="low", status="confirmed",
                      source="scanner", vuln_id=vuln.id, dedup_key=key or vuln.issue_key)
    db.add(finding)
    db.flush()
    return finding


def test_the_backfill_leaves_a_second_finding_of_one_check_alone_and_reports_the_pair(
    db_session, test_project, host,
):
    nikto, testssl = _two_tools_one_check(db_session, test_project, host)
    assert nikto.issue_key != testssl.issue_key          # two issues, before the catalog
    first = _scanner_finding(db_session, test_project, nikto)
    second = _scanner_finding(db_session, test_project, testssl)
    second_key = second.dedup_key

    unmerged: list = []
    counts = backfill_misconfigs(db_session, project_id=test_project.id, unmerged=unmerged)  # raised IntegrityError
    db_session.flush()

    assert nikto.issue_key == testssl.issue_key == "check:http_missing_hsts"
    assert first.dedup_key == "check:http_missing_hsts"
    # The second finding is untouched — nothing is merged for the operator.
    assert second.dedup_key == second_key and second.status == "confirmed"
    assert db_session.query(Finding).filter_by(project_id=test_project.id).count() == 2
    pair = {"project_id": test_project.id, "check_id": "http_missing_hsts",
            "finding_id": second.id, "kept_finding_id": first.id}
    assert unmerged == [pair] and counts[UNMERGED_KEY] == 1

    # Reported by every run until someone merges them — not only by the one
    # that adopted the rows.
    again: list = []
    counts = backfill_misconfigs(db_session, project_id=test_project.id, unmerged=again)
    assert again == [pair] and counts == {UNMERGED_KEY: 1}


def test_the_backfill_does_not_rekey_onto_a_check_that_already_has_a_finding(db_session, test_project, host):
    """An old finding, plus one promoted after the catalog existed."""
    nikto, _testssl = _two_tools_one_check(db_session, test_project, host)
    old = _scanner_finding(db_session, test_project, nikto)
    old_key = old.dedup_key
    current = Finding(project_id=test_project.id, title="HSTS", severity="low", status="open",
                      source="scanner", dedup_key="check:http_missing_hsts")
    db_session.add(current)
    db_session.flush()
    # A manual finding that cites the row is outside the index and still moves.
    manual = Finding(project_id=test_project.id, title="written by hand", severity="low", status="open",
                     source="manual", vuln_id=nikto.id, dedup_key="title:something")
    db_session.add(manual)
    db_session.flush()

    unmerged: list = []
    backfill_misconfigs(db_session, project_id=test_project.id, unmerged=unmerged)
    db_session.flush()

    assert old.dedup_key == old_key and manual.dedup_key == "check:http_missing_hsts"
    assert [(p["finding_id"], p["kept_finding_id"]) for p in unmerged] == [(old.id, current.id)]


def test_the_backfill_script_names_the_pairs(monkeypatch, db_session, test_project, host, capsys):
    from tests.test_data_repairs import _run_script

    nikto, testssl = _two_tools_one_check(db_session, test_project, host)
    first = _scanner_finding(db_session, test_project, nikto)
    second = _scanner_finding(db_session, test_project, testssl)
    db_session.commit()

    _run_script(monkeypatch, db_session, "backfill_misconfigs.py", ["--project", str(test_project.id)])
    out = capsys.readouterr().out
    assert "1 finding(s) NOT moved onto their catalog check" in out and "Merge them by hand" in out
    assert (f"project {test_project.id}: finding #{second.id} left as it is; "
            f"finding #{first.id} is the one for check http_missing_hsts") in out
    # The pair is not counted as an observation.
    assert "Recorded or refreshed 2 observation(s)" in out


# --- M1 ----------------------------------------------------------------------

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


def test_the_promote_click_and_a_result_promotion_take_their_locks_in_one_order(two_sessions):
    """B (promoting a test's result) holds the issue's evidence; A (the
    promote click on the same observation) arrives.  A used to insert the
    finding and then wait for the evidence, so when B went on to insert, each
    held what the other needed and Postgres ended one with a deadlock error.
    A now waits for the evidence BEFORE it inserts: B finishes, A joins."""
    project, user = two_sessions.project(role="analyst")

    def build(db):
        host = models.Host(project_id=project.id, ip_address="10.53.0.1", state="up")
        scan = models.Scan(project_id=project.id, filename="n.nessus", tool_name="nessus", scan_type="nessus")
        db.add_all([host, scan])
        db.flush()
        vuln = Vulnerability(host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                             severity=VulnerabilitySeverity.MEDIUM, title="Weak cipher suites offered")
        db.add(vuln)
        db.flush()
        test = HostTest(
            project_id=project.id, host_id=host.id, tool="nmap", description="d", rationale="r",
            source="person", created_by_user_id=user.id, request_key=str(uuid.uuid4()),
            request_hash="h", issue_key=vuln.issue_key, issue_title=vuln.title,
        )
        db.add(test)
        db.flush()
        record = EvidenceRecord(
            project_id=project.id, host_id=host.id, host_test_id=test.id, tool="nmap",
            outcome="finding", summary="shown", recorded_by_user_id=user.id,
        )
        db.add(record)
        db.flush()
        return vuln.id, host.id, vuln.issue_key, record.id

    vuln_id, host_id, issue_key, evidence_id = two_sessions.commit(build)

    # B's first step, exactly as create_finding_from_evidence takes it.
    b = two_sessions.b
    agent_evidence_service.lock_issue_evidence(b, host_ids=[host_id], issue_key=issue_key, also=evidence_id)

    outcome = {}

    def click():
        a = two_sessions.a
        try:
            finding = promote_or_dismiss_vulnerability(
                a, vuln=a.get(Vulnerability, vuln_id), project_id=project.id, actor_id=user.id, scope="host",
            )
            outcome["finding"] = finding.id
            a.commit()
        except BaseException as exc:  # noqa: BLE001
            a.rollback()
            outcome["error"] = exc

    thread = threading.Thread(target=click, daemon=True)
    thread.start()
    assert _blocked(two_sessions, thread), f"the click did not wait for the evidence: {outcome}"

    # B goes on to make the finding.  With A's uncommitted finding in the
    # unique index this insert waited on A while A waited on B.
    finding, joined = agent_evidence_service.create_finding_from_evidence(
        b, project.id, evidence_id, title=None, severity=None, status="confirmed", actor_id=user.id,
    )
    made = finding.id
    b.commit()
    thread.join(20)
    assert not thread.is_alive()
    assert "error" not in outcome, repr(outcome.get("error"))
    assert joined is True and outcome["finding"] == made      # the click joined B's finding

    db = two_sessions.fresh()
    try:
        assert [f.id for f in db.query(Finding).filter_by(project_id=project.id)] == [made]
        assert db.get(EvidenceRecord, evidence_id).finding_id == made
    finally:
        db.close()


# --- M2 ----------------------------------------------------------------------

def test_a_bulk_promotion_never_restatuses_a_finding_made_while_it_ran(
    client, db_session, test_project, test_user, monkeypatch,
):
    """Someone dismisses the issue on one host (a false-positive finding)
    between the bulk promotion's read and its write.  Replayed on one
    connection: the bulk path's earlier read, and the service's lookup, both
    answer "no finding yet" — as they do for the loser of the real race."""
    scan = _scan(db_session, test_project)
    vulns = []
    for i in (1, 2):
        h = models.Host(project_id=test_project.id, ip_address=f"10.54.0.{i}", state="up")
        db_session.add(h)
        db_session.flush()
        v = Vulnerability(host_id=h.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                          severity=VulnerabilitySeverity.MEDIUM, title="SMB Signing not required")
        db_session.add(v)
        db_session.flush()
        vulns.append(v)
    db_session.commit()
    key = vulns[0].issue_key
    dismissed = FindingService(db_session).dismiss_vulnerability_on_host(
        vuln=vulns[0], project_id=test_project.id, actor_id=test_user.id, summary="patched here",
    )
    db_session.commit()
    assert dismissed.status == "false_positive"

    monkeypatch.setattr(scanner_observation_service, "_findings_by_key", lambda db, project_id, keys: {})
    real = FindingService._scanner_finding_for
    calls = {"n": 0}

    def blind_once(self, vuln, project_id, k):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(self, vuln, project_id, k)

    monkeypatch.setattr(FindingService, "_scanner_finding_for", blind_once)

    r = client.post(f"{_base(test_project)}/scanner-observations/promote", json={"items": [{"issue_key": key}]})
    assert r.status_code in (200, 201), r.text
    [result] = r.json()["results"]
    assert result["finding_id"] == dismissed.id
    assert result["created"] is False                       # it joined; it did not create
    db_session.expire_all()
    assert db_session.query(Finding).filter_by(project_id=test_project.id).count() == 1
    assert db_session.get(Finding, dismissed.id).status == "false_positive"   # was re-statused to confirmed
    rows = {r.host_id: r.host_status for r in db_session.query(FindingHost).filter_by(finding_id=dismissed.id)}
    assert rows == {vulns[0].host_id: "false_positive", vulns[1].host_id: "open"}


def test_a_bulk_promotion_reports_created_for_a_finding_it_made(client, db_session, test_project, host):
    vuln = _observation(db_session, test_project, host)
    url = f"{_base(test_project)}/scanner-observations/promote"
    first = client.post(url, json={"items": [{"issue_key": vuln.issue_key}]}).json()["results"][0]
    again = client.post(url, json={"items": [{"issue_key": vuln.issue_key}]}).json()["results"][0]
    assert (first["created"], again["created"]) == (True, False) and first["finding_id"] == again["finding_id"]
    assert db_session.get(Finding, first["finding_id"]).status == "confirmed"


# --- M3 ----------------------------------------------------------------------

def test_proposal_notifications_go_to_current_active_members_only(client, db_session, test_project, host):
    left = _member(db_session, test_project, 4601, "left", member=False)          # no longer a member
    off = _member(db_session, test_project, 4602, "off", active=False)            # deactivated
    here = _member(db_session, test_project, 4603, "here")
    finding = Finding(project_id=test_project.id, title="Weak TLS", severity="medium", status="confirmed",
                      source="manual", created_by_id=left.id, owner_id=off.id)
    theirs = Finding(project_id=test_project.id, title="Open relay", severity="medium", status="confirmed",
                     source="manual", created_by_id=here.id, owner_id=left.id)
    db_session.add_all([finding, theirs])
    db_session.commit()
    key, _sid = _agent(client, test_project)

    for f in (finding, theirs):
        r = client.post("/api/v1/agent/proposals/finding-text", headers=key,
                        json={"finding_id": f.id, "fields": {"impact": "rewritten"}})
        assert r.status_code == 201, r.text

    assert _notes(db_session, left.id) == [] and _notes(db_session, off.id) == []
    [told] = _notes(db_session, here.id, "proposal")
    assert told.finding_id == theirs.id


# --- M5 ----------------------------------------------------------------------

def _item(host, **extra):
    return dict(request_key=str(uuid.uuid4()), host_id=host.id, tool="curl",
                description="Check response headers", rationale="Validate the observed web service", **extra)


def test_an_agents_tests_for_someone_are_one_notification_per_session(client, db_session, test_project, host):
    ana = _member(db_session, test_project, 4701, "ana")
    key, _sid = _agent(client, test_project)
    url = "/api/v1/agent/host-tests"

    ids = []
    for _ in range(3):                                   # three single-test calls
        r = client.post(url, headers=key, json={"tests": [_item(host, assigned_to_id=ana.id)]})
        assert r.status_code == 201, r.text
        ids.append(r.json()["items"][0]["id"])
    [told] = _notes(db_session, ana.id)                  # was three notifications
    assert (told.type, told.source_type, told.source_id, told.host_id) == ("assignment", "host_test", ids[0], host.id)
    assert told.title == "3 tests assigned to you on 10.52.0.1" and "(and 2 more)" in told.body

    # A test on a second host: still one line, which now opens the work list.
    other = models.Host(project_id=test_project.id, ip_address="10.52.0.2", state="up")
    db_session.add(other)
    db_session.commit()
    assert client.post(url, headers=key, json={"tests": [_item(other, assigned_to_id=ana.id)]}).status_code == 201
    [told] = _notes(db_session, ana.id)
    assert told.title == "4 tests assigned to you on 2 hosts" and told.host_id is None and told.source_id == ids[0]

    # Another session of the same operator is its own notification.
    key2, _ = _agent(client, test_project)
    assert client.post(url, headers=key2, json={"tests": [_item(host, assigned_to_id=ana.id)]}).status_code == 201
    assert [n.title for n in _notes(db_session, ana.id)] == [
        "4 tests assigned to you on 2 hosts", "Test assigned to you on 10.52.0.1"]

    # Once read, the session's next test starts a fresh one that counts only
    # what came since.
    for n in _notes(db_session, ana.id):
        n.is_read = True
    db_session.commit()
    last = client.post(url, headers=key, json={"tests": [_item(host, assigned_to_id=ana.id)]})
    assert last.status_code == 201
    fresh = [n for n in _notes(db_session, ana.id) if not n.is_read]
    assert [(n.title, n.source_id) for n in fresh] == [
        ("Test assigned to you on 10.52.0.1", last.json()["items"][0]["id"])]


def test_a_person_assigning_by_hand_notifies_per_action(client, db_session, test_project, host):
    ana = _member(db_session, test_project, 4711, "ana")
    for _ in range(2):
        r = client.post(f"{_base(test_project)}/host-tests", json={"tests": [_item(host, assigned_to_id=ana.id)]})
        assert r.status_code == 201, r.text
    assert [n.title for n in _notes(db_session, ana.id)] == ["Test assigned to you on 10.52.0.1"] * 2


# --- M6 ----------------------------------------------------------------------

def test_the_admins_count_leaves_out_an_observation_that_has_a_finding(client, db_session, test_project, host):
    padmin = _member(db_session, test_project, 4801, "padmin", role=ProjectRole.ADMIN)
    vuln = _observation(db_session, test_project, host)
    assert client.post(f"{_base(test_project)}/vulnerabilities/{vuln.id}/promote",
                       json={"vuln_id": vuln.id}).status_code == 201
    key, _sid = _agent(client, test_project)
    # On a finding (through its observation): its author is told, not the admins.
    r = client.post("/api/v1/agent/proposals/observation", headers=key,
                    json={"vulnerability_id": vuln.id, "action": "dismiss"})
    assert r.status_code == 201, r.text
    assert _notes(db_session, padmin.id, "proposal") == []

    # One proposal that really is on no finding: "a change", not "2 changes".
    r = client.post("/api/v1/agent/proposals/finding", headers=key,
                    json={"title": "Default credentials", "severity": "high", "host_ids": [host.id]})
    assert r.status_code == 201, r.text
    [told] = _notes(db_session, padmin.id, "proposal")
    assert told.title == "AI proposed a change that is on no finding yet"

    # An observation with no finding does count.
    bare = _observation(db_session, test_project, host, title="Telnet server detected")
    r = client.post("/api/v1/agent/proposals/observation", headers=key,
                    json={"vulnerability_id": bare.id, "action": "dismiss"})
    assert r.status_code == 201, r.text
    [told] = _notes(db_session, padmin.id, "proposal")
    assert told.title == "AI proposed 2 changes that are on no finding yet"


# --- m1 ----------------------------------------------------------------------

def test_the_deprecated_routes_take_the_id_the_start_response_gives(client, db_session, test_project):
    r = client.post(f"{_base(test_project)}/assist/start", json={"purpose": "old tab"})
    assert r.status_code == 201, r.text
    sid = r.json()["assist_session_id"]                  # what a client of the old shape reads
    assert db_session.get(AgentSession, sid).legacy_assist_session_id is None

    for path in (f"/assist-sessions/{sid}", f"/assist/sessions/{sid}"):
        got = client.get(_base(test_project) + path)
        assert got.status_code == 200, f"{path}: {got.text}"
        assert got.json()["id"] == sid and got.json()["purpose"] == "old tab"
    assert client.get(f"{_base(test_project)}/assist-sessions/{sid}/api-activity").status_code == 200
    assert client.post(f"{_base(test_project)}/assist/sessions/{sid}/end").status_code == 204
    db_session.expire_all()
    assert db_session.get(AgentSession, sid).status == "ended"


def test_an_old_id_wins_over_a_session_id_and_another_projects_id_is_not_found(client, db_session, test_project):
    def start(project, purpose):
        r = client.post(f"{_base(project)}/assist/start", json={"purpose": purpose})
        assert r.status_code == 201, r.text
        return r.json()["agent_session_id"]

    new = start(test_project, "started after the upgrade")
    old = start(test_project, "started before it")
    # ``old`` had the assist_sessions id that happens to equal ``new``'s id.
    db_session.query(AgentSession).filter(AgentSession.id == old).update({"legacy_assist_session_id": new})
    db_session.commit()
    got = client.get(f"{_base(test_project)}/assist-sessions/{new}")
    assert got.status_code == 200 and got.json()["id"] == old

    other = Project(name="other-m1", slug="other-m1", description="another project")
    db_session.add(other)
    db_session.commit()
    theirs = start(other, "another project's")
    db_session.query(AgentSession).filter(AgentSession.id == theirs).update({"legacy_assist_session_id": 990001})
    db_session.commit()
    base = _base(test_project)
    for foreign in (theirs, 990001):                     # its own id, and its old id
        assert client.get(f"{base}/assist-sessions/{foreign}").status_code == 404
        assert client.get(f"{base}/assist/sessions/{foreign}").status_code == 404
        assert client.get(f"{base}/assist-sessions/{foreign}/api-activity").status_code == 404
        assert client.post(f"{base}/assist/sessions/{foreign}/end").status_code == 404
    db_session.expire_all()
    assert db_session.get(AgentSession, theirs).status == "active"
