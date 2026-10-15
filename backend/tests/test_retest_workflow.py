"""The retest workflow (2.501.0; owner, 2026-10-14).

* A retest is per HOST: the finding-level ``retest`` status is retired — a
  status write refuses it, the agents' list refuses it as a filter value.
* A host enters Retest pending by hand, or when its remediation record
  becomes "Reported fixed" (the contact's claim asks for the retest).
* Whoever must know is told: the finding's owner; with no owner, every
  project member — never the person who asked; one notification per finding
  per action, however many hosts.
* Everyone sees a pending retest: the Findings list ``host_state=retest``
  (both doors), whose total is Operations' ``retests_pending.findings``.
* A retest is closed with evidence: Retest pending → Remediated is refused
  without a result showing it fixed, recorded on that host since the retest
  was asked for; "record the retest" writes the evidence and moves the host
  in one call (fixed → Remediated, not fixed → Still present).
* "Reported fixed" with no date is dated today.
"""
from datetime import datetime, timezone

import pytest

from app.db import models
from app.db.models_findings import FindingHost
from app.db.models_project import Notification, ProjectMembership
from app.db.models_proposals import EvidenceRecord
from app.db.models_remediation import FindingHostRemediation, RemediationPolicy
from app.services import remediation_service
from tests.test_remediation_vocabulary import _finding  # noqa: F401 — shared fixture builder

TODAY = datetime.now(timezone.utc).date()


@pytest.fixture
def agent_headers(client, test_project):
    """An agent session's key on the test project (as the agents' tests start one)."""
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


def _p(project) -> str:
    return f"/api/v1/projects/{project.id}"


def _host(db, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    return host


def _finding_on(client, db_session, project, ips, **extra):
    hosts = [_host(db_session, project, ip) for ip in ips]
    db_session.commit()
    r = client.post(f"{_p(project)}/findings", json={
        "title": "SMB signing disabled", "severity": "high", "host_ids": [h.id for h in hosts], **extra,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    return body, {h["ip_address"]: h["id"] for h in body["hosts"]}


def _move(client, project, finding_id, fh_id, state):
    return client.patch(f"{_p(project)}/findings/{finding_id}/endpoints/{fh_id}", json={"host_status": state})


def _retest(client, project, finding_id, fh_id, fixed, **extra):
    return client.post(f"{_p(project)}/findings/{finding_id}/endpoints/{fh_id}/retest", json={
        "fixed": fixed, "tool": "nmap", "summary": "SMB signing now required." if fixed else "Still optional.",
        "command": "nmap --script smb2-security-mode -p445 10.9.0.1", **extra,
    })


def _member(db_session, project, user_id, username):
    from app.db.models_auth import User, UserRole
    user = User(id=user_id, username=username, email=f"{username}@example.com", full_name=username.title(),
                hashed_password="x", role=UserRole.MEMBER, is_active=True)
    db_session.add(user)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=project.id, user_id=user.id, role="analyst"))
    db_session.commit()
    return user


# --- the finding-level status is retired ------------------------------------

def test_the_finding_level_retest_status_is_refused_on_both_doors(client, db_session, test_project, agent_headers):
    finding, _ = _finding_on(client, db_session, test_project, ["10.9.0.1"])
    r = client.post(f"{_p(test_project)}/findings/{finding['id']}/status", json={"status": "retest"})
    assert r.status_code == 422, r.text
    r = client.get("/api/v1/agent/assist/findings", headers=agent_headers, params={"status": "retest"})
    assert r.status_code == 422, r.text


# --- closing a retest needs its result --------------------------------------

def test_a_pending_retest_closes_only_with_a_result_recorded_since_it_was_asked_for(client, db_session, test_project):
    finding, fh = _finding_on(client, db_session, test_project, ["10.9.0.1", "10.9.0.2"])
    one = fh["10.9.0.1"]
    # A clean result recorded BEFORE the retest was asked for does not count
    # (dated in the past: the test runs in one transaction, so ``now()``
    # would be the same instant as the request).
    db_session.add(EvidenceRecord(project_id=test_project.id, host_id=db_session.get(FindingHost, one).host_id,
                                  finding_id=finding["id"], finding_host_id=one, tool="nmap",
                                  outcome="no_finding", summary="old",
                                  created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    db_session.commit()
    assert _move(client, test_project, finding["id"], one, "retest").status_code == 200
    refused = _move(client, test_project, finding["id"], one, "remediated")
    assert refused.status_code == 409 and "A retest was asked for" in refused.text

    # Recording the retest closes it, with the evidence on the endpoint.
    r = _retest(client, test_project, finding["id"], one, fixed=True)
    assert r.status_code == 200, r.text
    assert {h["id"]: h["host_status"] for h in r.json()["hosts"]}[one] == "remediated"
    record = (db_session.query(EvidenceRecord).filter(EvidenceRecord.finding_host_id == one)
              .order_by(EvidenceRecord.id.desc()).first())
    assert record.outcome == "no_finding" and record.finding_id == finding["id"]

    # A host never sent for retest is still set Remediated freely.
    assert _move(client, test_project, finding["id"], fh["10.9.0.2"], "remediated").status_code == 200


def test_a_retest_request_stands_after_a_hand_move_to_still_present(client, db_session, test_project):
    """Second review 2026-10-14, H2 (owner Q1): Retest pending → Still present
    → Remediated walked round the guard — leaving Retest pending cleared the
    request, so the second move was free and needed no evidence.  The request
    now stands until a recorded result, or a false-positive judgement,
    closes it."""
    finding, fh = _finding_on(client, db_session, test_project, ["10.9.0.5", "10.9.0.6"])
    one, two = fh["10.9.0.5"], fh["10.9.0.6"]
    assert _move(client, test_project, finding["id"], one, "retest").status_code == 200
    assert _move(client, test_project, finding["id"], one, "open").status_code == 200       # free
    db_session.expire_all()
    assert db_session.get(FindingHost, one).retest_requested_at is not None
    refused = _move(client, test_project, finding["id"], one, "remediated")
    assert refused.status_code == 409 and "A retest was asked for" in refused.text
    # The bulk bar too.
    bulk = client.patch(f"{_p(test_project)}/findings/{finding['id']}/endpoints",
                        json={"finding_host_ids": [one], "host_status": "remediated"})
    assert bulk.status_code == 409, bulk.text
    # A recorded result closes it — here "still present" — and then the
    # team's own conclusion is free again.
    assert _move(client, test_project, finding["id"], one, "retest").status_code == 200
    assert _retest(client, test_project, finding["id"], one, fixed=False).status_code == 200
    db_session.expire_all()
    assert db_session.get(FindingHost, one).retest_requested_at is None
    # A false-positive judgement closes it too.
    assert _move(client, test_project, finding["id"], two, "retest").status_code == 200
    assert _move(client, test_project, finding["id"], two, "false_positive").status_code == 200
    db_session.expire_all()
    assert db_session.get(FindingHost, two).retest_requested_at is None


def test_a_standing_request_on_a_still_present_host_is_shown_and_answered_where_it_stands(
    client, db_session, test_project,
):
    """Walkthrough 2026-10-15: after a hand move to Still present the request
    stood (above) but the page showed nothing of it, and recording the retest
    was refused (only a Retest pending host was retested) — the refusal of
    Remediated said to record a result nobody could record.  The row now
    carries the request, the retest is recorded where the host stands, and a
    "still present" result that moves nothing still closes the request and
    is said in the history."""
    finding, fh = _finding_on(client, db_session, test_project, ["10.9.0.7", "10.9.0.8"])
    one, two = fh["10.9.0.7"], fh["10.9.0.8"]
    for row in (one, two):
        assert _move(client, test_project, finding["id"], row, "retest").status_code == 200
        assert _move(client, test_project, finding["id"], row, "open").status_code == 200
    shown = client.get(f"{_p(test_project)}/findings/{finding['id']}").json()
    assert all(h["host_status"] == "open" and h["retest_requested_at"] for h in shown["hosts"])

    # Still there: nothing moves, the request closes, the history says so.
    r = _retest(client, test_project, finding["id"], one, fixed=False)
    assert r.status_code == 200, r.text
    row = {h["id"]: h for h in r.json()["hosts"]}[one]
    assert row["host_status"] == "open" and row["retest_requested_at"] is None
    history = client.get(f"{_p(test_project)}/findings/{finding['id']}/history").json()
    lines = [h.get("summary") or "" for h in history]
    assert any("10.9.0.7" in s and "retest: still present" in s for s in lines), lines
    # And then the team's own Remediated is free again.
    assert _move(client, test_project, finding["id"], one, "remediated").status_code == 200

    # Fixed: it moves to Remediated with its evidence.
    r = _retest(client, test_project, finding["id"], two, fixed=True)
    assert r.status_code == 200, r.text
    assert {h["id"]: h["host_status"] for h in r.json()["hosts"]}[two] == "remediated"


def test_a_retest_that_finds_it_still_there_puts_the_host_back_to_still_present(client, db_session, test_project):
    finding, fh = _finding_on(client, db_session, test_project, ["10.9.0.3"])
    one = fh["10.9.0.3"]
    # Only a host awaiting a retest is retested.
    assert _retest(client, test_project, finding["id"], one, fixed=False).status_code == 409
    _move(client, test_project, finding["id"], one, "retest")
    r = _retest(client, test_project, finding["id"], one, fixed=False)
    assert r.status_code == 200, r.text
    assert r.json()["hosts"][0]["host_status"] == "open"
    assert db_session.query(EvidenceRecord).filter(EvidenceRecord.finding_host_id == one).one().outcome == "finding"


def test_the_bulk_bar_cannot_close_retests_without_their_results(client, db_session, test_project):
    finding, fh = _finding_on(client, db_session, test_project, ["10.9.0.4", "10.9.0.5"])
    ids = list(fh.values())
    assert client.patch(f"{_p(test_project)}/findings/{finding['id']}/endpoints",
                        json={"finding_host_ids": ids, "host_status": "retest"}).status_code == 200
    r = client.patch(f"{_p(test_project)}/findings/{finding['id']}/endpoints",
                     json={"finding_host_ids": ids, "host_status": "remediated"})
    assert r.status_code == 409
    db_session.expire_all()
    assert {db_session.get(FindingHost, i).host_status for i in ids} == {"retest"}     # nothing moved


# --- who is told ------------------------------------------------------------

def test_the_owner_is_told_once_per_finding_and_with_no_owner_every_member(client, db_session, test_project):
    ana = _member(db_session, test_project, 9101, "ana")
    ben = _member(db_session, test_project, 9102, "ben")
    owned, fh = _finding_on(client, db_session, test_project, ["10.9.1.1", "10.9.1.2"], owner_id=ana.id)
    client.patch(f"{_p(test_project)}/findings/{owned['id']}/endpoints",
                 json={"finding_host_ids": list(fh.values()), "host_status": "retest"})
    told = db_session.query(Notification).filter(Notification.type == "retest").all()
    assert [(n.user_id, n.finding_id) for n in told] == [(ana.id, owned["id"])]       # one, for two hosts
    assert "2 hosts await a retest" in told[0].body

    unowned, fh2 = _finding_on(client, db_session, test_project, ["10.9.1.3"])
    _move(client, test_project, unowned["id"], fh2["10.9.1.3"], "retest")
    told = {n.user_id for n in db_session.query(Notification)
            .filter(Notification.type == "retest", Notification.finding_id == unowned["id"])}
    assert {ana.id, ben.id} <= told
    # …never the person who asked (the client is the test project's admin).
    asker = client.get("/api/v1/auth/profile").json()["id"]
    assert asker not in told


def test_an_owner_who_asks_tells_nobody_and_an_owner_who_left_is_replaced_by_the_project(
        client, db_session, test_project):
    """The walkthrough's bug: the owner was the global admin who asked — no
    membership row — and was read as "no owner", so the whole project was
    told."""
    from app.db.models_findings import Finding

    ana = _member(db_session, test_project, 9201, "ana2")
    asker = client.get("/api/v1/auth/profile").json()["id"]

    def owned_by(finding_id, user_id):
        # Set directly: the route refuses an owner who is not a member, which
        # is exactly the global admin's (and the departed owner's) case.
        db_session.get(Finding, finding_id).owner_id = user_id
        db_session.commit()

    mine, fh = _finding_on(client, db_session, test_project, ["10.9.3.1"])
    owned_by(mine["id"], asker)
    _move(client, test_project, mine["id"], fh["10.9.3.1"], "retest")
    assert db_session.query(Notification).filter(Notification.finding_id == mine["id"]).count() == 0

    # An owner who is no longer a member: the project is told instead.
    gone = _member(db_session, test_project, 9202, "gone")
    db_session.query(ProjectMembership).filter_by(user_id=gone.id, project_id=test_project.id).delete()
    db_session.commit()
    theirs, fh2 = _finding_on(client, db_session, test_project, ["10.9.3.2"])
    owned_by(theirs["id"], gone.id)
    _move(client, test_project, theirs["id"], fh2["10.9.3.2"], "retest")
    told = {n.user_id for n in db_session.query(Notification).filter(Notification.finding_id == theirs["id"])}
    assert ana.id in told and gone.id not in told and asker not in told


# --- everyone sees it -------------------------------------------------------

def test_the_project_retest_list_is_the_operations_count_on_both_doors(client, db_session, test_project, agent_headers):
    a, fa = _finding_on(client, db_session, test_project, ["10.9.2.1", "10.9.2.2"])
    b, fb = _finding_on(client, db_session, test_project, ["10.9.2.3"])
    _finding_on(client, db_session, test_project, ["10.9.2.4"])
    client.patch(f"{_p(test_project)}/findings/{a['id']}/endpoints",
                 json={"finding_host_ids": list(fa.values()), "host_status": "retest"})
    _move(client, test_project, b["id"], fb["10.9.2.3"], "retest")

    page = client.get(f"{_p(test_project)}/findings", params={"host_state": "retest"}).json()
    agents = client.get("/api/v1/agent/assist/findings", headers=agent_headers, params={"host_state": "retest"}).json()
    work = client.get(f"{_p(test_project)}/workbench", params={"include_investigate": "false",
                                                                "include_rows": "false"}).json()
    assert page["total"] == agents["total"] == work["retests_pending"]["findings"] == 2
    assert work["retests_pending"]["hosts"] == 3
    assert client.get(f"{_p(test_project)}/findings", params={"host_state": "nonsense"}).status_code == 422

    # Second review 2026-10-14, F1: "hosts" are hosts — 10.9.2.1 awaiting a
    # retest on a second finding is still one host.
    host_id = db_session.get(FindingHost, fa["10.9.2.1"]).host_id
    c = client.post(f"{_p(test_project)}/findings", json={"title": "Weak TLS", "severity": "low", "host_ids": [host_id]})
    assert c.status_code == 201, c.text
    _move(client, test_project, c.json()["id"], c.json()["hosts"][0]["id"], "retest")
    work = client.get(f"{_p(test_project)}/workbench", params={"include_investigate": "false",
                                                                "include_rows": "false"}).json()
    assert work["retests_pending"] == {"findings": 3, "hosts": 3}


def test_a_retest_stands_by_one_rule_on_every_list_and_on_the_guard(
    client, db_session, test_project, agent_headers,
):
    """Third review 2026-10-15, R1: the guard read "Retest pending OR a
    request kept", every list and count "Retest pending" only — a request
    kept after a hand move to Still present was on no list, and a finding
    judged a false positive kept its hosts on the retest list.  One rule now
    (``finding_needs.retest_stands``): a request recorded, the finding not
    judged a false positive."""
    kept, fk = _finding_on(client, db_session, test_project, ["10.9.4.1"])
    judged, fj = _finding_on(client, db_session, test_project, ["10.9.4.2"])
    one, two = fk["10.9.4.1"], fj["10.9.4.2"]
    _move(client, test_project, kept["id"], one, "retest")
    _move(client, test_project, kept["id"], one, "open")                       # the request is kept
    _move(client, test_project, judged["id"], two, "retest")
    r = client.post(f"{_p(test_project)}/findings/{judged['id']}/status", json={"status": "false_positive"})
    assert r.status_code == 200, r.text

    def listed():
        page = client.get(f"{_p(test_project)}/findings", params={"host_state": "retest"}).json()
        agents = client.get("/api/v1/agent/assist/findings", headers=agent_headers,
                            params={"host_state": "retest"}).json()
        work = client.get(f"{_p(test_project)}/workbench", params={"include_investigate": "false",
                                                                    "include_rows": "false"}).json()
        ids = sorted(f["id"] for f in page["items"])
        assert work["retests_pending"]["findings"] == page["total"] == agents["total"] == len(ids)
        return ids

    assert listed() == [kept["id"]]                       # the kept request is listed; the judged one is not
    rows = {h["id"]: h for h in client.get(f"{_p(test_project)}/findings/{kept['id']}").json()["hosts"]}
    assert rows[one]["host_status"] == "open" and rows[one]["retest_stands"] is True
    rows = {h["id"]: h for h in client.get(f"{_p(test_project)}/findings/{judged['id']}").json()["hosts"]}
    assert rows[two]["host_status"] == "retest" and rows[two]["retest_stands"] is False
    # The agents' finding read says the same, by the same rule.
    for fid, row, stands in ((kept["id"], one, True), (judged["id"], two, False)):
        read = client.get(f"/api/v1/agent/assist/findings/{fid}", headers=agent_headers).json()
        assert {h["finding_host_id"]: h["retest_stands"] for h in read["hosts"]}[row] is stands
    # The guard reads the same rule: no defect is concluded on a finding
    # judged a false positive, so nothing holds its host.
    assert _move(client, test_project, judged["id"], two, "remediated").status_code == 200
    assert _move(client, test_project, kept["id"], one, "remediated").status_code == 409


def test_an_undo_of_a_removal_puts_the_retest_back_and_the_guard_holds(client, db_session, test_project):
    """Third review 2026-10-15, H1: the removal's snapshot dropped the
    request, so remove → Undo → Remediated was accepted with no result."""
    finding, fh = _finding_on(client, db_session, test_project, ["10.9.5.1", "10.9.5.2"])
    one = fh["10.9.5.1"]
    _move(client, test_project, finding["id"], one, "retest")
    _move(client, test_project, finding["id"], one, "open")                    # the request is kept
    row = {h["id"]: h for h in client.get(f"{_p(test_project)}/findings/{finding['id']}").json()["hosts"]}[one]
    assert row["retest_requested_at"]
    r = client.delete(f"{_p(test_project)}/findings/{finding['id']}/endpoints/{one}")
    assert r.status_code == 200, r.text
    # The Undo sends back what the page held of the row.
    r = client.post(f"{_p(test_project)}/findings/{finding['id']}/hosts", json={"host_ids": [], "endpoints": [{
        "host_id": row["host_id"], "name_id": None, "host_status": "open",
        "retest_requested_at": row["retest_requested_at"],
    }]})
    assert r.status_code == 200, r.text
    restored = next(h for h in r.json()["hosts"] if h["host_id"] == row["host_id"])
    assert restored["retest_stands"] is True
    assert _move(client, test_project, finding["id"], restored["id"], "remediated").status_code == 409
    # Restoring straight to Remediated is the same move, held the same way.
    assert client.delete(f"{_p(test_project)}/findings/{finding['id']}/endpoints/{restored['id']}").status_code == 200
    r = client.post(f"{_p(test_project)}/findings/{finding['id']}/hosts", json={"host_ids": [], "endpoints": [{
        "host_id": row["host_id"], "host_status": "remediated", "retest_requested_at": row["retest_requested_at"],
    }]})
    assert r.status_code == 409, r.text


def test_the_endpoint_writers_lock_the_rows_they_move(client, db_session, test_project):
    """Third review 2026-10-15, S1: the picker's and the bulk bar's moves read
    the row unlocked (``record_retest`` locks it), so a move decided on a row
    a concurrent retest result had just changed left it stale."""
    from sqlalchemy import event

    finding, fh = _finding_on(client, db_session, test_project, ["10.9.6.1", "10.9.6.2"])
    seen = []

    def watch(conn, cursor, statement, params, context, executemany):
        if "FROM finding_hosts" in statement and statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", watch)
    try:
        assert _move(client, test_project, finding["id"], fh["10.9.6.1"], "retest").status_code == 200
        assert client.patch(f"{_p(test_project)}/findings/{finding['id']}/endpoints", json={
            "finding_host_ids": list(fh.values()), "host_status": "open"}).status_code == 200
    finally:
        event.remove(bind, "before_cursor_execute", watch)
    assert sum("FOR UPDATE" in s for s in seen) >= 2, seen


# --- "Reported fixed" asks for the retest ------------------------------------

@pytest.fixture
def tracking(db_session):
    db_session.add(RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30, days_medium=90,
                                     days_low=120, days_info=None, due_soon_days=7))
    db_session.commit()


def test_reported_fixed_puts_a_still_present_host_into_retest_and_is_dated_today(
        client, db_session, test_project, tracking):
    still, done = _finding(db_session, test_project, ["open", "remediated"])
    rows = [{"finding_host_id": still, "status": "closed"}, {"finding_host_id": done, "status": "closed"}]
    dry = client.post(f"{_p(test_project)}/remediation/apply", json={"rows": rows, "dry_run": True})
    assert dry.status_code == 200, dry.text
    assert dry.json()["summary"]["retests_requested"] == 1
    db_session.expire_all()
    assert db_session.get(FindingHost, still).host_status == "open"                  # a dry run writes nothing

    r = client.post(f"{_p(test_project)}/remediation/apply", json={"rows": rows})
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["retests_requested"] == 1
    db_session.expire_all()
    assert db_session.get(FindingHost, still).host_status == "retest"
    assert db_session.get(FindingHost, done).host_status == "remediated"             # left as it was
    record = db_session.query(FindingHostRemediation).filter_by(finding_host_id=still).one()
    assert record.status == "closed" and record.closed_on is not None
    assert record.closed_on == remediation_service.deadlines.load(db_session).today()
