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
    db_session.flush()

    counter = {"n": 0}

    def _count(conn, cursor, statement, params, context, executemany):
        counter["n"] += 1

    event.listen(Engine, "after_cursor_execute", _count)
    try:
        r = client.get(_url(test_project.id))
        assert r.status_code == 200, r.text
    finally:
        event.remove(Engine, "after_cursor_execute", _count)

    # Bound is a regression guard, not a target — currently ~17 (the
    # recent_notes section added one SELECT); flag a fan-out blow-up (e.g. an
    # N+1 creeping into a section).
    assert counter["n"] <= 20, f"workbench issued {counter['n']} SQL statements"


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
