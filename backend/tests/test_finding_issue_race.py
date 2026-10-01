"""One scanner finding per issue, even when two people promote it at once
(review 2026-10-01 R8).

``promote_vulnerability`` looked the issue's finding up and inserted when
there was none.  Two promotions running together both found none and inserted
two findings; every later lookup picked one arbitrarily and the client report
listed the issue twice.  The partial unique index ``uq_finding_scanner_issue``
now refuses the second insert, which runs in a savepoint and JOINS the winner.

The first tests use the ordinary single-connection fixtures (the index, and
the join when the insert is refused).  The last two use two REAL connections
(``tests/two_connections.py``) — the only way to see a second session wait on
the first's uncommitted row.
"""
import threading
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import models
from app.db.models_findings import Finding, FindingHost, FindingVulnerability
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.finding_service import FindingService
from tests.two_connections import two_sessions  # noqa: F401  (fixture)

CVE = "CVE-2021-44228"


def _estate(db, project_id, hosts=2):
    """``hosts`` hosts, each with a scanner row for the same CVE.  Returns the
    vulnerability ids.  Commits."""
    scan = models.Scan(project_id=project_id, filename="n.nessus", tool_name="nessus", scan_type="nessus")
    db.add(scan)
    db.flush()
    ids = []
    for i in range(hosts):
        host = models.Host(project_id=project_id, ip_address=f"10.61.0.{i + 1}", state="up")
        db.add(host)
        db.flush()
        vuln = Vulnerability(
            host_id=host.id, scan_id=scan.id, cve_id=CVE, plugin_id=f"p{i}", title=f"Log4Shell ({i})",
            severity=VulnerabilitySeverity.CRITICAL, source=VulnerabilitySource.NESSUS,
        )
        db.add(vuln)
        db.flush()
        ids.append(vuln.id)
    db.commit()
    return ids


# --- the index ---------------------------------------------------------------

def test_the_database_refuses_a_second_scanner_finding_for_an_issue(db_session, test_project):
    def finding(**over):
        return Finding(**{"project_id": test_project.id, "title": "t", "severity": "high", "status": "open",
                          "source": "scanner", "dedup_key": "cve:CVE-2020-0001", **over})

    db_session.add(finding())
    db_session.flush()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(finding())
            db_session.flush()
    # What the index leaves alone: a row-keyed finding (no issue identity), a
    # finding with no key, and a non-scanner finding that happens to carry one.
    for ok in (finding(dedup_key="row:1"), finding(dedup_key="row:1"), finding(dedup_key=None),
               finding(dedup_key=None), finding(source="manual"), finding(source="manual")):
        db_session.add(ok)
        db_session.flush()
    # Another project's finding for the same issue is its own.
    from app.db.models_project import Project
    other = Project(name="other", slug="other-r8")
    db_session.add(other)
    db_session.flush()
    db_session.add(finding(project_id=other.id))
    db_session.flush()


@pytest.mark.parametrize("path", ["promote", "dismiss_on_host"])
def test_an_insert_the_index_refuses_joins_the_finding_that_won(db_session, test_project, test_user, monkeypatch, path):
    """The race, replayed on one connection: the lookup answers "none" (as it
    does for the loser of a real race), the insert is refused, and the service
    joins the winner instead of failing or forking."""
    first, second = _estate(db_session, test_project.id)
    svc = FindingService(db_session)
    winner = svc.promote_vulnerability(
        vuln=db_session.get(Vulnerability, first), project_id=test_project.id, actor_id=test_user.id,
        only_this_host=True,
    )
    db_session.commit()

    real = FindingService._scanner_finding_for
    calls = {"n": 0}

    def blind_once(self, vuln, project_id, key):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(self, vuln, project_id, key)

    monkeypatch.setattr(FindingService, "_scanner_finding_for", blind_once)
    vuln = db_session.get(Vulnerability, second)
    if path == "promote":
        joined = svc.promote_vulnerability(
            vuln=vuln, project_id=test_project.id, actor_id=test_user.id, only_this_host=True,
        )
    else:
        joined = svc.dismiss_vulnerability_on_host(
            vuln=vuln, project_id=test_project.id, actor_id=test_user.id, summary="patched here",
        )
    db_session.commit()

    assert joined.id == winner.id and calls["n"] == 2
    assert db_session.query(Finding).filter(Finding.project_id == test_project.id).count() == 1
    assert db_session.query(FindingVulnerability).filter_by(finding_id=winner.id).count() == 2
    rows = {r.host_id: r.host_status for r in db_session.query(FindingHost).filter_by(finding_id=winner.id)}
    assert len(rows) == 2
    db_session.refresh(winner)
    # The winner's status is its own: a dismissal that lost the race is about
    # ITS host's endpoint, not the issue.
    assert winner.status == "confirmed"
    if path == "dismiss_on_host":
        assert rows[vuln.host_id] == "false_positive"


# --- two real connections ----------------------------------------------------

def _findings_of(pair, project_id):
    db = pair.fresh()
    try:
        return [
            (f.id, f.status,
             db.query(FindingHost).filter_by(finding_id=f.id).count(),
             db.query(FindingVulnerability).filter_by(finding_id=f.id).count())
            for f in db.query(Finding).filter(Finding.project_id == project_id).order_by(Finding.id)
        ]
    finally:
        db.close()


def _promote(project_id, user_id, vuln_id):
    def run(db):
        vuln = db.get(Vulnerability, vuln_id)
        return FindingService(db).promote_vulnerability(
            vuln=vuln, project_id=project_id, actor_id=user_id, only_this_host=True,
        ).id
    return run


def test_the_second_of_two_promotions_waits_and_joins(two_sessions):
    """Deterministic: A promotes and does not commit; B's promotion of the
    same issue sees no finding (A's is uncommitted), so it inserts — and waits
    on A's row in the unique index.  When A commits, B's insert is refused and
    B joins A's finding.  Before the index B simply inserted a second one."""
    project, user = two_sessions.project()
    first, second = two_sessions.commit(lambda db: _estate(db, project.id))

    a_finding = _promote(project.id, user.id, first)(two_sessions.a)   # flushed, NOT committed

    outcome = {}

    def b():
        try:
            outcome["id"] = _promote(project.id, user.id, second)(two_sessions.b)
            two_sessions.b.commit()
        except BaseException as exc:  # noqa: BLE001
            two_sessions.b.rollback()
            outcome["error"] = exc

    thread = threading.Thread(target=b, daemon=True)
    thread.start()
    # B must be blocked on A's uncommitted row, not finished.
    deadline = time.time() + 10
    waiting = False
    while time.time() < deadline and not waiting and thread.is_alive():
        with two_sessions._engine.connect() as probe:
            waiting = bool(probe.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar())
        time.sleep(0.05)
    assert waiting and thread.is_alive(), f"B did not wait for A's insert: {outcome}"

    two_sessions.a.commit()
    thread.join(20)
    assert not thread.is_alive(), "B never finished after A committed"
    assert "error" not in outcome, repr(outcome.get("error"))
    assert outcome["id"] == a_finding

    assert _findings_of(two_sessions, project.id) == [(a_finding, "confirmed", 2, 2)]


def test_two_promotions_released_together_make_one_finding(two_sessions):
    """The same, undirected: both start at once, several times over.  However
    they interleave, the issue ends with one finding carrying both hosts and
    both scanner rows, and neither promotion fails."""
    project, user = two_sessions.project()
    for attempt in range(5):
        first, second = two_sessions.commit(lambda db: _estate_n(db, project.id, attempt))
        results = two_sessions.race(
            _promote(project.id, user.id, first), _promote(project.id, user.id, second),
        )
        assert not any(isinstance(r, BaseException) for r in results), results
        assert results[0] == results[1]
    assert [row[1:] for row in _findings_of(two_sessions, project.id)] == [("confirmed", 2, 2)] * 5


def _estate_n(db, project_id, n):
    """A fresh issue (its own CVE and addresses) for each attempt."""
    scan = models.Scan(project_id=project_id, filename=f"n{n}.nessus", tool_name="nessus", scan_type="nessus")
    db.add(scan)
    db.flush()
    ids = []
    for i in range(2):
        host = models.Host(project_id=project_id, ip_address=f"10.62.{n}.{i + 1}", state="up")
        db.add(host)
        db.flush()
        vuln = Vulnerability(
            host_id=host.id, scan_id=scan.id, cve_id=f"CVE-2022-{1000 + n}", plugin_id=f"q{n}-{i}",
            title=f"Issue {n} as scanner {i} words it",
            severity=VulnerabilitySeverity.HIGH, source=VulnerabilitySource.NESSUS,
        )
        db.add(vuln)
        db.flush()
        ids.append(vuln.id)
    db.commit()
    return ids
