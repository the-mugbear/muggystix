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
    reviewed = _make_host(db_session, test_project.id, "10.9.9.2")
    db_session.add(models.HostFollow(
        host_id=reviewed.id, user_id=1, status=models.FollowStatus.REVIEWED,
        review_conclusion="needs_evidence", reviewed_at=datetime.now(timezone.utc),
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

    # Bound is a regression guard, not a target — currently 25: ~17 for the
    # personal sections, plus five grouped statements for the investigation
    # queue (v2.347.0: untouched hosts, vulns, high-value ports, conflicts,
    # sources; the changed-since-scan window runs only when a tier-4
    # candidate exists), plus three for the review follow-ups (v2.359.0:
    # reviewed follows, ports first seen after the review, critical/high
    # observations recorded after it).  Flag a fan-out blow-up (e.g. an N+1
    # creeping into a section), not a fixed additive cost.
    assert counter["n"] <= 28, (
        f"workbench issued {counter['n']} SQL statements:\n" + "\n".join(statements)
    )


def test_workbench_returns_all_sections(client, test_project):
    r = client.get(_url(test_project.id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"my_queue", "my_tasks", "team_review", "since_last_visit"}
    # Empty project — sections render their zero states.
    assert body["my_queue"]["items"] == []
    assert body["my_tasks"]["items"] == []
    assert body["team_review"]["reviewers"] == []


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


def test_changes_after_seen_are_reported(client, db_session, test_project):
    # Seed + mark seen.
    client.post(_url(test_project.id, "/seen"))
    body = client.get(_url(test_project.id)).json()["since_last_visit"]
    cursor = datetime.fromisoformat(body["last_viewed_at"])
    after = cursor + timedelta(hours=1)

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
    from app.api.v1.endpoints import workbench

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


def _review(client, project, host, conclusion="no_issue", summary=None):
    r = client.post(
        f"/api/v1/projects/{project.id}/hosts/{host.id}/follow",
        json={"status": "reviewed", "review_conclusion": conclusion, "review_summary": summary},
    )
    assert r.status_code == 200, r.text
    return r


def test_review_followups_resurface_open_questions_and_changes(client, db_session, test_project):
    """A reviewed host left every queue for good.  Two kinds are not done: a
    review concluded "needs more evidence", and a host that changed after it
    was reviewed.  A clean, unchanged review stays out."""
    clean = _make_host(db_session, test_project.id, "10.8.0.1")
    open_q = _make_host(db_session, test_project.id, "10.8.0.2")
    changed = _make_host(db_session, test_project.id, "10.8.0.3")
    db_session.add(models.Port(host_id=changed.id, port_number=22, protocol="tcp", state="open",
                               first_seen=datetime.now(timezone.utc) - timedelta(days=30)))
    db_session.commit()

    _review(client, test_project, clean)
    _review(client, test_project, open_q, "needs_evidence", "waiting on the creds test")
    _review(client, test_project, changed)

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
    assert set(rows) == {"10.8.0.2", "10.8.0.3"}
    assert body["followups"]["total"] == 2 and body["followups"]["mine_total"] == 2

    assert [r["kind"] for r in rows["10.8.0.2"]["reasons"]] == ["needs_evidence"]
    assert rows["10.8.0.2"]["review_summary"] == "waiting on the creds test"
    assert rows["10.8.0.2"]["mine"] is True and rows["10.8.0.2"]["reviewer"] == "test-admin"

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
    from app.api.v1.endpoints import workbench

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


def test_my_activity_feed_unifies_notes_findings_reviews(client, db_session, test_project, test_user):
    """§27 — GET /workbench/my-activity merges the caller's notes, created
    findings, and reviewed hosts into one newest-first feed with deep-links."""
    pid = test_project.id
    host = _make_host(db_session, pid, "10.3.0.1")
    db_session.add(models.Annotation(
        host_id=host.id, user_id=test_user.id, body="checked SMB signing", note_type="observation",
    ))
    db_session.commit()

    fid = client.post(
        f"/api/v1/projects/{pid}/findings", json={"title": "Weak TLS", "severity": "high"},
    ).json()["id"]
    assert client.post(
        f"/api/v1/projects/{pid}/hosts/{host.id}/follow", json={"status": "reviewed"},
    ).status_code == 200

    items = client.get(_url(pid, "/my-activity")).json()["items"]
    by_kind = {e["kind"]: e for e in items}
    assert {"note", "finding_created", "host_reviewed"} <= set(by_kind)
    assert by_kind["note"]["host_id"] == host.id and by_kind["note"]["note_id"] is not None
    assert by_kind["finding_created"]["finding_id"] == fid
    assert by_kind["host_reviewed"]["host_id"] == host.id
    # Newest-first ordering.
    ats = [e["at"] for e in items]
    assert ats == sorted(ats, reverse=True)


def test_my_activity_filters(client, db_session, test_project, test_user):
    """§27 recall filters — kinds restricts type, search matches title/body."""
    pid = test_project.id
    host = _make_host(db_session, pid, "10.3.9.9")
    db_session.add(models.Annotation(
        host_id=host.id, user_id=test_user.id, body="examined kerberos", note_type="observation",
    ))
    db_session.commit()
    client.post(f"/api/v1/projects/{pid}/findings", json={"title": "Weak TLS cipher", "severity": "high"})

    base = _url(pid, "/my-activity")
    notes_only = client.get(f"{base}?kinds=note").json()["items"]
    assert notes_only and all(e["kind"] == "note" for e in notes_only)

    tls = client.get(f"{base}?search=weak%20tls").json()["items"]
    assert [e["kind"] for e in tls] == ["finding_created"]

    kerb = client.get(f"{base}?search=kerberos").json()["items"]
    assert [e["kind"] for e in kerb] == ["note"]

    assert client.get(f"{base}?search=zzznomatch").json()["items"] == []


def test_my_activity_includes_agent_runs(client, db_session, test_project, test_user):
    """§27 — recon/execution/plan-generation runs the caller started appear as
    'session' events with a deep-link; they're excluded from a text search."""
    from app.db import models
    from app.db.models_agent import AgentSession

    from app.db.models_agent import ReconSession, TestPlan

    scope = models.Scope(project_id=test_project.id, name="ext")
    db_session.add(scope)
    db_session.flush()
    s = AgentSession(
        workflow="recon", project_id=test_project.id,
        started_by_id=test_user.id, status="completed",
    )
    db_session.add(s)
    db_session.flush()
    # v2.340.1 — the deep link must carry the RUN id (what /recon/runs/{id}
    # takes), not the session id; the two only coincide by accident.
    run = ReconSession(
        project_id=test_project.id, scope_id=scope.id, agent_session_id=s.id,
        started_by_id=test_user.id, status="completed",
    )
    db_session.add(run)
    db_session.commit()

    base = _url(test_project.id, "/my-activity")
    sessions = [e for e in client.get(base).json()["items"] if e["kind"] == "session"]
    assert len(sessions) == 1
    assert sessions[0]["link"] == f"/recon/runs/{run.id}"

    # kinds filter isolates them; a text search excludes them (no title).
    assert all(e["kind"] == "session" for e in client.get(f"{base}?kinds=session").json()["items"])
    assert client.get(f"{base}?search=anything").json()["items"] == []


def test_my_activity_survives_legacy_plan_sessions_and_lists_project_sessions(
    client, db_session, test_project, test_user,
):
    """v2.340.1 — a legacy plan-generation session 500'd the whole feed (the
    link builder read a ``plan_id`` attribute the session row never had), and
    project sessions — every session since the consolidation — were omitted."""
    from app.db.models_agent import AgentSession, TestPlan

    legacy_plan = AgentSession(
        workflow="plan_generation", project_id=test_project.id,
        started_by_id=test_user.id, status="completed",
    )
    orphan_plan = AgentSession(
        workflow="plan_generation", project_id=test_project.id,
        started_by_id=test_user.id, status="completed",
    )
    project = AgentSession(
        workflow="project", project_id=test_project.id,
        started_by_id=test_user.id, status="active",
    )
    db_session.add_all([legacy_plan, orphan_plan, project])
    db_session.flush()
    plan = TestPlan(
        project_id=test_project.id, title="legacy", status="draft",
        agent_session_id=legacy_plan.id, created_by_user_id=test_user.id,
    )
    db_session.add(plan)
    db_session.commit()

    r = client.get(_url(test_project.id, "/my-activity?kinds=session"))
    assert r.status_code == 200, r.text
    by_summary = {e["summary"]: e["link"] for e in r.json()["items"]}
    links = {e["link"] for e in r.json()["items"]}
    assert len(r.json()["items"]) == 3
    assert f"/test-plans/{plan.id}" in links          # legacy plan session → its plan
    assert None in links                               # a plan session with no plan links nowhere, and does not 500
    assert by_summary.get("Ran an agent session (active)") == "/agent-activity"
