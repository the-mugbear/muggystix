"""Tests for the Operations workbench (refactor P2).

Covers the batched ``GET /workbench`` composition and the durable
``POST /workbench/seen`` cursor that drives "since your last visit".

The ``client`` fixture authenticates as ``test_user`` (id=1).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models_vulnerability import (
    Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
)


def _url(pid, suffix=""):
    return f"/api/v1/projects/{pid}/workbench{suffix}"


def _make_host(db_session, project_id, ip, first_seen=None):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    if first_seen is not None:
        h.first_seen = first_seen
    db_session.add(h)
    db_session.flush()
    return h


def _make_scan(db_session, project_id, filename, created_at=None):
    s = models.Scan(project_id=project_id, filename=filename)
    if created_at is not None:
        s.created_at = created_at
    db_session.add(s)
    db_session.flush()
    return s


def _make_vuln(db_session, host_id, scan_id, severity, created_at=None):
    v = Vulnerability(
        title="finding",
        severity=severity,
        source=VulnerabilitySource.MANUAL,
        host_id=host_id,
        scan_id=scan_id,
    )
    if created_at is not None:
        v.created_at = created_at
    db_session.add(v)
    db_session.flush()
    return v


def test_workbench_query_count_is_bounded(client, db_session, test_project):
    """Review #6 — regression guard / query-count tracing: one /workbench
    request must stay well under a fan-out bound (it composes several
    aggregations on one connection).  Seed a little data so every section
    runs its real query."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    scan = models.Scan(project_id=test_project.id, filename="qc.xml")
    host = _make_host(db_session, test_project.id, "10.9.9.1")
    db_session.add(scan)
    db_session.flush()
    _make_vuln(db_session, host.id, scan.id, VulnerabilitySeverity.CRITICAL)
    # A reviewed host, so the review follow-ups run all three of their
    # statements rather than returning after the first.
    # (Changed since its review: a port first seen after it — the one thing
    # that lists a finished review.)
    reviewed = _make_host(db_session, test_project.id, "10.9.9.2")
    db_session.add(models.HostFollow(
        host_id=reviewed.id, user_id=1, status=models.FollowStatus.REVIEWED,
        reviewed_at=datetime.now(timezone.utc) - timedelta(days=1),
    ))
    db_session.add(models.Port(host_id=reviewed.id, port_number=8443, protocol="tcp", state="open",
                               first_seen=datetime.now(timezone.utc)))
    # v2.450.0 — a finding that needs its owner, and a test to claim, so "My
    # work" runs its per-row lookups (hosts, pending proposals) too.
    from app.db.models_findings import Finding, FindingHost
    from app.db.models_host_tests import HostTest
    finding = Finding(project_id=test_project.id, title="needs me", severity="high",
                      status="open", source="manual", owner_id=1, created_by_id=1)
    db_session.add(finding)
    db_session.flush()
    # On the reviewed host: the first one stays untouched, so the queue runs.
    db_session.add(FindingHost(finding_id=finding.id, host_id=reviewed.id))
    db_session.add(HostTest(
        project_id=test_project.id, host_id=reviewed.id, priority="high", tool="t",
        description="d", rationale="r", status="proposed", source="person",
        request_key="qc-1", request_hash="qc-1",
    ))
    db_session.flush()

    counter = {"n": 0}
    statements: list = []

    def _count(conn, cursor, statement, params, context, executemany):
        counter["n"] += 1
        # Kept for the failure message: a tripped bound should say WHICH
        # statements ran, not just how many.
        statements.append(" ".join(statement.split())[:110])

    event.listen(Engine, "after_cursor_execute", _count)
    try:
        r = client.get(_url(test_project.id))
        assert r.status_code == 200, r.text
    finally:
        event.remove(Engine, "after_cursor_execute", _count)

    # Bound is a regression guard, not a target — 27 measured at v2.442.0
    # (bound 30; it was 31 while the interrupted-runs statement existed).
    # How it got there: ~17 for the
    # personal sections, plus five grouped statements for the investigation
    # queue (v2.347.0: untouched hosts, vulns, high-value ports, conflicts,
    # sources; the changed-since-scan window runs only when a tier-4
    # candidate exists), plus three for the review follow-ups (v2.359.0:
    # reviewed follows, ports first seen after the review, critical/high
    # observations recorded after it), plus three more in v2.363.0 (hosts
    # changed in the since-last-visit window; blocked imports; interrupted
    # execution runs — each ONE grouped statement; the interrupted-runs one
    # went with execution runs in v2.442.0).  Flag a fan-out blow-up
    # (e.g. an N+1 creeping into a section), not a fixed additive cost.
    # v2.450.0 — 28 measured: two for the measures strip (hosts + tested in
    # one, untouched-with-a-critical in the other) and one for the pending
    # proposals of the findings shown; "Findings that need me" takes its total
    # as a window, so its old count statement went.
    # v2.451.0 — 26 measured: the measures strip's two statements went with
    # it (project status is Posture's); the bound follows, so a new N+1 of two
    # statements is still caught.
    # v2.451.1 — 24 measured: the team roster's two statements (its distinct
    # count and its rows) went with ``team_review``.
    # v2.452.0 — still 24 measured (bound = measured + 2): paging the lists
    # added no statement to this call.  The page's own light call
    # (``include_rows=false``) is bounded in ``test_operations_tabs.py``.
    # v2.476.0 — 27 measured here, 25 on every read after a person's first:
    # one statement for ``setup`` (has hosts / has a scope entry — it replaced
    # the page's whole scope-coverage request), and, on the FIRST read only
    # (this one: the project has no cursor yet), the cursor's upsert and this
    # fixture's RELEASE SAVEPOINT for its commit — the write the page used to
    # send as a second request.  Bound = measured + 2.
    assert counter["n"] <= 29, (
        f"workbench issued {counter['n']} SQL statements:\n" + "\n".join(statements)
    )


def test_workbench_returns_all_sections(client, test_project):
    r = client.get(_url(test_project.id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"my_queue", "my_tasks", "my_findings", "followups", "since_last_visit"}
    # Empty project — sections render their zero states.
    assert body["my_queue"]["items"] == []
    assert body["my_tasks"]["items"] == []


def test_first_visit_marks_everything_new(client, db_session, test_project):
    scan = _make_scan(db_session, test_project.id, "first.xml")
    host = _make_host(db_session, test_project.id, "10.9.0.1")
    _make_vuln(db_session, host.id, scan.id, VulnerabilitySeverity.CRITICAL)

    r = client.get(_url(test_project.id))
    body = r.json()["since_last_visit"]
    assert body["is_first_visit"] is True
    assert body["last_viewed_at"] is None
    assert body["new_scan_count"] == 1
    assert body["latest_scan_filename"] == "first.xml"
    assert body["new_host_count"] == 1
    assert body["new_critical_findings"] == 1


def test_seen_then_nothing_new(client, db_session, test_project):
    scan = _make_scan(db_session, test_project.id, "seeded.xml")
    host = _make_host(db_session, test_project.id, "10.9.1.1")
    _make_vuln(db_session, host.id, scan.id, VulnerabilitySeverity.HIGH)

    seen = client.post(_url(test_project.id, "/seen"))
    assert seen.status_code == 200, seen.text
    assert seen.json()["last_viewed_at"] is not None

    body = client.get(_url(test_project.id)).json()["since_last_visit"]
    assert body["is_first_visit"] is False
    # All seeded data predates the cursor → nothing new.
    assert body["new_scan_count"] == 0
    assert body["new_host_count"] == 0
    assert body["new_high_findings"] == 0


def _rewind_cursor(db_session, project_id, hours=2):
    """Put the caller's last visit in the PAST and return it.  The counts are a
    (last_viewed, as_of] window since v2.363.0, so test data belongs between
    the cursor and now — not in the future, where the old open-ended count
    found it and a real snapshot never would."""
    cursor = db_session.query(models.OperationsCursor).filter(
        models.OperationsCursor.project_id == project_id).one()
    cursor.last_viewed_at = datetime.now(timezone.utc) - timedelta(hours=hours)
    db_session.commit()
    return cursor.last_viewed_at


def test_changes_after_seen_are_reported(client, db_session, test_project):
    # Seed + mark seen, two hours ago.
    client.post(_url(test_project.id, "/seen"))
    after = _rewind_cursor(db_session, test_project.id) + timedelta(hours=1)

    scan = _make_scan(db_session, test_project.id, "fresh.xml", created_at=after)
    host = _make_host(db_session, test_project.id, "10.9.2.1", first_seen=after)
    # Vulnerability.created_at is naive — strip tzinfo for an apples-to-apples
    # comparison against a naive column.
    _make_vuln(db_session, host.id, scan.id, VulnerabilitySeverity.CRITICAL,
               created_at=after.replace(tzinfo=None))

    body = client.get(_url(test_project.id)).json()["since_last_visit"]
    assert body["is_first_visit"] is False
    assert body["new_scan_count"] == 1
    assert body["latest_scan_filename"] == "fresh.xml"
    assert body["new_host_count"] == 1
    assert body["new_critical_findings"] == 1


def test_seen_acknowledges_the_displayed_snapshot_not_the_click(client, db_session, test_project):
    """A scan that lands between the summary loading and the operator clicking
    Acknowledge was never shown to them — it must resurface, not be swallowed
    by a cursor stamped at click time."""
    client.post(_url(test_project.id, "/seen"))
    snapshot = client.get(_url(test_project.id)).json()["since_last_visit"]
    assert snapshot["as_of"] is not None
    as_of = datetime.fromisoformat(snapshot["as_of"])

    # Arrives after the snapshot was taken, before the acknowledgement.
    _make_scan(db_session, test_project.id, "late.xml", created_at=as_of + timedelta(seconds=1))

    seen = client.post(_url(test_project.id, "/seen"), json={"as_of": snapshot["as_of"]})
    assert seen.status_code == 200, seen.text
    assert datetime.fromisoformat(seen.json()["last_viewed_at"]) == as_of

    body = client.get(_url(test_project.id)).json()["since_last_visit"]
    assert body["new_scan_count"] == 1
    assert body["latest_scan_filename"] == "late.xml"


def test_seen_never_moves_past_now(client, test_project):
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    seen = client.post(_url(test_project.id, "/seen"), json={"as_of": future})
    assert seen.status_code == 200, seen.text
    assert datetime.fromisoformat(seen.json()["last_viewed_at"]) <= datetime.now(timezone.utc)


def test_failed_investigation_queue_is_reported_unavailable(client, test_project, monkeypatch):
    """An empty queue reads as "every host has been touched by someone"; a
    queue that could not be computed must say so instead."""
    from app.services import workbench_service as workbench  # the composition (v2.428.0)

    ok = client.get(_url(test_project.id)).json()
    assert ok["investigate_unavailable"] is False

    def _boom(*args, **kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr(workbench, "compute_investigation_queue", _boom)
    r = client.get(_url(test_project.id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["investigate_unavailable"] is True
    assert body["investigate"]["items"] == []
    # The personal sections still came back.
    assert "my_queue" in body and "since_last_visit" in body


def _review(client, project, host, note=None):
    """Mark the host Reviewed as the page does — the status and nothing else —
    and, when given, write the optional note with its own call."""
    url = f"/api/v1/projects/{project.id}/hosts/{host.id}/follow"
    r = client.post(url, json={"status": "reviewed"})
    assert r.status_code == 200, r.text
    if note is not None:
        r = client.patch(url, json={"review_summary": note})
        assert r.status_code == 200, r.text
    return r


def test_review_followups_resurface_changes_and_nothing_else(client, db_session, test_project):
    """A reviewed host left every queue for good.  ONE kind is not done: a
    host that changed after it was reviewed.  A clean, unchanged review stays
    out — and so does a review that an older version concluded "needs more
    evidence": that half is gone (a review records no conclusion; a host that
    needs more evidence stays In Review)."""
    clean = _make_host(db_session, test_project.id, "10.8.0.1")
    open_q = _make_host(db_session, test_project.id, "10.8.0.2")
    changed = _make_host(db_session, test_project.id, "10.8.0.3")
    db_session.add(models.Port(host_id=changed.id, port_number=22, protocol="tcp", state="open",
                               first_seen=datetime.now(timezone.utc) - timedelta(days=30)))
    db_session.commit()

    _review(client, test_project, clean)
    _review(client, test_project, open_q)
    _review(client, test_project, changed, note="looked at ssh")
    # What a row written before conclusions were retired looks like.
    legacy = db_session.query(models.HostFollow).filter_by(host_id=open_q.id).one()
    legacy.review_conclusion = "needs_evidence"
    legacy.review_summary = "waiting on the creds test"
    db_session.commit()

    # After the review: a new open port and a new critical observation.
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    scan = _make_scan(db_session, test_project.id, "after.xml")
    db_session.add(models.Port(host_id=changed.id, port_number=8443, protocol="tcp", state="open", first_seen=later))
    db_session.add(models.Port(host_id=changed.id, port_number=9000, protocol="tcp", state="closed", first_seen=later))
    _make_vuln(db_session, changed.id, scan.id, VulnerabilitySeverity.CRITICAL, created_at=later.replace(tzinfo=None))
    db_session.commit()

    body = client.get(_url(test_project.id)).json()
    assert body["followups_unavailable"] is False
    rows = {r["ip_address"]: r for r in body["followups"]["items"]}
    assert set(rows) == {"10.8.0.3"}
    assert body["followups"]["total"] == 1
    # The row carries the reviewer's note, and no conclusion field at all.
    assert rows["10.8.0.3"]["review_summary"] == "looked at ssh"
    assert "review_conclusion" not in rows["10.8.0.3"]

    kinds = {r["kind"]: r["text"] for r in rows["10.8.0.3"]["reasons"]}
    assert set(kinds) == {"new_ports", "new_vulns"}
    # The port open before the review and the closed one are not "new open ports".
    assert "1 open port" in kinds["new_ports"] and "8443" in kinds["new_ports"]
    assert "22" not in kinds["new_ports"] and "9000" not in kinds["new_ports"]
    assert "1 critical" in kinds["new_vulns"]


def test_viewing_a_reviewed_host_does_not_reset_changed_since_review(client, db_session, test_project):
    """``updated_at`` is bumped by every write to the follow row, including the
    view timestamp — measured against it, the queue reset whenever somebody
    looked.  ``reviewed_at`` only moves with the review itself."""
    host = _make_host(db_session, test_project.id, "10.8.1.1")
    db_session.commit()
    _review(client, test_project, host)
    follow = db_session.query(models.HostFollow).filter_by(host_id=host.id).one()
    reviewed_at = follow.reviewed_at
    assert reviewed_at is not None

    later = datetime.now(timezone.utc) + timedelta(hours=1)
    db_session.add(models.Port(host_id=host.id, port_number=3389, protocol="tcp", state="open", first_seen=later))
    db_session.commit()

    assert client.post(f"/api/v1/projects/{test_project.id}/hosts/{host.id}/view").status_code in (200, 204)
    db_session.refresh(follow)
    assert follow.reviewed_at == reviewed_at
    assert [r["ip_address"] for r in client.get(_url(test_project.id)).json()["followups"]["items"]] == ["10.8.1.1"]

    # Re-opening the review is the way out: it leaves the follow-ups and the
    # baseline is cleared with the conclusion.
    client.post(f"/api/v1/projects/{test_project.id}/hosts/{host.id}/follow", json={"status": "in_review"})
    db_session.refresh(follow)
    assert follow.reviewed_at is None and follow.review_conclusion is None
    assert client.get(_url(test_project.id)).json()["followups"]["items"] == []


def test_failed_followups_are_reported_unavailable(client, test_project, monkeypatch):
    from app.services import workbench_service as workbench  # the composition (v2.428.0)

    def _boom(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(workbench, "compute_review_followups", _boom)
    body = client.get(_url(test_project.id)).json()
    assert body["followups_unavailable"] is True and body["followups"]["items"] == []


def test_seen_is_idempotent_one_row(client, db_session, test_project):
    from app.db.models import OperationsCursor
    client.post(_url(test_project.id, "/seen"))
    client.post(_url(test_project.id, "/seen"))
    rows = (
        db_session.query(OperationsCursor)
        .filter(OperationsCursor.project_id == test_project.id)
        .all()
    )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# v2.476.0 — the first read by a PERSON starts the cursor (it was a second
# request the page fired from an effect).  Only the first: after that the
# cursor moves when they acknowledge, so a glance — or the page's once-a-minute
# re-read of its counts — never swallows changes nobody looked at.
# ---------------------------------------------------------------------------

def _cursor(db_session, project_id):
    db_session.expire_all()
    return db_session.query(models.OperationsCursor).filter(
        models.OperationsCursor.project_id == project_id).first()


def test_the_first_read_starts_the_cursor_and_is_still_answered_as_a_first_visit(
    client, db_session, test_project,
):
    _make_scan(db_session, test_project.id, "before.xml")
    _make_host(db_session, test_project.id, "10.9.7.1")
    assert _cursor(db_session, test_project.id) is None

    first = client.get(_url(test_project.id), params={"include_rows": "false"})
    assert first.status_code == 200, first.text
    since = first.json()["since_last_visit"]
    # Answered against the cursor as it WAS: no baseline, everything is new.
    assert since["is_first_visit"] is True and since["last_viewed_at"] is None
    assert since["new_scan_count"] == 1 and since["new_host_count"] == 1

    # …and the baseline is the moment those counts were taken, with no POST.
    cursor = _cursor(db_session, test_project.id)
    assert cursor is not None, "the first read left no cursor"
    assert cursor.last_viewed_at == datetime.fromisoformat(since["as_of"])

    # What arrives afterwards is new against that baseline.
    _make_scan(db_session, test_project.id, "after.xml",
               created_at=cursor.last_viewed_at + timedelta(seconds=1))
    again = client.get(_url(test_project.id)).json()["since_last_visit"]
    assert again["is_first_visit"] is False
    assert again["new_scan_count"] == 1 and again["latest_scan_filename"] == "after.xml"
    assert again["new_host_count"] == 0


def test_a_later_read_never_moves_the_cursor(client, db_session, test_project):
    """Reading is not acknowledging: once there is a baseline, the same change
    is reported on every read until ``POST /workbench/seen``."""
    client.get(_url(test_project.id))
    rewound = _rewind_cursor(db_session, test_project.id)
    _make_scan(db_session, test_project.id, "unseen.xml", created_at=rewound + timedelta(hours=1))

    for _ in range(3):
        since = client.get(_url(test_project.id), params={"include_rows": "false"}).json()["since_last_visit"]
        assert since["is_first_visit"] is False
        assert since["new_scan_count"] == 1
    assert _cursor(db_session, test_project.id).last_viewed_at == rewound

    # Acknowledging is still what moves it.
    client.post(_url(test_project.id, "/seen"), json={"as_of": since["as_of"]})
    assert client.get(_url(test_project.id)).json()["since_last_visit"]["new_scan_count"] == 0


# ---------------------------------------------------------------------------
# v2.476.0 — what the page needs to choose between its setup blocks and the
# work: whether the project has a host, and a scope entry.  (It read the whole
# scope-coverage answer for that.)
# ---------------------------------------------------------------------------

def test_the_workbench_says_whether_the_project_has_hosts_and_scope_entries(client, db_session, test_project):
    from app.db.models_project import Project

    pid = test_project.id
    # Another project's host and subnet are not this project's.
    other = Project(name="Other", slug="other-setup")
    db_session.add(other)
    db_session.flush()
    _make_host(db_session, other.id, "10.9.8.1")
    other_scope = models.Scope(project_id=other.id, name="theirs")
    db_session.add(other_scope)
    db_session.flush()
    db_session.add(models.Subnet(scope_id=other_scope.id, cidr="10.9.8.0/24"))
    db_session.commit()

    def setup(**params):
        r = client.get(_url(pid), params=params)
        assert r.status_code == 200, r.text
        return r.json()["setup"]

    assert setup() == {"has_hosts": False, "has_scopes": False, "scope_rows": 0, "only_scope_id": None}

    # The scope ROW every project gets says nothing: an entry does.
    scope = models.Scope(project_id=pid, name="ours")
    db_session.add(scope)
    db_session.commit()
    assert setup() == {"has_hosts": False, "has_scopes": False, "scope_rows": 1, "only_scope_id": scope.id}

    db_session.add(models.Subnet(scope_id=scope.id, cidr="10.9.9.0/24"))
    db_session.commit()
    assert setup() == {"has_hosts": False, "has_scopes": True, "scope_rows": 1, "only_scope_id": scope.id}

    _make_host(db_session, pid, "10.9.9.1")
    db_session.add(models.Scope(project_id=pid, name="second"))
    db_session.commit()
    light = setup(include_rows="false", include_investigate="false")
    # Two scopes: none is "the" scope.
    assert light == {"has_hosts": True, "has_scopes": True, "scope_rows": 2, "only_scope_id": None}
    assert setup() == light


def test_workbench_returns_my_recent_authored_notes(client, db_session, test_project, test_user):
    """recent_notes surfaces the caller's latest authored notes (distinct from
    my_notes, the assigned-work queue)."""
    host = _make_host(db_session, test_project.id, "10.7.7.7")
    db_session.add(models.Annotation(
        host_id=host.id, user_id=test_user.id, body="my latest investigation note",
    ))
    db_session.commit()

    resp = client.get(_url(test_project.id))
    assert resp.status_code == 200
    recent = resp.json()["recent_notes"]["items"]
    assert recent, "expected the authored note in recent_notes"
    assert recent[0]["host_ip"] == "10.7.7.7"
    assert recent[0]["body_preview"].startswith("my latest investigation")


# v2.451.1 — the team roster and the personal activity feed are gone (owner,
# 2026-10-02: Operations is the reader's own page; nothing read either).
def test_the_team_roster_and_the_activity_feed_are_gone(client, db_session, test_project, test_user):
    # A host in review, so a roster would have had something to say.
    host = _make_host(db_session, test_project.id, "10.3.0.1")
    db_session.add(models.HostFollow(
        host_id=host.id, user_id=test_user.id, status=models.FollowStatus.IN_REVIEW,
    ))
    db_session.commit()

    r = client.get(_url(test_project.id))
    assert r.status_code == 200, r.text
    assert "team_review" not in r.json()
    assert r.json()["my_queue"]["in_review_count"] == 1

    assert client.get(_url(test_project.id, "/my-activity")).status_code == 404


def test_legacy_sessions_do_not_break_the_session_list_and_are_not_listed(
    client, db_session, test_project, test_user,
):
    """v2.340.1 — a legacy plan-generation session 500'd the personal activity
    feed, and project sessions were omitted from it.  v2.442.0 — the legacy
    per-workflow rows (recon, plan generation, execution) have no page left
    to link to, so they are not listed.

    v2.451.1 — that feed (``GET /workbench/my-activity``) was removed; the
    list of sessions is Agent Sessions', so the guard moved to it: the legacy
    rows neither break it nor appear in it."""
    from app.db.models_agent import AgentSession

    legacy = [
        AgentSession(workflow=wf, project_id=test_project.id,
                     started_by_id=test_user.id, status="completed")
        for wf in ("plan_generation", "execution", "recon")
    ]
    project = AgentSession(
        workflow="project", project_id=test_project.id,
        started_by_id=test_user.id, status="active",
    )
    db_session.add_all([*legacy, project])
    db_session.commit()

    r = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions")
    assert r.status_code == 200, r.text
    assert [(s["id"], s["kind"]) for s in r.json()["sessions"]] == [(project.id, "project")]


# v2.424.1 — Operations loads the "Worth a look" queue on its own request so
# the personal sections are not held up by it on a large project.
def test_workbench_can_leave_out_the_queue(client, test_project, monkeypatch):
    from app.api.v1.endpoints import workbench

    calls = []
    real = workbench.compute_investigation_queue
    monkeypatch.setattr(workbench, "compute_investigation_queue",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    r = client.get(_url(test_project.id), params={"include_investigate": "false"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["investigate"] is None and body["investigate_unavailable"] is False
    assert calls == []
    # The default is unchanged for every other caller.
    assert client.get(_url(test_project.id)).json()["investigate"] is not None


def test_the_queue_alone_matches_the_embedded_one(client, test_project):
    embedded = client.get(_url(test_project.id)).json()["investigate"]
    alone = client.get(_url(test_project.id, "/investigate"))
    assert alone.status_code == 200, alone.text
    assert alone.json() == embedded


def test_the_queue_alone_says_when_it_failed(client, test_project, monkeypatch):
    from app.api.v1.endpoints import workbench

    def _boom(*args, **kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr(workbench, "compute_investigation_queue", _boom)
    r = client.get(_url(test_project.id, "/investigate"))
    assert r.status_code == 503
    assert "could not be computed" in r.json()["detail"]
