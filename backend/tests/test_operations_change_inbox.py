"""Operations "since your last visit" as a change inbox + blockers (v2.363.0).

Two gaps from the 2026-09-19 Operations review, closed together:

* The banner's counts were passive badges.  They now open the exact hosts they
  counted: the counts and the DSL fields ``firstseen:`` / ``changedsince:`` /
  ``vulnsince:`` share ONE window definition (host_query_predicates), over the
  same (last_viewed, as_of] window.  Pins that a count and its link agree, that
  new records and changes to existing targets are disjoint, and that severity
  and time are matched on the SAME observation row.
* Work that has stopped was shown nowhere an analyst starts the day: failed /
  partial imports nobody dismissed, and execution runs that are paused or
  whose agent session ended.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models_agent import AgentSession, ExecutionSession, TestPlan
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


def _wb(pid, suffix=""):
    return f"/api/v1/projects/{pid}/workbench{suffix}"


def _hosts(client, pid, q):
    r = client.get(f"/api/v1/projects/{pid}/hosts/", params={"q": q, "limit": 50})
    assert r.status_code == 200, r.text
    return sorted(h["ip_address"] for h in r.json()["items"])


def _host(db, pid, ip, first_seen):
    h = models.Host(project_id=pid, ip_address=ip, state="up", first_seen=first_seen)
    db.add(h)
    db.flush()
    return h


def _vuln(db, host, scan, severity, created_at):
    # Vulnerability.created_at is a naive column.
    db.add(Vulnerability(
        title="obs", severity=severity, source=VulnerabilitySource.MANUAL,
        host_id=host.id, scan_id=scan.id, created_at=created_at.replace(tzinfo=None),
    ))
    db.flush()


def _seed(client, db, project):
    """Last visit two hours ago; one brand-new host, one known host that gained
    a port, one known host that gained a CRITICAL observation, one known host
    with an OLD critical and a NEW low, and one untouched known host."""
    client.post(_wb(project.id, "/seen"))
    cursor = db.query(models.OperationsCursor).filter(
        models.OperationsCursor.project_id == project.id).one()
    visit = datetime.now(timezone.utc) - timedelta(hours=2)
    cursor.last_viewed_at = visit
    before, after = visit - timedelta(days=3), visit + timedelta(hours=1)

    scan = models.Scan(project_id=project.id, filename="s.xml", created_at=after)
    db.add(scan)
    db.flush()

    _host(db, project.id, "10.7.0.1", after)                       # new record
    gained_port = _host(db, project.id, "10.7.0.2", before)
    db.add(models.Port(host_id=gained_port.id, port_number=8443, protocol="tcp",
                       state="open", first_seen=after))
    gained_crit = _host(db, project.id, "10.7.0.3", before)
    _vuln(db, gained_crit, scan, VulnerabilitySeverity.CRITICAL, after)
    mixed = _host(db, project.id, "10.7.0.4", before)
    _vuln(db, mixed, scan, VulnerabilitySeverity.CRITICAL, before)   # old critical
    _vuln(db, mixed, scan, VulnerabilitySeverity.LOW, after)         # new low
    _host(db, project.id, "10.7.0.5", before)                       # untouched
    db.commit()


def _window(since):
    return f'{since["last_viewed_at"]}..{since["as_of"]}'


def test_each_count_opens_exactly_the_hosts_it_counted(client, db_session, test_project):
    _seed(client, db_session, test_project)
    since = client.get(_wb(test_project.id)).json()["since_last_visit"]
    w = _window(since)

    new = _hosts(client, test_project.id, f'firstseen:"{w}"')
    assert new == ["10.7.0.1"]
    assert since["new_host_count"] == len(new)

    changed = _hosts(client, test_project.id, f'changedsince:"{w}"')
    assert changed == ["10.7.0.2", "10.7.0.3", "10.7.0.4"]
    assert since["changed_host_count"] == len(changed)
    # New records and changes to existing targets never overlap.
    assert not set(new) & set(changed)

    crit = _hosts(client, test_project.id, f'vulnsince:"critical@{w}"')
    assert crit == ["10.7.0.3"]
    assert since["new_critical_findings"] == 1
    assert since["new_critical_hosts"] == len(crit)


def test_severity_and_time_are_matched_on_the_same_row(client, db_session, test_project):
    """10.7.0.4 has a critical (old) and something new (a low).  `has:critical`
    AND "new observation" would list it as a new critical; it is not one."""
    _seed(client, db_session, test_project)
    w = _window(client.get(_wb(test_project.id)).json()["since_last_visit"])
    assert "10.7.0.4" not in _hosts(client, test_project.id, f'vulnsince:"critical@{w}"')
    assert "10.7.0.4" in _hosts(client, test_project.id, f'vulnsince:"low@{w}"')
    assert "10.7.0.4" in _hosts(client, test_project.id, f'vulnsince:"{w}"')


def test_a_window_is_bounded_by_the_snapshot(client, db_session, test_project):
    """Something arriving after the displayed snapshot belongs to the NEXT one —
    the same contract `POST /workbench/seen {as_of}` acknowledges."""
    _seed(client, db_session, test_project)
    since = client.get(_wb(test_project.id)).json()["since_last_visit"]
    _host(db_session, test_project.id, "10.7.0.9", datetime.now(timezone.utc) + timedelta(minutes=5))
    db_session.commit()
    assert _hosts(client, test_project.id, f'firstseen:"{_window(since)}"') == ["10.7.0.1"]
    # Open-ended ("since then") does include it.
    assert "10.7.0.9" in _hosts(client, test_project.id, f'firstseen:"{since["last_viewed_at"]}"')


def test_a_bad_window_is_a_query_error_not_an_empty_list(client, test_project):
    for q in ('firstseen:"yesterday"', 'vulnsince:"urgent@2026-09-19T00:00:00Z"',
              'changedsince:"2026-09-20T00:00:00Z..2026-09-19T00:00:00Z"'):
        r = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": q})
        assert r.status_code == 400, (q, r.status_code, r.text)


# --- blockers ---------------------------------------------------------------

def _job(db, pid, name, status, **kw):
    db.add(models.IngestionJob(
        project_id=pid, filename=name, original_filename=name,
        storage_path=f"/tmp/{name}", status=status, **kw,
    ))


def test_blockers_list_stopped_imports_until_dismissed(client, db_session, test_project):
    pid = test_project.id
    _job(db_session, pid, "broken.xml", "failed", error_message="not well-formed")
    _job(db_session, pid, "half.nessus", "completed", partial=True,
         message="Nessus file processed successfully",
         parser_warnings="Incomplete XML — hosts after this point are MISSING")
    _job(db_session, pid, "fine.xml", "completed")
    _job(db_session, pid, "old-broken.xml", "failed", dismissed_at=datetime.now(timezone.utc))
    db_session.commit()

    body = client.get(_wb(pid)).json()
    assert body["blockers_unavailable"] is False
    b = body["blockers"]
    assert (b["failed_import_count"], b["partial_import_count"]) == (1, 1)
    kinds = {i["filename"]: i["kind"] for i in b["imports"]}
    assert kinds == {"broken.xml": "failed", "half.nessus": "partial"}
    assert next(i for i in b["imports"] if i["kind"] == "failed")["message"] == "not well-formed"
    # A partial job's own message is the parser's success line; what was lost
    # is in the warnings, and that is what a list of blocked work must show.
    partial = next(i for i in b["imports"] if i["kind"] == "partial")
    assert "MISSING" in partial["message"] and "successfully" not in partial["message"]


def test_blockers_list_runs_that_stopped_without_completing(client, db_session, test_project, test_agent):
    pid = test_project.id

    versions = iter(range(1, 10))

    def plan(title):
        p = TestPlan(project_id=pid, version=next(versions), title=title, status="in_progress")
        db_session.add(p)
        db_session.flush()
        return p

    def agent_session(status):
        s = AgentSession(workflow="execution", project_id=pid, agent_id=test_agent.id, status=status)
        db_session.add(s)
        db_session.flush()
        return s

    live, ended = agent_session("active"), agent_session("completed")
    db_session.add_all([
        ExecutionSession(test_plan_id=plan("Paused plan").id, status="paused"),
        ExecutionSession(test_plan_id=plan("Orphaned plan").id, status="active", agent_session_id=ended.id),
        ExecutionSession(test_plan_id=plan("Running plan").id, status="active", agent_session_id=live.id),
        ExecutionSession(test_plan_id=plan("Done plan").id, status="completed", agent_session_id=ended.id),
        # v2.424.0 — a legacy run (no parent session) whose agent holds no
        # live key: the Runs list called it stalled; Blocked missed it.
        ExecutionSession(test_plan_id=plan("Legacy plan").id, status="active", agent_id=test_agent.id),
    ])
    db_session.commit()

    b = client.get(_wb(pid)).json()["blockers"]
    assert b["interrupted_execution_count"] == 3
    assert {e["plan_title"]: e["reason"] for e in b["executions"]} == {
        "Paused plan": "paused", "Orphaned plan": "session_ended", "Legacy plan": "session_ended",
    }


def test_nothing_blocked_is_empty_not_unavailable(client, test_project):
    body = client.get(_wb(test_project.id)).json()
    assert body["blockers_unavailable"] is False
    assert body["blockers"]["imports"] == [] and body["blockers"]["executions"] == []


# --- the list the "Inspect import errors" button opens ----------------------

def _results(client, pid, **params):
    r = client.get(f"/api/v1/projects/{pid}/parse-errors/ingestion-results", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_needs_attention_lists_exactly_what_the_blockers_counted(client, db_session, test_project):
    """The button said "Inspect import errors" and landed on every upload.  The
    filter is the SAME condition the blockers count, so they cannot disagree."""
    pid = test_project.id
    _job(db_session, pid, "broken.xml", "failed", error_message="not well-formed")
    _job(db_session, pid, "half.nessus", "completed", partial=True, parser_warnings="hosts MISSING")
    _job(db_session, pid, "fine.xml", "completed")
    _job(db_session, pid, "old-broken.xml", "failed", dismissed_at=datetime.now(timezone.utc))
    db_session.commit()

    b = client.get(_wb(pid)).json()["blockers"]
    body = _results(client, pid, status="needs_attention")
    assert sorted(i["original_filename"] for i in body["items"]) == ["broken.xml", "half.nessus"]
    assert body["total"] == b["failed_import_count"] + b["partial_import_count"] == 2
    assert body["summary"]["total_needs_attention"] == 2
    # Unfiltered, everything is still there — the filter hides nothing for good.
    assert _results(client, pid)["total"] == 4


def test_a_partial_import_is_visibly_partial_in_the_results_list(client, db_session, test_project):
    _job(db_session, test_project.id, "half.nessus", "completed", partial=True,
         skipped_count=3, parser_warnings="hosts after this point are MISSING")
    db_session.commit()
    (row,) = _results(client, test_project.id)["items"]
    assert row["status"] == "completed" and row["partial"] is True
    assert row["skipped_count"] == 3 and "MISSING" in row["parser_warnings"]
    assert row["dismissed_at"] is None


def test_a_partial_import_can_be_dismissed_and_then_stops_blocking(client, db_session, test_project):
    """Only FAILED jobs were dismissable, so a partial import listed as blocked
    could never be cleared — a permanent banner."""
    pid = test_project.id
    _job(db_session, pid, "half.nessus", "completed", partial=True, submitted_by_id=1)
    _job(db_session, pid, "fine.xml", "completed", submitted_by_id=1)
    db_session.commit()
    jobs = {j.original_filename: j.id for j in db_session.query(models.IngestionJob).all()}

    r = client.post(f"/api/v1/projects/{pid}/upload/jobs/{jobs['half.nessus']}/dismiss")
    assert r.status_code == 200, r.text
    assert client.get(_wb(pid)).json()["blockers"]["partial_import_count"] == 0
    # Dismissed, not laundered: it still reads partial in the full list.
    row = next(i for i in _results(client, pid)["items"] if i["original_filename"] == "half.nessus")
    assert row["partial"] is True and row["dismissed_at"] is not None

    # A clean completed job has nothing to dismiss.
    r = client.post(f"/api/v1/projects/{pid}/upload/jobs/{jobs['fine.xml']}/dismiss")
    assert r.status_code == 400
