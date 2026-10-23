"""Review 2026-10-23, findings / triage (H-TR1, R-TR1, R-TR2 and the nits).

* H-TR1 — accepting an agent's ``finding_create`` proposal never linked the
  evidence it cited: the host test still counted an unpromoted result and
  offered "Create finding", and a second finding followed.  The accept is now
  the result's own "Create finding" (``agent_evidence_service.promote_evidence``).
* R-TR1 — the status change, the PATCH and the bulk writes took no finding
  lock first, so the history's "before" could be a value already replaced.
* R-TR2 — the bulk answers counted findings already as asked as affected.
* A follow-up for a past day was stamped noon UTC (the next day east of
  UTC+12); the retest notice told viewers and auditors to act.

(H-TR2, the restore's retest request, is in ``test_retest_workflow.py``.)
"""
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost, FindingStatusHistory
from app.db.models_project import Notification, ProjectMembership
from app.db.models_proposals import EvidenceRecord
from app.db.models_remediation import RemediationEvent, RemediationPolicy
from app.services import remediation_policy
from app.services.finding_actions import assign_findings, set_owner
from app.services.finding_service import FindingService
from tests.two_connections import two_sessions  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _evidence_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))


def _p(project):
    return f"/api/v1/projects/{project.id}"


def _host(db, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.commit()
    return host


def _agent(client, project):
    r = client.post(f"{_p(project)}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


def _test_with_result(client, project, host, **extra):
    """A person's host test and its result showing an issue; returns
    ``(test, evidence_id)``."""
    r = client.post(f"{_p(project)}/host-tests", json={"tests": [dict(
        request_key=str(uuid.uuid4()), host_id=host.id, tool="curl", description="Check headers",
        rationale="Validate the web service", **extra)]})
    assert r.status_code == 201, r.text
    test = r.json()["items"][0]
    r = client.post(f"{_p(project)}/host-tests/{test['id']}/result", json={
        "expected_revision": test["revision"], "request_key": str(uuid.uuid4()),
        "outcome": "finding", "summary": "It showed the issue"})
    assert r.status_code == 201, r.text
    return test, r.json()["evidence"]["id"]


def _observation(db, project, host, title="SMB Signing not required"):
    from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource

    scan = models.Scan(project_id=project.id, filename="n.nessus", tool_name="nessus", scan_type="nessus")
    db.add(scan)
    db.flush()
    vuln = Vulnerability(host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                         severity=VulnerabilitySeverity.MEDIUM, title=title)
    db.add(vuln)
    db.commit()
    return vuln


def _propose(client, key, hosts, evidence_ids, **extra):
    r = client.post("/api/v1/agent/proposals/finding", headers=key, json={
        "title": "Clickjacking possible", "severity": "low", "host_ids": [h.id for h in hosts],
        "evidence_ids": evidence_ids, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _accept(client, project, pid):
    return client.post(f"{_p(project)}/proposals/{pid}/accept", json={})


# --- H-TR1 -------------------------------------------------------------------

def test_accepting_a_proposed_finding_links_the_result_it_cites(client, db_session, test_project):
    host, other = _host(db_session, test_project, "10.81.0.1"), _host(db_session, test_project, "10.81.0.2")
    test, evidence_id = _test_with_result(client, test_project, host)
    pid = _propose(client, _agent(client, test_project), [host, other], [evidence_id],
                   report_text={"description": "Framing is allowed."})

    accepted = _accept(client, test_project, pid)
    assert accepted.status_code == 200, accepted.text
    finding_id = accepted.json()["result_finding_id"]
    db_session.expire_all()
    assert db_session.get(EvidenceRecord, evidence_id).finding_id == finding_id
    finding = db_session.get(Finding, finding_id)
    assert (finding.title, finding.severity, finding.description) == ("Clickjacking possible", "low", "Framing is allowed.")
    assert {fh.host_id for fh in db_session.query(FindingHost).filter_by(finding_id=finding_id)} == {host.id, other.id}
    # The test counts the result as promoted, and offers no second finding.
    read = client.get(f"{_p(test_project)}/host-tests/{test['id']}").json()
    assert read["unpromoted_findings"] == 0 and read["finding_ids"] == [finding_id]
    assert client.post(f"{_p(test_project)}/evidence/{evidence_id}/finding",
                       json={"title": "Again", "severity": "low"}).status_code == 409
    assert db_session.query(Finding).filter_by(project_id=test_project.id).count() == 1


def test_an_accepted_proposal_citing_a_confirmed_observation_joins_the_issues_finding(
    client, db_session, test_project,
):
    host = _host(db_session, test_project, "10.81.1.1")
    vuln = _observation(db_session, test_project, host)
    promoted = client.post(f"{_p(test_project)}/vulnerabilities/{vuln.id}/promote",
                           json={"vuln_id": vuln.id, "scope": "host", "status": "open"})
    assert promoted.status_code == 201, promoted.text
    existing = promoted.json()["id"]
    _, evidence_id = _test_with_result(client, test_project, host, vulnerability_id=vuln.id)
    key = _agent(client, test_project)

    # With report text the accept would replace the issue finding's own: refused, nothing written.
    with_text = _propose(client, key, [host], [evidence_id], report_text={"description": "Agent text."})
    refused = _accept(client, test_project, with_text)
    assert refused.status_code == 409, refused.text
    assert f"#{existing}" in refused.json()["detail"]
    db_session.expire_all()
    assert db_session.get(EvidenceRecord, evidence_id).finding_id is None

    plain = _propose(client, key, [host], [evidence_id], status="confirmed")
    accepted = _accept(client, test_project, plain)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["result_finding_id"] == existing
    db_session.expire_all()
    assert db_session.query(Finding).filter_by(project_id=test_project.id).count() == 1
    assert db_session.get(EvidenceRecord, evidence_id).finding_id == existing
    # A test's result confirms on join (the click's ``on_join="confirm"``).
    assert db_session.get(Finding, existing).status == "confirmed"


def test_an_accepted_proposal_citing_an_unpromoted_observation_promotes_it_with_the_text(
    client, db_session, test_project,
):
    host = _host(db_session, test_project, "10.81.2.1")
    vuln = _observation(db_session, test_project, host)
    _, evidence_id = _test_with_result(client, test_project, host, vulnerability_id=vuln.id)
    pid = _propose(client, _agent(client, test_project), [host], [evidence_id],
                   report_text={"impact": "Relay attacks."})
    accepted = _accept(client, test_project, pid)
    assert accepted.status_code == 200, accepted.text
    finding = db_session.get(Finding, accepted.json()["result_finding_id"])
    # The issue names and rates its finding, as the person's click does.
    assert (finding.source, finding.title, finding.impact) == ("scanner", "SMB Signing not required", "Relay attacks.")
    db_session.expire_all()
    assert db_session.get(EvidenceRecord, evidence_id).finding_id == finding.id
    # Promoting the observation afterwards is the same finding.
    again = client.post(f"{_p(test_project)}/vulnerabilities/{vuln.id}/promote",
                        json={"vuln_id": vuln.id, "scope": "host"})
    assert again.json()["id"] == finding.id


def test_a_proposal_citing_no_promotable_result_creates_the_finding_as_before(client, db_session, test_project):
    host = _host(db_session, test_project, "10.81.3.1")
    key = _agent(client, test_project)
    clean = client.post("/api/v1/agent/evidence", headers=key, json={
        "host_id": host.id, "tool": "curl", "outcome": "no_finding", "summary": "header present"}).json()
    pid = _propose(client, key, [host], [clean["id"]])
    accepted = _accept(client, test_project, pid)
    assert accepted.status_code == 200, accepted.text
    finding = db_session.get(Finding, accepted.json()["result_finding_id"])
    assert (finding.source, finding.title) == ("manual", "Clickjacking possible")
    db_session.expire_all()
    assert db_session.get(EvidenceRecord, clean["id"]).finding_id is None


# --- R-TR1: the finding's lock first, "before" read under it -------------------

def _committed_finding(two_sessions, project, user):
    def build(db):
        finding = Finding(project_id=project.id, title="Race", severity="high", status="confirmed",
                          source="manual", created_by_id=user.id)
        db.add(finding)
        db.flush()
        return finding.id
    return two_sessions.commit(build)


def _on_a_thread(db, fn):
    out = {}

    def run():
        try:
            fn(db)
            db.commit()
            out["error"] = None
        except BaseException as exc:  # noqa: BLE001 — handed back to the test
            db.rollback()
            out["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, out


@pytest.mark.parametrize("bulk", [False, True])
def test_a_status_change_records_the_status_it_replaced(two_sessions, bulk):
    project, user = two_sessions.project()
    finding_id = _committed_finding(two_sessions, project, user)
    a, b = two_sessions.a, two_sessions.b
    # B has read the finding (confirmed) — as a route does, before it writes.
    b_finding = b.get(Finding, finding_id)
    assert b_finding.status == "confirmed"
    # A concludes it, not yet committed.
    FindingService(a).set_status(finding=a.get(Finding, finding_id), status="accepted_risk", actor_id=user.id)
    a.flush()

    svc = FindingService(b)
    change = ((lambda db: svc.set_statuses(findings=[b_finding], status="remediated", actor_id=user.id)) if bulk
              else (lambda db: svc.set_status(finding=b_finding, status="remediated", actor_id=user.id)))
    thread, out = _on_a_thread(b, change)
    time.sleep(0.5)
    assert thread.is_alive(), "the second status change must wait for the first's lock"
    a.commit()
    thread.join(15)
    assert out["error"] is None, out["error"]
    db = two_sessions.fresh()
    try:
        moves = [(r.from_status, r.to_status) for r in db.query(FindingStatusHistory)
                 .filter_by(finding_id=finding_id).order_by(FindingStatusHistory.id)]
        assert moves == [("confirmed", "accepted_risk"), ("accepted_risk", "remediated")]
    finally:
        db.close()


def test_an_assignment_records_the_owner_it_replaced(two_sessions):
    project, user = two_sessions.project()
    finding_id = _committed_finding(two_sessions, project, user)
    a, b = two_sessions.a, two_sessions.b
    b_finding = b.get(Finding, finding_id)
    assert b_finding.owner_id is None
    # A gives it to the user, not yet committed.
    set_owner(a, a.get(Finding, finding_id), user.id, None)
    a.flush()

    # B unassigns it (the bulk assign's call), from its stale "no owner".
    thread, out = _on_a_thread(b, lambda db: assign_findings(db, None, [b_finding], None, user.id))
    time.sleep(0.5)
    assert thread.is_alive(), "the assignment must wait for the first's lock"
    a.commit()
    thread.join(15)
    assert out["error"] is None, out["error"]
    db = two_sessions.fresh()
    try:
        edits = [(r.before_value, r.after_value) for r in db.query(FindingStatusHistory)
                 .filter_by(finding_id=finding_id, field="owner").order_by(FindingStatusHistory.id)]
        # Read unlocked, B saw "no owner" already and wrote nothing.
        assert edits == [(None, "Race Tester"), ("Race Tester", None)]
        assert db.get(Finding, finding_id).owner_id is None
    finally:
        db.close()


# --- R-TR2: the bulk answers count what changed ------------------------------

def _findings(db, project, statuses):
    rows = [Finding(project_id=project.id, title=f"F{i}", severity="low", status=s, source="manual")
            for i, s in enumerate(statuses)]
    db.add_all(rows)
    db.commit()
    return [r.id for r in rows]


def test_the_bulk_status_answer_counts_only_the_findings_it_moved(client, db_session, test_project):
    ids = _findings(db_session, test_project, ["open", "confirmed", "confirmed"])
    r = client.post(f"{_p(test_project)}/findings/bulk/status",
                    json={"finding_ids": [*ids, 987654], "status": "confirmed"})
    assert r.status_code == 200, r.text
    assert r.json() == {"affected": 1, "requested": 4, "skipped_ids": [987654], "unchanged_ids": sorted(ids[1:])}


def test_the_bulk_assign_answer_counts_only_the_findings_that_changed_hands(
    client, db_session, test_project, test_user,
):
    ids = _findings(db_session, test_project, ["open", "open"])
    db_session.get(Finding, ids[0]).owner_id = test_user.id
    db_session.commit()
    r = client.post(f"{_p(test_project)}/findings/bulk/assign",
                    json={"finding_ids": ids, "assignee_user_id": test_user.id})
    assert r.status_code == 200, r.text
    assert r.json() == {"affected": 1, "requested": 2, "skipped_ids": [], "unchanged_ids": [ids[0]]}


# --- nits ----------------------------------------------------------------------

def test_a_follow_up_for_a_past_day_is_dated_that_day_in_the_policys_zone(client, db_session, test_project):
    zone = "Pacific/Kiritimati"   # UTC+14: noon UTC is 02:00 the NEXT day here
    db_session.add(RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30, days_medium=90,
                                     days_low=120, days_info=None, due_soon_days=7, time_zone=zone))
    host = _host(db_session, test_project, "10.81.9.1")
    finding = Finding(project_id=test_project.id, title="Old", severity="high", status="confirmed", source="manual")
    db_session.add(finding)
    db_session.flush()
    link = FindingHost(finding_id=finding.id, host_id=host.id, host_status="open")
    db_session.add(link)
    db_session.commit()
    local_today = remediation_policy.load(db_session).today()
    r = client.post(f"{_p(test_project)}/remediation/apply", json={"overwrite": True, "rows": [{
        "finding_host_id": link.id, "contact_email": "roger@example.com",
        "notified_on": (local_today - timedelta(days=60)).isoformat()}]})
    assert r.status_code == 200, r.text
    yesterday = local_today - timedelta(days=1)
    r = client.post(f"{_p(test_project)}/remediation/follow-up",
                    json={"contact_email": "roger@example.com", "followed_up_on": yesterday.isoformat()})
    assert r.status_code == 200, r.text
    event = db_session.query(RemediationEvent).filter_by(kind="follow_up").one()
    happened = event.occurred_at if event.occurred_at.tzinfo else event.occurred_at.replace(tzinfo=timezone.utc)
    assert happened.astimezone(remediation_policy.zone(zone)).date() == yesterday
    assert happened.astimezone(remediation_policy.zone(zone)).hour == 12


def _member(db, project, username, role):
    user = User(username=username, email=f"{username}@example.com", full_name=username.title(),
                hashed_password="x", role=UserRole.MEMBER, is_active=True,
                created_at=datetime.now(timezone.utc))
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
    db.commit()
    return user


def test_the_retest_notice_asks_only_those_who_can_record_a_result(client, db_session, test_project):
    host = _host(db_session, test_project, "10.81.8.1")
    analyst = _member(db_session, test_project, "ana-1023", "analyst")
    viewer = _member(db_session, test_project, "vic-1023", "viewer")
    made = client.post(f"{_p(test_project)}/findings",
                       json={"title": "Unowned", "severity": "high", "host_ids": [host.id]}).json()
    fh = made["hosts"][0]["id"]
    r = client.patch(f"{_p(test_project)}/findings/{made['id']}/endpoints/{fh}", json={"host_status": "retest"})
    assert r.status_code == 200, r.text
    told = {n.user_id: n.body for n in db_session.query(Notification).filter_by(type="retest")}
    assert set(told) >= {analyst.id, viewer.id}
    assert "may record the retest's result" in told[analyst.id]
    assert "await" in told[viewer.id] and "may record" not in told[viewer.id]
