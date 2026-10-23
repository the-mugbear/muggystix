"""Review 2026-10-22, A-R1 — every write to an existing finding takes the
finding's row lock FIRST (``FindingService.lock_finding``).

Before it, a promotion joining an existing finding read the finding and its
endpoints without a lock:

* a test result's "confirm on join" decided on a status read before a person
  concluded the finding, and wrote ``confirmed`` over the conclusion;
* two promotions attaching overlapping hosts each read the attached set, then
  inserted the same endpoint — the second failed on the unique index (a 500),
  or deadlocked when they inserted in different orders.

Two REAL connections (``tests/two_connections.py``): the ordinary fixtures bind
every session to one connection, where nothing ever waits.  Each test makes
the first writer hold its transaction open, starts the second on a thread,
checks that it waits, then commits the first.
"""
import threading
import time

from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.services.finding_service import FindingService
from tests.two_connections import two_sessions  # noqa: F401  (fixture)


def _seed(two_sessions, project, user, n_hosts):
    """A scan, ``n_hosts`` hosts each with a row of one issue, and that issue's
    finding (open) attached to the first host only."""
    def build(db):
        scan = models.Scan(project_id=project.id, filename="race.nessus", scan_type="nessus", tool_name="nessus")
        db.add(scan)
        db.flush()
        hosts, vulns = [], []
        for i in range(n_hosts):
            h = models.Host(project_id=project.id, ip_address=f"10.77.0.{i + 1}", state="up")
            db.add(h)
            db.flush()
            v = Vulnerability(host_id=h.id, scan_id=scan.id, title="Anonymous FTP enabled",
                              severity=VulnerabilitySeverity.HIGH, source="nessus",
                              plugin_id="10079", check_id="ftp_anonymous")
            db.add(v)
            db.flush()
            hosts.append(h.id)
            vulns.append(v.id)
        first = db.get(Vulnerability, vulns[0])
        finding, created = FindingService(db).promote_vulnerability(
            vuln=first, project_id=project.id, actor_id=user.id, status="open", host_ids=[hosts[0]],
        )
        assert created
        return finding.id, hosts, vulns
    return two_sessions.commit(build)


def _second_writer(db, fn):
    """Run ``fn(db)`` on a thread, committing on success; the outcome (None
    or the exception) is in the returned dict once the thread is joined."""
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


def test_a_test_result_joining_never_overwrites_a_conclusion_made_meanwhile(two_sessions):
    project, user = two_sessions.project()
    finding_id, hosts, vulns = _seed(two_sessions, project, user, n_hosts=1)

    # A person concludes the finding (the PATCH's write), not yet committed.
    a = two_sessions.a
    FindingService(a).set_status(finding=a.get(Finding, finding_id), status="accepted_risk", actor_id=user.id)
    a.flush()

    # A test's result confirms the issue on its host: on_join="confirm".
    def confirm(db):
        FindingService(db).promote_vulnerability(
            vuln=db.get(Vulnerability, vulns[0]), project_id=project.id, actor_id=user.id,
            status="confirmed", host_ids=[hosts[0]], on_join="confirm",
        )

    thread, out = _second_writer(two_sessions.b, confirm)
    time.sleep(1.0)
    assert thread.is_alive(), "the joining write must wait for the finding's lock"
    a.commit()
    thread.join(30)
    assert not thread.is_alive()
    assert out["error"] is None, out["error"]

    db = two_sessions.fresh()
    try:
        assert db.get(Finding, finding_id).status == "accepted_risk"
    finally:
        db.close()


def test_two_promotions_attaching_the_same_hosts_in_opposite_orders_both_succeed(two_sessions):
    project, user = two_sessions.project()
    finding_id, hosts, vulns = _seed(two_sessions, project, user, n_hosts=3)
    _, h1, h2 = hosts

    a = two_sessions.a
    FindingService(a).promote_vulnerability(
        vuln=a.get(Vulnerability, vulns[1]), project_id=project.id, actor_id=user.id,
        host_ids=[h1, h2], on_join="keep",
    )
    a.flush()

    def other_order(db):
        FindingService(db).promote_vulnerability(
            vuln=db.get(Vulnerability, vulns[2]), project_id=project.id, actor_id=user.id,
            host_ids=[h2, h1], on_join="keep",
        )

    thread, out = _second_writer(two_sessions.b, other_order)
    time.sleep(1.0)
    assert thread.is_alive(), "the second promotion must wait for the finding's lock"
    a.commit()
    thread.join(30)
    assert not thread.is_alive(), "deadlock"
    assert out["error"] is None, out["error"]

    db = two_sessions.fresh()
    try:
        attached = [hid for (hid,) in db.query(FindingHost.host_id).filter(FindingHost.finding_id == finding_id)]
        assert sorted(attached) == sorted(hosts)
    finally:
        db.close()
