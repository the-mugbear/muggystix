"""Second review of 2026-10-23, findings / triage / remediation (TRI-1 … TRI-10).

* TRI-1 — a result's promotion decided ``created`` from a lookup made before
  the promotion; a concurrent promotion of the same issue on another host made
  it read "made by this call" while it joined, so an accepted ``finding_create``
  with report text skipped its 409 and replaced the joined finding's text.
* TRI-2 / TRI-3 — every finding lock is ``FindingService.lock_findings`` (one
  order, columns re-read); assign-from-report locks its whole set once.
* TRI-4 — deleting a finding leaves its children to the database's cascade.
* TRI-5 — the removed endpoint's retest request is typed columns.
* TRI-6 / TRI-8 — notification fan-out reads membership once.
* TRI-7 — one announcement of a note.
* TRI-9 — one status-line writer; TRI-10 — the nits.
"""
import threading
import time
import uuid

from sqlalchemy import text

from app.db import models
from app.db.models_findings import Finding
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.agent_evidence_service import promote_evidence
from app.services.finding_service import FindingService
from tests.two_connections import two_sessions  # noqa: F401  (fixture)


def _blocked(pair, thread, timeout=10):
    """True once some statement of this database waits on a lock."""
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


def _on_a_thread(db, fn):
    out = {}

    def run():
        try:
            out["result"] = fn(db)
            db.commit()
            out["error"] = None
        except BaseException as exc:  # noqa: BLE001 — handed back to the test
            db.rollback()
            out["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, out


# --- TRI-1 -------------------------------------------------------------------

def test_a_result_promotion_that_joins_a_concurrent_promotion_says_it_joined(two_sessions):
    """A promotes issue X on h2 (its finding inserted, not committed); B
    promotes a test's result for X on h1.  B's insert waits on the issue's
    unique index and joins A's finding once A commits — and must say so, or a
    cited ``finding_create`` with report text replaces A's text, no 409."""
    project, user = two_sessions.project(role="analyst")

    def build(db):
        scan = models.Scan(project_id=project.id, filename="n.nessus", tool_name="nessus", scan_type="nessus")
        h1 = models.Host(project_id=project.id, ip_address="10.91.0.1", state="up")
        h2 = models.Host(project_id=project.id, ip_address="10.91.0.2", state="up")
        db.add_all([scan, h1, h2])
        db.flush()
        v1, v2 = (Vulnerability(host_id=h.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                                severity=VulnerabilitySeverity.MEDIUM, title="SMB Signing not required")
                  for h in (h1, h2))
        db.add_all([v1, v2])
        db.flush()
        test = HostTest(
            project_id=project.id, host_id=h1.id, tool="nmap", description="d", rationale="r",
            source="person", created_by_user_id=user.id, request_key=str(uuid.uuid4()),
            request_hash="h", issue_key=v1.issue_key, issue_title=v1.title,
        )
        db.add(test)
        db.flush()
        record = EvidenceRecord(
            project_id=project.id, host_id=h1.id, host_test_id=test.id, tool="nmap",
            outcome="finding", summary="shown", recorded_by_user_id=user.id,
        )
        db.add(record)
        db.flush()
        return v2.id, h2.id, record.id

    v2_id, h2_id, evidence_id = two_sessions.commit(build)
    a, b = two_sessions.a, two_sessions.b
    winner, created = FindingService(a).promote_vulnerability(
        vuln=a.get(Vulnerability, v2_id), project_id=project.id, actor_id=user.id, host_ids=[h2_id],
    )
    assert created
    a.flush()
    winner_id = winner.id

    thread, out = _on_a_thread(b, lambda db: promote_evidence(
        db, project.id, evidence_id, title=None, severity=None, status="confirmed", actor_id=user.id,
    ))
    assert _blocked(two_sessions, thread), "B's insert must wait on the issue's unique index"
    a.commit()
    thread.join(15)
    assert out["error"] is None, out["error"]
    made = out["result"]
    assert made.finding.id == winner_id
    assert made.created is False, "B joined A's finding; it did not make it"
    db = two_sessions.fresh()
    try:
        assert db.query(Finding).filter_by(project_id=project.id).count() == 1
    finally:
        db.close()


# --- TRI-2 -------------------------------------------------------------------

def test_assign_from_report_takes_its_finding_locks_in_id_order_across_batches(two_sessions):
    """501 targets: the high-id finding's 500 endpoints fill the first batch,
    the low-id finding's one endpoint the second.  B holds the low finding,
    then wants the high one (an id-ordered writer).  Batch by batch, A held
    the high finding while it waited for the low one — a deadlock.  Locked
    whole first, A waits holding nothing and B goes through."""
    from datetime import datetime, timezone

    from app.db.models_findings import FindingHost
    from app.db.models_reports import Report
    from app.schemas.remediation_schemas import AssignFromReportBody
    from app.services import remediation_service

    project, user = two_sessions.project()

    def build(db):
        hosts = [models.Host(project_id=project.id, ip_address=f"10.92.{i // 250}.{i % 250 + 1}", state="up")
                 for i in range(501)]
        low = Finding(project_id=project.id, title="Low", severity="high", status="confirmed",
                      source="manual", created_by_id=user.id)
        high = Finding(project_id=project.id, title="High", severity="high", status="confirmed",
                       source="manual", created_by_id=user.id)
        db.add_all([*hosts, low])
        db.flush()
        db.add(high)
        db.flush()
        # The high finding's endpoints first: lower finding_host ids.
        db.add_all([FindingHost(finding_id=high.id, host_id=h.id, host_status="open") for h in hosts[1:]])
        db.flush()
        db.add(FindingHost(finding_id=low.id, host_id=hosts[0].id, host_status="open"))
        db.flush()
        reported = {
            str(high.id): {"endpoints": {f"{h.id}:": {} for h in hosts[1:]}},
            str(low.id): {"endpoints": {f"{hosts[0].id}:": {}}},
        }
        report = Report(project_id=project.id, kind="full", status="issued", title="Issued", template="pentest",
                        number=1, issued_at=datetime.now(timezone.utc), snapshot={"reported": reported})
        db.add(report)
        db.flush()
        return low.id, high.id, report.id

    low_id, high_id, report_id = two_sessions.commit(build)
    assert low_id < high_id
    a, b = two_sessions.a, two_sessions.b
    FindingService(b).lock_finding_ids([low_id])

    thread, out = _on_a_thread(a, lambda db: remediation_service.assign_from_report(
        db, project.id, AssignFromReportBody(report_id=report_id), remediation_service._Person(user.id)))
    assert _blocked(two_sessions, thread), "A must wait for the low finding B holds"
    # B, an id-ordered writer, goes on to the high finding.
    FindingService(b).lock_finding_ids([high_id])
    b.commit()
    thread.join(30)
    assert not thread.is_alive()
    assert out["error"] is None, out["error"]
    assert out["result"]["assigned"] == 501


# --- TRI-3 -------------------------------------------------------------------

def test_reported_fixed_reads_the_finding_status_under_its_lock(two_sessions):
    """``apply`` loads its endpoints (and their finding) before it locks.  B
    judges the finding a false positive and commits while A waits for the
    lock: A must see that, and ask for no retest of a concluded finding."""
    from app.db.models_findings import FindingHost
    from app.schemas.remediation_schemas import ApplyBody, ApplyRow
    from app.services import remediation_service

    project, user = two_sessions.project()

    def build(db):
        host = models.Host(project_id=project.id, ip_address="10.93.0.1", state="up")
        finding = Finding(project_id=project.id, title="Judged", severity="high", status="confirmed",
                          source="manual", created_by_id=user.id)
        db.add_all([host, finding])
        db.flush()
        fh = FindingHost(finding_id=finding.id, host_id=host.id, host_status="open")
        db.add(fh)
        db.flush()
        return finding.id, fh.id

    finding_id, fh_id = two_sessions.commit(build)
    a, b = two_sessions.a, two_sessions.b
    FindingService(b).set_status(finding=b.get(Finding, finding_id), status="false_positive", actor_id=user.id)
    b.flush()

    body = ApplyBody(rows=[ApplyRow(finding_host_id=fh_id, status="closed")])
    thread, out = _on_a_thread(a, lambda db: remediation_service.apply(
        db, project.id, body, remediation_service._Person(user.id)))
    assert _blocked(two_sessions, thread), "apply must wait for the finding's lock"
    b.commit()
    thread.join(15)
    assert out["error"] is None, out["error"]
    assert out["result"]["summary"]["retests_requested"] == 0
    db = two_sessions.fresh()
    try:
        assert db.get(FindingHost, fh_id).host_status == "open"
    finally:
        db.close()


# --- TRI-4 -------------------------------------------------------------------

def test_deleting_a_finding_leaves_its_children_to_the_database(client, db_session, test_project):
    """No endpoint, history line or evidence link is loaded or deleted row by
    row: one DELETE of the finding, and the cascade removes the rest."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    from app.db.models_findings import FindingHost, FindingStatusHistory

    hosts = [models.Host(project_id=test_project.id, ip_address=f"10.94.0.{i + 1}", state="up") for i in range(30)]
    finding = Finding(project_id=test_project.id, title="Widespread", severity="high", status="confirmed",
                      source="manual")
    db_session.add_all([*hosts, finding])
    db_session.flush()
    db_session.add_all([FindingHost(finding_id=finding.id, host_id=h.id, host_status="open") for h in hosts])
    db_session.add_all([FindingStatusHistory(finding_id=finding.id, from_status=None, to_status="confirmed")
                        for _ in range(5)])
    db_session.commit()
    finding_id = finding.id

    seen = []

    def _record(conn, cursor, statement, *args):
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "after_cursor_execute", _record)
    try:
        r = client.delete(f"/api/v1/projects/{test_project.id}/findings/{finding_id}")
    finally:
        event.remove(Engine, "after_cursor_execute", _record)
    assert r.status_code == 204, r.text
    assert not [s for s in seen if s.startswith(("DELETE FROM finding_hosts", "DELETE FROM finding_status_history",
                                                 "DELETE FROM finding_vulnerabilities"))]
    assert not [s for s in seen if s.startswith("SELECT") and "finding_hosts.host_status" in s]
    db_session.expire_all()
    assert db_session.query(FindingHost).filter_by(finding_id=finding_id).count() == 0
    assert db_session.query(FindingStatusHistory).filter_by(finding_id=finding_id).count() == 0


# --- TRI-6 / TRI-10 (writers) ---------------------------------------------------

def _person(db, project, username, role, global_role=None):
    from datetime import datetime, timezone

    from app.db.models_auth import User, UserRole
    from app.db.models_project import ProjectMembership

    user = User(username=username, email=f"{username}@example.com", full_name=username.title(),
                hashed_password="x", role=global_role or UserRole.MEMBER, is_active=True,
                created_at=datetime.now(timezone.utc))
    db.add(user)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=role))
    db.flush()
    return user


def _retests(db, project, owners):
    """One finding per owner, each with one endpoint (host loaded)."""
    from app.db.models_findings import FindingHost

    pending = []
    for i, owner in enumerate(owners):
        host = models.Host(project_id=project.id, ip_address=f"10.95.{len(owners)}.{i + 1}", state="up")
        finding = Finding(project_id=project.id, title=f"F{i}", severity="high", status="confirmed",
                          source="manual", owner_id=owner.id)
        db.add_all([host, finding])
        db.flush()
        fh = FindingHost(finding_id=finding.id, host=host, host_status="open")
        db.add(fh)
        db.flush()
        pending.append((finding, [fh]))
    return pending


def _statements(fn):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    seen = []

    def _count(conn, cursor, statement, *_args):
        seen.append(" ".join(statement.split())[:120])

    event.listen(Engine, "after_cursor_execute", _count)
    try:
        fn()
    finally:
        event.remove(Engine, "after_cursor_execute", _count)
    return seen


def test_the_retest_fan_out_reads_membership_once_whatever_the_findings(db_session, test_project, test_user):
    from app.db.models_project import Notification
    from app.services.notification_service import NotificationService

    owners = [_person(db_session, test_project, f"own-tri6-{i}", "analyst") for i in range(6)]
    two, six = _retests(db_session, test_project, owners[:2]), _retests(db_session, test_project, owners)
    svc = NotificationService(db_session)
    few = _statements(lambda: svc.notify_retests_requested(two, test_user.id))
    many = _statements(lambda: svc.notify_retests_requested(six, test_user.id))
    assert len(few) == len(many), (few, many)
    db_session.flush()
    told = [n.user_id for n in db_session.query(Notification).filter_by(type="retest")]
    assert sorted(told) == sorted([o.id for o in owners[:2]] + [o.id for o in owners])


def test_a_bulk_status_change_costs_the_same_whatever_the_findings(db_session, test_project, test_user):
    """TRI-9: one history line per finding, written by one flush — the
    recorder flushed each line (5,000 round trips at the bulk cap)."""
    from app.db.models_findings import FindingStatusHistory

    def findings(n):
        made = [Finding(project_id=test_project.id, title=f"B{i}", severity="low", status="open", source="manual")
                for i in range(n)]
        db_session.add_all(made)
        db_session.flush()
        return made

    two, six = findings(2), findings(6)
    svc = FindingService(db_session)
    few = _statements(lambda: svc.set_statuses(findings=two, status="confirmed", actor_id=test_user.id))
    many = _statements(lambda: svc.set_statuses(findings=six, status="confirmed", actor_id=test_user.id))
    assert len(few) == len(many), (few, many)
    lines = db_session.query(FindingStatusHistory).filter(
        FindingStatusHistory.finding_id.in_([f.id for f in two + six])).all()
    assert sorted((r.from_status, r.to_status) for r in lines) == [("open", "confirmed")] * 8


def test_a_global_admin_told_of_a_retest_is_told_they_may_record_it(db_session, test_project, test_user):
    from app.db.models_auth import UserRole
    from app.db.models_project import Notification
    from app.services.notification_service import NotificationService

    admin = _person(db_session, test_project, "gadm-tri10", "viewer", global_role=UserRole.ADMIN)
    viewer = _person(db_session, test_project, "view-tri10", "viewer")
    pending = _retests(db_session, test_project, [admin, viewer])
    NotificationService(db_session).notify_retests_requested(pending, test_user.id)
    db_session.flush()
    told = {n.user_id: n.body for n in db_session.query(Notification).filter_by(type="retest")}
    assert "may record the retest's result" in told[admin.id]
    assert "may record" not in told[viewer.id]
