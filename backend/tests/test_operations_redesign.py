"""Operations redesign (review 2026-10-02, v2.450.0).

What the page gained, each pinned where it is computed:

* (v2.451.0) the measures strip is gone — project status is Posture's; the
  terrain's sums, which Posture's sentence states, are still the lengths of
  the Hosts lists they open;
* "Findings that need me" — a finding is listed for a reason, and a
  confirmed, written-up finding is not work;
* "My work" adds up — each test counted once, no group cut by another's rows;
* "Changed since review" — the caller's own reviews only (v2.451.0), its
  count is its DSL list (``follow:revisit``), and "Still reviewed" re-stamps
  the reviewer's own review, all or nothing;
* the activity feed says what Agent Sessions says about a session.

The ``client`` fixture authenticates as ``test_user`` (id=1).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models import FollowStatus
from app.db.models_findings import Finding, FindingHost, FindingVulnerability
from app.db.models_host_tests import HostTest
from app.db.models_proposals import AgentProposal, EvidenceRecord
from app.db.models_vulnerability import (
    Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
)

_USER_ID_SEQ = [4200]


def _wb(pid, suffix=""):
    return f"/api/v1/projects/{pid}/workbench{suffix}"


def _host_ips(client, pid, q):
    r = client.get(f"/api/v1/projects/{pid}/hosts/", params={"q": q, "limit": 500})
    assert r.status_code == 200, r.text
    return {item["ip_address"] for item in r.json()["items"]}


def _user(db, username):
    from app.db.models_auth import User, UserRole
    _USER_ID_SEQ[0] += 1
    u = User(
        id=_USER_ID_SEQ[0], username=username, email=f"{username}@example.com",
        full_name=username.capitalize(), hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER, is_active=True, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(u)
    db.flush()
    return u


def _host(db, pid, ip):
    h = models.Host(project_id=pid, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _vuln(db, host_id, scan_id, severity=VulnerabilitySeverity.CRITICAL, created_at=None):
    v = Vulnerability(
        title=f"obs {uuid.uuid4().hex[:6]}", severity=severity,
        source=VulnerabilitySource.MANUAL, host_id=host_id, scan_id=scan_id,
    )
    if created_at is not None:
        v.created_at = created_at
    db.add(v)
    db.flush()
    return v


def _scan(db, pid):
    s = models.Scan(project_id=pid, filename=f"{uuid.uuid4().hex[:6]}.xml")
    db.add(s)
    db.flush()
    return s


def _test(db, pid, host_id, priority="high", assigned_to_id=None, status="proposed"):
    key = uuid.uuid4().hex
    t = HostTest(
        project_id=pid, host_id=host_id, priority=priority, tool="t", description="check",
        rationale="because", status=status, assigned_to_id=assigned_to_id, source="person",
        request_key=key, request_hash=key,
    )
    db.add(t)
    db.flush()
    return t


def _evidence(db, pid, host_id, outcome="no_finding"):
    e = EvidenceRecord(project_id=pid, host_id=host_id, tool="t", outcome=outcome, summary="ran")
    db.add(e)
    db.flush()
    return e


def _finding(db, pid, owner_id, title, status="confirmed", severity="high", text=True, **over):
    f = Finding(
        project_id=pid, title=title, severity=severity, status=status, source="manual",
        owner_id=owner_id, created_by_id=owner_id,
        description="what it is" if text else None,
        impact="why it matters" if text else None,
        recommendation="what to do" if text else None,
    )
    for k, v in over.items():
        setattr(f, k, v)
    db.add(f)
    db.flush()
    return f


def _proposal(db, pid, **over):
    p = AgentProposal(project_id=pid, kind="finding_text", status="pending", source="agent",
                      payload={"value": "x"}, field="impact")
    for k, v in over.items():
        setattr(p, k, v)
    db.add(p)
    db.flush()
    return p


# ---------------------------------------------------------------------------
# Project status left the workbench (v2.451.0); the terrain's sums still agree
# with the Hosts lists they open
# ---------------------------------------------------------------------------

def _seed_measures(db, pid, user_id):
    scan = _scan(db, pid)
    tested = _host(db, pid, "10.20.0.1")
    _evidence(db, pid, tested.id, "no_finding")
    failed_attempt = _host(db, pid, "10.20.0.2")       # a failed attempt is not a test
    _evidence(db, pid, failed_attempt.id, "failed")
    crit_untouched = [_host(db, pid, f"10.20.1.{i}") for i in (1, 2, 3)]
    for h in crit_untouched:
        _vuln(db, h.id, scan.id)
    crit_touched = _host(db, pid, "10.20.2.1")         # critical, but someone has it
    _vuln(db, crit_touched.id, scan.id)
    db.add(models.HostFollow(host_id=crit_touched.id, user_id=user_id, status=FollowStatus.IN_REVIEW))
    _host(db, pid, "10.20.3.1")                        # untouched, nothing critical
    high_only = _host(db, pid, "10.20.3.2")
    _vuln(db, high_only.id, scan.id, VulnerabilitySeverity.HIGH)
    db.commit()
    return {h.ip_address for h in crit_untouched}


def test_the_terrain_sentence_counts_exactly_the_hosts_its_lists_open(
    client, db_session, test_project, test_user,
):
    """Posture's "Where the team has been" sentence sums the terrain's blocks
    ("reached x of y, z tested; n untouched hosts carry a critical
    observation") and links to Hosts lists — each sum is its list's length.
    (Ported from the Operations measures strip, removed in v2.451.0: it stated
    the same three numbers.)"""
    pid = test_project.id
    crit_untouched = _seed_measures(db_session, pid, test_user.id)
    blocks = client.get(_wb(pid, "/terrain")).json()["blocks"]
    assert sum(b["hosts"] for b in blocks) == 8

    tested_list = _host_ips(client, pid, "has:tested")
    assert tested_list == {"10.20.0.1"}                 # a failed attempt is not a test
    assert sum(b["tested"] for b in blocks) == len(tested_list)

    untouched_critical_list = _host_ips(client, pid, "has:untouched has:critical")
    assert untouched_critical_list == crit_untouched
    assert sum(b["critical_untouched"] for b in blocks) == len(untouched_critical_list) == 3


def test_the_workbench_carries_no_project_status(client, db_session, test_project, test_user):
    """Operations is the reader's own page (v2.451.0): no ``measures`` block,
    no ``/workbench/measures`` route — project status is Posture's."""
    pid = test_project.id
    _seed_measures(db_session, pid, test_user.id)
    body = client.get(_wb(pid)).json()
    assert "measures" not in body and "measures_unavailable" not in body
    assert "my_queue" in body and "my_work" in body
    assert client.get(_wb(pid, "/measures")).status_code == 404


def test_the_agents_workbench_carries_the_same_queue_total(db_session, test_project, test_user):
    """Agent parity: the agents' read is the same service, so it carries the
    queue's total — and, like the page, no project-wide measures."""
    from app.services.workbench_service import compute_workbench

    _seed_measures(db_session, test_project.id, test_user.id)
    wb = compute_workbench(db_session, test_user, test_project, include_investigate=False)
    assert not hasattr(wb, "measures")
    assert wb.my_work.total == wb.my_work.hosts_in_review == 1


# ---------------------------------------------------------------------------
# Findings that need me
# ---------------------------------------------------------------------------

def _my_findings(client, pid):
    r = client.get(_wb(pid), params={"include_investigate": "false"})
    assert r.status_code == 200, r.text
    return r.json()["my_findings"]


def test_a_confirmed_written_up_finding_is_not_work(client, db_session, test_project, test_user):
    _finding(db_session, test_project.id, test_user.id, "Done and written", status="confirmed")
    _finding(db_session, test_project.id, test_user.id, "Fixed and written", status="remediated")
    db_session.commit()
    body = _my_findings(client, test_project.id)
    assert body["items"] == [] and body["total_open"] == 0


@pytest.mark.parametrize("status", ["open", "retest"])
def test_a_finding_under_investigation_needs_its_owner(client, db_session, test_project, test_user, status):
    f = _finding(db_session, test_project.id, test_user.id, "Still looking", status=status)
    db_session.commit()
    body = _my_findings(client, test_project.id)
    assert [i["finding_id"] for i in body["items"]] == [f.id] and body["total_open"] == 1
    assert body["items"][0]["needs"] == [{"kind": "under_investigation", "text": "under investigation"}]
    # Unwritten text on a finding the report would not include yet is not owed.
    assert body["items"][0]["missing_text"] == []


def test_a_reportable_finding_with_empty_required_text_names_the_sections(
    client, db_session, test_project, test_user,
):
    f = _finding(db_session, test_project.id, test_user.id, "Half written",
                 status="confirmed", impact="   \n\t", recommendation=None)
    db_session.commit()
    item = _my_findings(client, test_project.id)["items"][0]
    assert item["finding_id"] == f.id
    assert item["missing_text"] == ["impact", "recommendation"]
    assert item["needs"] == [
        {"kind": "missing_text", "text": "report text missing: impact, recommendation"},
    ]


def test_missing_text_is_the_report_pages_own_list(client, db_session, test_project, test_user):
    """Not re-derived: the same findings, and the same sections, as the client
    report's "Missing report text" — including the finding the report drops
    because every endpoint was judged a false positive."""
    from app.services.client_report_service import ClientReportService, missing_required_text

    pid = test_project.id
    host = _host(db_session, pid, "10.21.0.1")
    kept = _finding(db_session, pid, test_user.id, "Reportable, no impact", impact=None)
    db_session.add(FindingHost(finding_id=kept.id, host_id=host.id, host_status="open"))
    dropped = _finding(db_session, pid, test_user.id, "Every endpoint a false positive", impact=None)
    db_session.add(FindingHost(finding_id=dropped.id, host_id=host.id, host_status="false_positive"))
    no_endpoint = _finding(db_session, pid, test_user.id, "No endpoint, no text",
                           status="accepted_risk", text=False)
    _finding(db_session, pid, test_user.id, "False positive, no text", status="false_positive", text=False)
    db_session.commit()

    reported = ClientReportService(db_session)._included(pid)
    expected = {f.id: missing_required_text(f) for f in reported if missing_required_text(f)}
    assert set(expected) == {kept.id, no_endpoint.id}

    body = _my_findings(client, pid)
    assert {i["finding_id"]: i["missing_text"] for i in body["items"]} == expected
    assert body["total_open"] == len(expected)


def test_a_pending_proposal_about_my_finding_needs_me(client, db_session, test_project, test_user):
    """Both routes of ``proposal_service._about_findings``: a proposal naming
    the finding, and one on an observation that evidences it — counted as
    ``pending_per_finding`` counts them.  A decided proposal is not owed."""
    from app.services import proposal_service

    pid = test_project.id
    scan = _scan(db_session, pid)
    host = _host(db_session, pid, "10.22.0.1")
    named = _finding(db_session, pid, test_user.id, "Named by a proposal")
    _proposal(db_session, pid, finding_id=named.id)
    _proposal(db_session, pid, finding_id=named.id, field="description")
    evidenced = _finding(db_session, pid, test_user.id, "Evidenced by an observation")
    vuln = _vuln(db_session, host.id, scan.id)
    db_session.add(FindingVulnerability(finding_id=evidenced.id, vuln_id=vuln.id))
    _proposal(db_session, pid, kind="observation_dismiss", vulnerability_id=vuln.id, field=None)
    decided = _finding(db_session, pid, test_user.id, "Proposal already decided")
    _proposal(db_session, pid, finding_id=decided.id, status="accepted")
    db_session.commit()

    body = _my_findings(client, pid)
    by_id = {i["finding_id"]: i for i in body["items"]}
    assert set(by_id) == {named.id, evidenced.id}
    assert by_id[named.id]["needs"] == [{"kind": "proposals", "text": "2 proposals to decide"}]
    assert by_id[evidenced.id]["needs"] == [{"kind": "proposals", "text": "1 proposal to decide"}]
    assert {fid: i["pending_proposals"] for fid, i in by_id.items()} == (
        proposal_service.pending_per_finding(db_session, [named.id, evidenced.id, decided.id])
    )


def test_someone_elses_finding_is_not_mine_and_the_total_is_not_the_preview(
    client, db_session, test_project, test_user,
):
    other = _user(db_session, "other-owner")
    _finding(db_session, test_project.id, other.id, "Theirs", status="open")
    for i in range(18):                                 # more than the 15 the page loads
        _finding(db_session, test_project.id, test_user.id, f"Mine {i}", status="open")
    db_session.commit()
    body = _my_findings(client, test_project.id)
    assert len(body["items"]) == 15 and body["total_open"] == 18
    assert all(i["title"].startswith("Mine") for i in body["items"])


# ---------------------------------------------------------------------------
# My work adds up
# ---------------------------------------------------------------------------

def test_every_group_brings_its_own_rows_and_the_total_is_their_sum(
    client, db_session, test_project, test_user,
):
    """The reviewed state: "15 to claim" over a list with no claimable row —
    one limit over the merged list, and the tests on in-review hosts took all
    of it.  And "87 yours" over groups that added up to 62."""
    pid = test_project.id
    reviewing = [_host(db_session, pid, f"10.23.0.{i}") for i in range(1, 4)]
    for h in reviewing:
        db_session.add(models.HostFollow(host_id=h.id, user_id=test_user.id, status=FollowStatus.IN_REVIEW))
    for i in range(20):                                 # far more than one group's rows
        _test(db_session, pid, reviewing[i % 3].id, "critical")
    elsewhere = [_host(db_session, pid, f"10.23.1.{i}") for i in range(1, 5)]
    claimable = [_test(db_session, pid, h.id, "high") for h in elsewhere]
    assigned = _test(db_session, pid, elsewhere[0].id, "low", assigned_to_id=test_user.id)
    # Assigned AND on a host in review: counted once, under "assigned".
    both = _test(db_session, pid, reviewing[0].id, "low", assigned_to_id=test_user.id)
    _finding(db_session, pid, test_user.id, "Needs me", status="open")
    _finding(db_session, pid, test_user.id, "Does not", status="confirmed")
    db_session.commit()

    body = client.get(_wb(pid)).json()
    tasks = body["my_tasks"]
    assert tasks["group_counts"] == {"assigned": 2, "in_review": 20, "triage": 4}
    assert sum(tasks["group_counts"].values()) == tasks["total_open"] == 26

    # The claimable rows are ON the page, whatever the other groups hold.
    listed = {i["test_id"]: i for i in tasks["items"]}
    assert {t.id for t in claimable} <= set(listed)
    assert {assigned.id, both.id} <= set(listed)
    by_group = {"assigned": 0, "in_review": 0, "triage": 0}
    for item in tasks["items"]:
        by_group[item["reasons"][0]] += 1
    assert by_group == {"assigned": 2, "in_review": 10, "triage": 4}

    assert body["my_work"] == {
        "total": 3 + 2 + 20 + 1,
        "hosts_in_review": 3,
        "tests_assigned": 2,
        "tests_on_hosts_in_review": 20,
        "findings_needing_me": 1,
        "to_claim": 4,
    }
    # "In review" opens exactly the caller's hosts — not a teammate's.
    teammate = _user(db_session, "teammate")
    theirs = _host(db_session, pid, "10.23.9.9")
    db_session.add(models.HostFollow(host_id=theirs.id, user_id=teammate.id, status=FollowStatus.IN_REVIEW))
    db_session.commit()
    assert _host_ips(client, pid, "follow:mine") == {h.ip_address for h in reviewing}
    assert "10.23.9.9" in _host_ips(client, pid, "follow:in_review")


# ---------------------------------------------------------------------------
# Changed since review
# ---------------------------------------------------------------------------

def _review(client, pid, host, conclusion="no_issue"):
    r = client.post(
        f"/api/v1/projects/{pid}/hosts/{host.id}/follow",
        json={"status": "reviewed", "review_conclusion": conclusion},
    )
    assert r.status_code == 200, r.text


def _seed_followups(client, db, pid):
    """clean (reviewed, unchanged) · open_q (needs evidence) · ported (new open
    port) · observed (new critical) — the last two changed after the review."""
    hosts = {name: _host(db, pid, ip) for name, ip in (
        ("clean", "10.24.0.1"), ("open_q", "10.24.0.2"), ("ported", "10.24.0.3"), ("observed", "10.24.0.4"),
    )}
    db.add(models.Port(host_id=hosts["ported"].id, port_number=22, protocol="tcp", state="open",
                       first_seen=datetime.now(timezone.utc) - timedelta(days=30)))
    db.commit()
    for name, host in hosts.items():
        _review(client, pid, host, "needs_evidence" if name == "open_q" else "no_issue")
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    scan = _scan(db, pid)
    db.add(models.Port(host_id=hosts["ported"].id, port_number=8443, protocol="tcp", state="open", first_seen=later))
    db.add(models.Port(host_id=hosts["clean"].id, port_number=9000, protocol="tcp", state="closed", first_seen=later))
    _vuln(db, hosts["observed"].id, scan.id, created_at=later.replace(tzinfo=None))
    _vuln(db, hosts["clean"].id, scan.id, VulnerabilitySeverity.LOW, created_at=later.replace(tzinfo=None))
    db.commit()
    return hosts


def _teammates_followups(db, pid, mine):
    """A teammate's reviews that ARE follow-ups for the teammate, on hosts
    that are not follow-ups for the caller: their own changed host, their own
    open question, and — the case a "reviewed by me" filter over the team-wide
    predicates gets wrong — a host the CALLER reviewed cleanly (``mine``
    ["clean"]) that changed after the teammate's OLDER review."""
    mate = _user(db, "teammate-reviewer")
    long_ago = datetime.now(timezone.utc) - timedelta(days=3)
    theirs_changed = _host(db, pid, "10.24.5.1")
    theirs_open = _host(db, pid, "10.24.5.2")
    db.add(models.Port(host_id=theirs_changed.id, port_number=445, protocol="tcp", state="open",
                       first_seen=datetime.now(timezone.utc) - timedelta(days=1)))
    # After the teammate's review of "clean", before the caller's.
    db.add(models.Port(host_id=mine["clean"].id, port_number=3389, protocol="tcp", state="open",
                       first_seen=datetime.now(timezone.utc) - timedelta(days=1)))
    db.add_all([
        models.HostFollow(host_id=theirs_changed.id, user_id=mate.id, status=FollowStatus.REVIEWED,
                          review_conclusion="no_issue", reviewed_at=long_ago),
        models.HostFollow(host_id=theirs_open.id, user_id=mate.id, status=FollowStatus.REVIEWED,
                          review_conclusion="needs_evidence", reviewed_at=long_ago),
        models.HostFollow(host_id=mine["clean"].id, user_id=mate.id, status=FollowStatus.REVIEWED,
                          review_conclusion="no_issue", reviewed_at=long_ago),
        # The teammate also reviewed one of the caller's changed hosts: still
        # ONE row for the caller.
        models.HostFollow(host_id=mine["ported"].id, user_id=mate.id, status=FollowStatus.REVIEWED,
                          review_conclusion="no_issue", reviewed_at=long_ago),
    ])
    db.commit()
    return {"10.24.5.1", "10.24.5.2"}


def test_changed_since_review_lists_only_the_callers_reviews(client, db_session, test_project, test_user):
    """Operations is the reader's own page (v2.451.0): a teammate's review is
    never listed, whatever happened to its host.  It listed every reviewer's,
    marked "by you"."""
    pid = test_project.id
    hosts = _seed_followups(client, db_session, pid)
    theirs = _teammates_followups(db_session, pid, hosts)

    followups = client.get(_wb(pid)).json()["followups"]
    listed = [r["ip_address"] for r in followups["items"]]
    assert sorted(listed) == ["10.24.0.2", "10.24.0.3", "10.24.0.4"]     # one row per host
    assert not theirs & set(listed)
    assert "10.24.0.1" not in listed            # changed after THEIR review, not after mine
    assert followups["total"] == 3
    assert set(followups) == {"items", "total"}
    assert all({"mine", "reviewer", "reviewer_id"}.isdisjoint(r) for r in followups["items"])


def test_changed_since_review_count_is_its_hosts_list(client, db_session, test_project, test_user):
    """"Open all N in Hosts" opens ``follow:revisit`` — exactly the section's
    hosts.  The team-wide query it used to open is a different, larger list."""
    pid = test_project.id
    hosts = _seed_followups(client, db_session, pid)
    theirs = _teammates_followups(db_session, pid, hosts)

    followups = client.get(_wb(pid)).json()["followups"]
    mine = _host_ips(client, pid, "follow:revisit")
    assert mine == {r["ip_address"] for r in followups["items"]} == {"10.24.0.2", "10.24.0.3", "10.24.0.4"}
    assert len(mine) == followups["total"]

    team_wide = _host_ips(client, pid, "has:changed_since_review OR conclusion:needs_evidence")
    assert team_wide == mine | theirs | {"10.24.0.1"}
    # Narrowing the team-wide predicates to hosts the caller reviewed is NOT
    # the caller's list: 10.24.0.1 changed after the teammate's review only.
    assert "10.24.0.1" in _host_ips(
        client, pid, "follow:reviewed (has:changed_since_review OR conclusion:needs_evidence)",
    )

    # A review that went back In Review is not concluded: it leaves the list.
    r = client.post(f"/api/v1/projects/{pid}/hosts/{hosts['open_q'].id}/follow", json={"status": "in_review"})
    assert r.status_code == 200, r.text
    assert _host_ips(client, pid, "follow:revisit") == {"10.24.0.3", "10.24.0.4"}
    assert client.get(_wb(pid)).json()["followups"]["total"] == 2


def test_the_agents_followups_are_its_operators(db_session, client, test_project, test_user):
    """The agent read is the same service, keyed to the session's operator."""
    from app.services.workbench_service import compute_workbench

    pid = test_project.id
    hosts = _seed_followups(client, db_session, pid)
    _teammates_followups(db_session, pid, hosts)
    wb = compute_workbench(db_session, test_user, test_project, include_investigate=False)
    assert {r.ip_address for r in wb.followups.items} == {"10.24.0.2", "10.24.0.3", "10.24.0.4"}
    assert wb.followups.total == 3


def test_still_reviewed_restamps_the_review_and_keeps_the_conclusion(
    client, db_session, test_project, test_user,
):
    pid = test_project.id
    hosts = _seed_followups(client, db_session, pid)
    follow = db_session.query(models.HostFollow).filter_by(host_id=hosts["ported"].id).one()
    follow.review_summary = "looked at ssh"
    # The change is in the past, so a re-stamp to NOW is after it.
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    follow.reviewed_at = past - timedelta(hours=1)
    for port in db_session.query(models.Port).filter_by(host_id=hosts["ported"].id, port_number=8443):
        port.first_seen = past
    db_session.commit()
    assert "10.24.0.3" in _host_ips(client, pid, "has:changed_since_review")

    r = client.post(_wb(pid, "/followups/still-reviewed"), json={"host_ids": [hosts["ported"].id]})
    assert r.status_code == 200, r.text
    assert r.json() == {"host_ids": [hosts["ported"].id]}

    db_session.refresh(follow)
    assert follow.status == FollowStatus.REVIEWED
    assert follow.review_conclusion == "no_issue" and follow.review_summary == "looked at ssh"
    assert follow.reviewed_at > past
    # It has left the queue and its list — until it changes again.
    rows = client.get(_wb(pid)).json()["followups"]["items"]
    assert "10.24.0.3" not in {row["ip_address"] for row in rows}
    assert "10.24.0.3" not in _host_ips(client, pid, "has:changed_since_review")


def test_still_reviewed_is_all_or_nothing(client, db_session, test_project, test_user):
    pid = test_project.id
    hosts = _seed_followups(client, db_session, pid)
    other = _user(db_session, "their-review")
    theirs = _host(db_session, pid, "10.24.1.1")
    db_session.add(models.HostFollow(
        host_id=theirs.id, user_id=other.id, status=FollowStatus.REVIEWED,
        review_conclusion="no_issue", reviewed_at=datetime.now(timezone.utc) - timedelta(days=1),
    ))
    from app.db.models_project import Project
    elsewhere = Project(name="another engagement", slug=f"another-{uuid.uuid4().hex[:8]}")
    db_session.add(elsewhere)
    db_session.flush()
    foreign = _host(db_session, elsewhere.id, "10.24.2.1")
    db_session.add(models.HostFollow(
        host_id=foreign.id, user_id=test_user.id, status=FollowStatus.REVIEWED,
        review_conclusion="no_issue", reviewed_at=datetime.now(timezone.utc) - timedelta(days=1),
    ))
    db_session.commit()
    before = {
        f.id: f.reviewed_at for f in db_session.query(models.HostFollow).all()
    }

    # A good host beside: an open question, someone else's review, another
    # project's host, a host that does not exist.
    for bad in (hosts["open_q"].id, theirs.id, foreign.id, 999_999):
        r = client.post(_wb(pid, "/followups/still-reviewed"),
                        json={"host_ids": [hosts["ported"].id, bad]})
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["host_ids"] == [bad]
        assert "Nothing was changed" in r.json()["detail"]["message"]

    db_session.expire_all()
    assert {f.id: f.reviewed_at for f in db_session.query(models.HostFollow).all()} == before

    assert client.post(_wb(pid, "/followups/still-reviewed"), json={"host_ids": []}).status_code == 422
    assert client.post(_wb(pid, "/followups/still-reviewed"),
                       json={"host_ids": list(range(1, 202))}).status_code == 422


def test_still_reviewed_is_one_statement_for_many_hosts(client, db_session, test_project, test_user):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    pid = test_project.id
    ids = []
    for i in range(1, 41):
        h = _host(db_session, pid, f"10.25.0.{i}")
        db_session.add(models.HostFollow(
            host_id=h.id, user_id=test_user.id, status=FollowStatus.REVIEWED,
            review_conclusion="no_issue", reviewed_at=datetime.now(timezone.utc) - timedelta(days=1),
        ))
        ids.append(h.id)
    db_session.commit()

    writes: list = []

    def _seen(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE"):
            writes.append(statement)

    event.listen(Engine, "after_cursor_execute", _seen)
    try:
        r = client.post(_wb(pid, "/followups/still-reviewed"), json={"host_ids": ids})
    finally:
        event.remove(Engine, "after_cursor_execute", _seen)
    assert r.status_code == 200, r.text
    assert len(writes) == 1, writes


# ---------------------------------------------------------------------------
# The untouched queue can be paged in place
# ---------------------------------------------------------------------------

def test_the_queue_pages_without_changing_its_totals(client, db_session, test_project):
    pid = test_project.id
    scan = _scan(db_session, pid)
    for i in range(1, 8):
        _vuln(db_session, _host(db_session, pid, f"10.26.0.{i}").id, scan.id)
    db_session.commit()

    first = client.get(_wb(pid, "/investigate"), params={"limit": 3}).json()
    second = client.get(_wb(pid, "/investigate"), params={"limit": 3, "offset": 3}).json()
    rest = client.get(_wb(pid, "/investigate"), params={"limit": 3, "offset": 6}).json()
    assert [len(p["items"]) for p in (first, second, rest)] == [3, 3, 1]
    seen = [r["host_id"] for p in (first, second, rest) for r in p["items"]]
    assert len(set(seen)) == 7
    assert first["queue_total"] == second["queue_total"] == rest["queue_total"] == 7
    assert first["tier_counts"] == rest["tier_counts"]
    assert first["tier_counts"][1] == 7


# The Hosts query each of the first three tiers opens (utils/operationsQueue
# TIER_QUERY on the page).  Tiers 4 and 5 have no query — the page says so.
TIER_QUERY = {
    1: "has:untouched AND has:critical_exploit",
    2: "has:untouched AND has:critical AND NOT has:critical_exploit",
    3: "has:untouched AND has:exploit AND NOT has:critical",
}


def test_a_tiers_count_is_the_hosts_list_it_opens(client, db_session, test_project, test_user):
    pid = test_project.id
    scan = _scan(db_session, pid)

    def host_with(ip, *observations):
        h = _host(db_session, pid, ip)
        for severity, exploitable in observations:
            v = _vuln(db_session, h.id, scan.id, severity)
            v.exploitable = exploitable
        return h

    C, H, L = VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH, VulnerabilitySeverity.LOW
    host_with("10.27.1.1", (C, True))
    host_with("10.27.1.2", (C, True), (C, False))
    host_with("10.27.2.1", (C, False))
    host_with("10.27.2.2", (C, False), (L, True))       # critical beside an exploitable low: tier 2
    host_with("10.27.2.3", (C, False), (H, False))
    host_with("10.27.3.1", (H, True))
    host_with("10.27.3.2", (L, True))
    host_with("10.27.9.1", (H, False))                  # no tier
    touched = host_with("10.27.9.2", (C, True))         # tier-1 material, but someone has it
    db_session.add(models.HostFollow(host_id=touched.id, user_id=test_user.id, status=FollowStatus.IN_REVIEW))
    db_session.commit()

    counts = client.get(_wb(pid, "/investigate")).json()["tier_counts"]
    assert counts[:3] == [2, 3, 2]
    for tier, query in TIER_QUERY.items():
        rows = client.get(_wb(pid, "/investigate"), params={"tier": tier}).json()["items"]
        listed = _host_ips(client, pid, query)
        assert listed == {r["ip_address"] for r in rows}, tier
        assert len(listed) == counts[tier - 1], tier


# ---------------------------------------------------------------------------
# The activity feed says what Agent Sessions says
# ---------------------------------------------------------------------------

def test_session_key_state_is_live_only_while_a_key_is_valid():
    from app.services.agent_session_service import session_key_state

    now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    hour = timedelta(hours=1)
    assert session_key_state("active", now + hour, now + 5 * hour, now) == "live"
    assert session_key_state("active", now - hour, now + 5 * hour, now) == "resumable"
    # A revoked key (none at all) inside the lifetime is resumable too.
    assert session_key_state("active", None, now + 5 * hour, now) == "resumable"
    assert session_key_state("active", now - 2 * hour, now - hour, now) == "ended"
    assert session_key_state("active", None, None, now) == "ended"
    assert session_key_state("ended", now + hour, now + 5 * hour, now) == "ended"
    # Naive datetimes (some drivers) are read as UTC.
    assert session_key_state("active", (now + hour).replace(tzinfo=None), None, now) == "live"


def test_an_active_session_with_no_valid_key_does_not_read_active(
    client, db_session, test_project, test_agent, test_user,
):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    from app.db.models_agent import AgentSession
    from app.db.models_auth import APIKey

    now = datetime.now(timezone.utc)

    def _session(started_ago_hours, status="active"):
        s = AgentSession(
            workflow="project", project_id=test_project.id, agent_id=test_agent.id,
            started_by_id=test_user.id, status=status,
            started_at=now - timedelta(hours=started_ago_hours),
        )
        db_session.add(s)
        db_session.flush()
        return s

    def _key(session, expires_in_hours, name):
        db_session.add(APIKey(
            agent_id=test_agent.id, agent_session_id=session.id, name=name,
            key_hash=f"hash-{name}", key_prefix=f"nm_{name}"[:20], is_active=True,
            expires_at=now + timedelta(hours=expires_in_hours),
        ))

    live = _session(1)
    _key(live, 2, "live")
    lapsed = _session(2)                                # key ran out, inside its lifetime
    _key(lapsed, -1, "lapsed")
    over = _session(24 * 365)                           # active on paper, far past any lifetime
    _key(over, -24 * 300, "over")
    ended = _session(3, status="ended")
    db_session.commit()

    statements: list = []

    def _seen(conn, cursor, statement, params, context, executemany):
        if "api_keys" in statement:
            statements.append(statement)

    event.listen(Engine, "after_cursor_execute", _seen)
    try:
        r = client.get(_wb(test_project.id, "/my-activity"), params={"kinds": "session"})
    finally:
        event.remove(Engine, "after_cursor_execute", _seen)
    assert r.status_code == 200, r.text
    by_link = {e["link"]: e["summary"] for e in r.json()["items"]}
    assert by_link == {
        f"/agent-sessions/{live.id}": "Ran an agent session (live)",
        f"/agent-sessions/{lapsed.id}": "Ran an agent session (key expired — resumable)",
        f"/agent-sessions/{over.id}": "Ran an agent session (ended)",
        f"/agent-sessions/{ended.id}": "Ran an agent session (ended)",
    }
    assert not any("(active)" in s for s in by_link.values())
    # The keys of the page's sessions in ONE read — not one per row.
    assert len(statements) == 1, statements


def test_the_sessions_line_asks_for_the_readers_own_sessions(
    client, db_session, test_project, test_user, test_agent,
):
    """Operations' agent-sessions line (5.330.0) sends ``kind=project
    status=active user_id=<me>``: a teammate's live session is not in the
    answer.  It sent no ``user_id`` and counted the whole project's."""
    from app.db.models_agent import AgentSession

    mate = _user(db_session, "session-teammate")
    now = datetime.now(timezone.utc)
    mine, theirs, mine_ended = (
        AgentSession(workflow="project", project_id=test_project.id, agent_id=test_agent.id,
                     started_by_id=who, status=status, started_at=now - timedelta(hours=1))
        for who, status in ((test_user.id, "active"), (mate.id, "active"), (test_user.id, "ended"))
    )
    db_session.add_all([mine, theirs, mine_ended])
    db_session.commit()

    url = f"/api/v1/projects/{test_project.id}/agent-sessions"
    whole_project = client.get(url, params={"kind": "project", "status": "active"}).json()["sessions"]
    assert {s["id"] for s in whole_project} == {mine.id, theirs.id}

    r = client.get(url, params={"kind": "project", "status": "active", "user_id": test_user.id})
    assert r.status_code == 200, r.text
    assert [s["id"] for s in r.json()["sessions"]] == [mine.id]
