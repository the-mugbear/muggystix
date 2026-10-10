"""Operations as tabs (v2.452.0): one list on screen, complete and paged.

Each tab's COUNT comes from the light ``GET /workbench?include_rows=false``;
its ROWS come from a paged route built on the same function.  What is pinned
here, for every tab, with more rows than one page (25) holds:

* count == the number of rows the tab pages through, and every page reports
  the same whole-list total;
* the pages are disjoint and complete (a total order — no row twice, none
  skipped);
* where a Hosts query lists the same hosts (``follow:mine``,
  ``follow:revisit``), it lists exactly them;
* the light call carries the same counts as the full one, no rows, and fewer
  statements.

The ``client`` fixture authenticates as ``test_user`` (id=1).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models import FollowStatus
from app.db.models_findings import Finding
from app.db.models_host_tests import HostTest
from app.db.models_vulnerability import (
    Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
)

PAGE = 25


def _wb(pid, suffix=""):
    return f"/api/v1/projects/{pid}/workbench{suffix}"


def _counts(client, pid):
    r = client.get(_wb(pid), params={"include_rows": "false", "include_investigate": "false"})
    assert r.status_code == 200, r.text
    return r.json()


def _pages(client, pid, suffix, items_of=lambda body: body["items"], **params):
    """Every page of a tab's list, 25 at a time, until an empty one."""
    pages = []
    offset = 0
    while True:
        r = client.get(_wb(pid, suffix), params={"limit": PAGE, "offset": offset, **params})
        assert r.status_code == 200, r.text
        body = r.json()
        pages.append(body)
        if not items_of(body):
            return pages
        offset += PAGE
        assert offset < 1000, "the list never ended"


def _host(db, pid, ip):
    h = models.Host(project_id=pid, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _host_ids(client, pid, q):
    r = client.get(f"/api/v1/projects/{pid}/hosts/", params={"q": q, "limit": 500})
    assert r.status_code == 200, r.text
    return {item["id"] for item in r.json()["items"]}


def _test(db, pid, host_id, priority="high", assigned_to_id=None):
    key = uuid.uuid4().hex
    t = HostTest(
        project_id=pid, host_id=host_id, priority=priority, tool="nmap", description="check",
        rationale="because", status="proposed", assigned_to_id=assigned_to_id, source="person",
        request_key=key, request_hash=key,
    )
    db.add(t)
    db.flush()
    return t


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

def test_findings_tab_count_is_the_list_it_pages_through(client, db_session, test_project, test_user):
    pid = test_project.id
    severities = ("low", "critical", "high", "medium")
    for i in range(31):
        db_session.add(Finding(
            project_id=pid, title=f"Needs me {i}", severity=severities[i % 4], status="open",
            source="manual", owner_id=test_user.id, created_by_id=test_user.id,
        ))
    # Confirmed and written up: a state, not work — in neither count nor list.
    db_session.add(Finding(
        project_id=pid, title="Done", severity="critical", status="confirmed", source="manual",
        owner_id=test_user.id, created_by_id=test_user.id,
        description="d", impact="i", recommendation="r",
    ))
    db_session.commit()

    counts = _counts(client, pid)
    assert counts["my_work"]["findings_needing_me"] == counts["my_findings"]["total_open"] == 31
    assert counts["my_findings"]["items"] == []

    pages = _pages(client, pid, "/findings")
    assert [len(p["items"]) for p in pages] == [25, 6, 0]
    assert {p["total_open"] for p in pages} == {31}       # the page past the end too
    listed = [i["finding_id"] for p in pages for i in p["items"]]
    assert len(set(listed)) == 31
    assert all(i["needs"] for p in pages for i in p["items"])
    # Severity first, across the page boundary.
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    order = [rank[i["severity"]] for p in pages for i in p["items"]]
    assert order == sorted(order)


def test_findings_to_decide_and_to_write_are_the_lists_they_page_through(
    client, db_session, test_project, test_user,
):
    """v2.453.0 — the lead says the two kinds of work apart; each number is
    the list ``need=`` returns, and together they are the tab."""
    from app.db.models_proposals import AgentProposal

    pid = test_project.id

    def finding(title, status, **text):
        f = Finding(
            project_id=pid, title=title, severity="high", status=status, source="manual",
            owner_id=test_user.id, created_by_id=test_user.id, **text,
        )
        db_session.add(f)
        db_session.flush()
        return f

    for i in range(27):                                   # more than a page
        finding(f"Investigating {i}", "open")
    for i in range(29):                                   # more than a page
        finding(f"Unwritten {i}", "confirmed")
    # Unwritten AND a proposal waiting: a decision comes first — counted once.
    both = finding("Unwritten with a draft", "confirmed")
    db_session.add(AgentProposal(
        project_id=pid, kind="finding_text", status="pending", source="agent", finding_id=both.id,
        field="description", payload={"value": "x"},
    ))
    finding("Done", "confirmed", description="d", impact="i", recommendation="r")
    db_session.commit()

    counts = _counts(client, pid)["my_work"]
    assert (counts["findings_to_decide"], counts["findings_to_write"]) == (28, 29)
    assert counts["findings_needing_me"] == 57

    decide = _pages(client, pid, "/findings", need="decide")
    write = _pages(client, pid, "/findings", need="write")
    assert [len(p["items"]) for p in decide] == [25, 3, 0]
    assert [len(p["items"]) for p in write] == [25, 4, 0]
    # Every page says the same whole-list figures, the one past the end too.
    for page in decide + write:
        assert page["need_counts"] == {"decide": 28, "write": 29} and page["total_open"] == 57
    decide_ids = {i["finding_id"] for p in decide for i in p["items"]}
    write_ids = {i["finding_id"] for p in write for i in p["items"]}
    assert both.id in decide_ids and not (decide_ids & write_ids)
    assert len(decide_ids | write_ids) == 57
    for p in write:
        assert all([n["kind"] for n in i["needs"]] == ["missing_text"] for i in p["items"])
    assert client.get(_wb(pid, "/findings"), params={"need": "other"}).status_code == 422


# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------

def test_hosts_tab_count_is_the_list_it_pages_through(client, db_session, test_project, test_user):
    pid = test_project.id
    now = datetime.now(timezone.utc)
    mine = [_host(db_session, pid, f"10.40.0.{i}") for i in range(1, 29)]
    for i, h in enumerate(mine):
        db_session.add(models.HostFollow(
            host_id=h.id, user_id=test_user.id, status=FollowStatus.IN_REVIEW,
            # Several share a timestamp: the order must still be total.
            updated_at=now - timedelta(minutes=i // 4),
        ))
    reviewed = _host(db_session, pid, "10.40.1.1")          # finished: not in review
    db_session.add(models.HostFollow(
        host_id=reviewed.id, user_id=test_user.id, status=FollowStatus.REVIEWED,
        review_conclusion="no_issue", reviewed_at=now,
    ))
    db_session.add(models.Port(host_id=mine[0].id, port_number=443, protocol="tcp", state="open"))
    db_session.commit()

    counts = _counts(client, pid)
    assert counts["my_work"]["hosts_in_review"] == counts["my_queue"]["in_review_count"] == 28
    assert counts["my_queue"]["items"] == []

    pages = _pages(client, pid, "/hosts")
    assert [len(p["items"]) for p in pages] == [25, 3, 0]
    assert {p["in_review_count"] for p in pages} == {28}
    listed = [i["host_id"] for p in pages for i in p["items"]]
    assert len(set(listed)) == 28
    # The tab's "Open in Hosts" link lists exactly these.
    assert set(listed) == _host_ids(client, pid, "follow:mine") == {h.id for h in mine}
    by_id = {i["host_id"]: i for p in pages for i in p["items"]}
    assert by_id[mine[0].id]["open_port_count"] == 1


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_tests_tab_counts_are_the_lists_it_pages_through(client, db_session, test_project, test_user):
    pid = test_project.id
    reviewing = [_host(db_session, pid, f"10.41.0.{i}") for i in range(1, 4)]
    for h in reviewing:
        db_session.add(models.HostFollow(host_id=h.id, user_id=test_user.id, status=FollowStatus.IN_REVIEW))
    elsewhere = [_host(db_session, pid, f"10.41.1.{i}") for i in range(1, 4)]
    priorities = ("low", "critical", "medium", "high")
    assigned = [_test(db_session, pid, elsewhere[i % 3].id, priorities[i % 4], assigned_to_id=test_user.id)
                for i in range(27)]
    # Assigned AND on a host in review: once, under "assigned".
    assigned.append(_test(db_session, pid, reviewing[0].id, "low", assigned_to_id=test_user.id))
    on_review = [_test(db_session, pid, reviewing[i % 3].id, priorities[i % 4]) for i in range(26)]
    claimable = [_test(db_session, pid, elsewhere[i % 3].id, "critical" if i % 2 else "high") for i in range(30)]
    _test(db_session, pid, elsewhere[0].id, "low")           # unassigned, low: nobody's list
    db_session.commit()

    counts = _counts(client, pid)
    work = counts["my_work"]
    assert (work["tests_assigned"], work["tests_on_hosts_in_review"], work["to_claim"]) == (28, 26, 30)
    assert counts["my_tasks"]["items"] == []
    assert counts["my_tasks"]["group_counts"] == {"assigned": 28, "in_review": 26, "triage": 30}

    # Every kind together: mine + free to claim.
    pages = _pages(client, pid, "/tests")
    listed = [i["test_id"] for p in pages for i in p["items"]]
    assert [len(p["items"]) for p in pages] == [25, 25, 25, 9, 0]
    assert len(set(listed)) == len(listed) == 84
    assert len(listed) == work["tests_assigned"] + work["tests_on_hosts_in_review"] + work["to_claim"]
    assert {p["total_open"] for p in pages} == {84}
    assert {tuple(sorted(p["group_counts"].items())) for p in pages} == {
        (("assigned", 28), ("in_review", 26), ("triage", 30)),
    }
    # Assigned first, then on a host in review, then free to claim.
    kinds = [i["reasons"][0] for p in pages for i in p["items"]]
    assert kinds == ["assigned"] * 28 + ["in_review"] * 26 + ["triage"] * 30
    assert all(i["tool"] == "nmap" for p in pages for i in p["items"])

    # One kind: its own count, its own tests, paged.
    for kind, expected in (("assigned", assigned), ("in_review", on_review), ("triage", claimable)):
        kind_pages = _pages(client, pid, "/tests", kind=kind)
        ids = [i["test_id"] for p in kind_pages for i in p["items"]]
        assert len(ids) == len(set(ids)) == counts["my_tasks"]["group_counts"][kind]
        assert set(ids) == {t.id for t in expected}
        # The counts stay whole-list under a kind filter.
        assert {p["total_open"] for p in kind_pages} == {84}
        # By priority within the kind, across the page boundary.
        rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        order = [rank[i["priority"]] for p in kind_pages for i in p["items"]]
        assert order == sorted(order)

    assert client.get(_wb(pid, "/tests"), params={"kind": "everyone"}).status_code == 422


def test_the_workbench_preview_is_unchanged_by_paging(client, db_session, test_project, test_user):
    """The agents' read keeps its previews: per group, the top rows."""
    pid = test_project.id
    host = _host(db_session, pid, "10.41.5.1")
    for _ in range(12):
        _test(db_session, pid, host.id, "high")
    for _ in range(3):
        _test(db_session, pid, host.id, "low", assigned_to_id=test_user.id)
    db_session.commit()
    tasks = client.get(_wb(pid), params={"include_investigate": "false"}).json()["my_tasks"]
    by_kind = {"assigned": 0, "in_review": 0, "triage": 0}
    for item in tasks["items"]:
        by_kind[item["reasons"][0]] += 1
    assert by_kind == {"assigned": 3, "in_review": 0, "triage": 10}
    assert tasks["group_counts"] == {"assigned": 3, "in_review": 0, "triage": 12}


# ---------------------------------------------------------------------------
# Changed since review
# ---------------------------------------------------------------------------

def test_changed_tab_count_is_the_list_it_pages_through(client, db_session, test_project, test_user):
    pid = test_project.id
    now = datetime.now(timezone.utc)
    open_q = [_host(db_session, pid, f"10.42.0.{i}") for i in range(1, 28)]
    for i, h in enumerate(open_q):
        db_session.add(models.HostFollow(
            host_id=h.id, user_id=test_user.id, status=FollowStatus.REVIEWED,
            review_conclusion="needs_evidence", reviewed_at=now - timedelta(hours=i // 5),
        ))
    clean = _host(db_session, pid, "10.42.1.1")              # reviewed, nothing changed
    db_session.add(models.HostFollow(
        host_id=clean.id, user_id=test_user.id, status=FollowStatus.REVIEWED,
        review_conclusion="no_issue", reviewed_at=now,
    ))
    db_session.commit()

    counts = _counts(client, pid)
    assert counts["followups"]["total"] == 27 and counts["followups"]["items"] == []
    assert counts["followups_unavailable"] is False

    pages = _pages(client, pid, "/followups")
    assert [len(p["items"]) for p in pages] == [25, 2, 0]
    assert {p["total"] for p in pages} == {27}
    listed = [i["host_id"] for p in pages for i in p["items"]]
    assert len(set(listed)) == 27
    assert set(listed) == _host_ids(client, pid, "follow:revisit") == {h.id for h in open_q}


def test_the_changed_list_says_when_it_failed(client, test_project, monkeypatch):
    from app.api.v1.endpoints import workbench as workbench_module

    def _boom(*args, **kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(workbench_module, "compute_review_followups", _boom)
    r = client.get(_wb(test_project.id, "/followups"))
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Pick up
# ---------------------------------------------------------------------------

def test_pick_up_tab_count_is_the_list_it_pages_through(client, db_session, test_project):
    pid = test_project.id
    scan = models.Scan(project_id=pid, filename="tabs.xml")
    db_session.add(scan)
    db_session.flush()
    for i in range(1, 32):
        h = _host(db_session, pid, f"10.43.0.{i}")
        db_session.add(Vulnerability(
            title=f"obs {i}", severity=VulnerabilitySeverity.CRITICAL,
            source=VulnerabilitySource.MANUAL, host_id=h.id, scan_id=scan.id,
        ))
    _host(db_session, pid, "10.43.1.1")                      # untouched, no reason: not in the queue
    db_session.commit()

    # The tab's count is one light request: a single row, whole-queue totals.
    count = client.get(_wb(pid, "/investigate"), params={"limit": 1}).json()
    assert count["queue_total"] == 31 and count["untouched_total"] == 32

    pages = _pages(client, pid, "/investigate")
    assert [len(p["items"]) for p in pages] == [25, 6, 0]
    assert {p["queue_total"] for p in pages} == {31}
    assert len({i["host_id"] for p in pages for i in p["items"]}) == 31
    # One tier: its chip's count is the list it pages through.
    tier_pages = _pages(client, pid, "/investigate", tier=2)
    assert sum(len(p["items"]) for p in tier_pages) == count["tier_counts"][1] == 31


# ---------------------------------------------------------------------------
# The light call
# ---------------------------------------------------------------------------

def test_the_light_call_has_the_full_calls_counts_and_fewer_statements(
    client, db_session, test_project, test_user,
):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    pid = test_project.id
    host = _host(db_session, pid, "10.44.0.1")
    db_session.add(models.HostFollow(host_id=host.id, user_id=test_user.id, status=FollowStatus.IN_REVIEW))
    reviewed = _host(db_session, pid, "10.44.0.2")
    db_session.add(models.HostFollow(
        host_id=reviewed.id, user_id=test_user.id, status=FollowStatus.REVIEWED,
        review_conclusion="needs_evidence", reviewed_at=datetime.now(timezone.utc),
    ))
    _test(db_session, pid, host.id, "high")
    _test(db_session, pid, reviewed.id, "critical")
    db_session.add(Finding(project_id=pid, title="needs me", severity="high", status="open",
                           source="manual", owner_id=test_user.id, created_by_id=test_user.id))
    db_session.add(models.Annotation(host_id=host.id, user_id=test_user.id, body="a note"))
    db_session.commit()

    def _run(params):
        seen: list = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            seen.append(" ".join(statement.split())[:110])

        event.listen(Engine, "after_cursor_execute", _count)
        try:
            r = client.get(_wb(pid), params=params)
            assert r.status_code == 200, r.text
        finally:
            event.remove(Engine, "after_cursor_execute", _count)
        return r.json(), seen

    full, full_statements = _run({"include_investigate": "false"})
    light, light_statements = _run({"include_investigate": "false", "include_rows": "false"})

    assert light["my_work"] == full["my_work"] == {
        "total": 3, "hosts_in_review": 1, "tests_assigned": 0,
        "tests_on_hosts_in_review": 1, "findings_needing_me": 1,
        "findings_to_decide": 1, "findings_to_write": 0, "to_claim": 1,
    }
    assert light["followups"]["total"] == full["followups"]["total"] == 1
    assert light["my_tasks"]["group_counts"] == full["my_tasks"]["group_counts"]
    assert light["blockers"] == full["blockers"]
    for section in ("my_queue", "my_tasks", "my_findings", "followups", "recent_notes"):
        assert light[section]["items"] == [], section
        assert full[section]["items"], section
    # A regression guard, not a target: 13 measured for the light call at
    # v2.452.0 (the full one, without the queue, 23).  Bound = measured + 2.
    # v2.476.0 — 15 measured: one more for ``setup`` (has hosts / has a scope
    # entry), which replaced the page's whole scope-coverage request; and the
    # light call here is now the caller's SECOND read (the full one above
    # started the cursor — its upsert is counted in ``test_workbench.py``), so
    # it is no longer answered as a first visit: the since-last-visit window
    # counts changed hosts apart from new ones, and this fixture opens a
    # SAVEPOINT after the first read's commit.
    assert len(light_statements) < len(full_statements)
    assert len(light_statements) <= 17, "\n".join(light_statements)


@pytest.mark.parametrize("suffix", ["/findings", "/hosts", "/tests", "/followups"])
def test_a_tabs_list_is_bounded_by_its_limit(client, test_project, suffix):
    assert client.get(_wb(test_project.id, suffix), params={"limit": 101}).status_code == 422
    assert client.get(_wb(test_project.id, suffix), params={"offset": -1}).status_code == 422
    assert client.get(_wb(test_project.id, suffix)).status_code == 200
