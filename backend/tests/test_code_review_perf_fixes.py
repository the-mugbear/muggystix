"""Regression tests for the v2.91.1 code-review perf fixes.

The two fixes this file was written for no longer have an endpoint:

  NEW E — execution-progress counted entries and results with grouped SQL
          instead of materialising every row on every poll.  The endpoint
          (``/agent/test-plans/{id}/execution-progress``) went with test plans
          and execution runs in v2.442.0.

  NEW H — list_agents batched its APIKey lookup.  Its test was removed in
          v2.295.0 along with the ``/agents`` router.

What E guarded — "a progress read derives its counts in SQL, in a fixed
number of statements, and the counts are right" — still has a home: the host
tests listing, which is what an operator or an agent now polls for progress.
Each row carries its ``evidence_count`` from ONE grouped query
(``host_test_service.serialize_many``), never a query per test.
"""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.db import models
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord


def _seed(db_session, project, n_tests, start_octet):
    """``n_tests`` tests on as many hosts; test i gets i % 3 evidence records."""
    rows = []
    for i in range(n_tests):
        host = models.Host(project_id=project.id, ip_address=f"10.77.{start_octet}.{i + 1}", state="up")
        db_session.add(host)
        db_session.flush()
        test = HostTest(
            project_id=project.id, host_id=host.id, tool="nmap", description=f"t{i}",
            rationale="r", priority="medium", status="proposed", source="person",
            request_key=f"perf-{start_octet}-{i}", request_hash="0" * 64,
        )
        db_session.add(test)
        db_session.flush()
        for _ in range(i % 3):
            db_session.add(EvidenceRecord(
                project_id=project.id, host_id=host.id, host_test_id=test.id,
                tool="nmap", outcome="no_finding", summary="s",
            ))
        rows.append(test)
    db_session.commit()
    return rows


def _listing(client, project):
    counter = {"n": 0}

    def _count(conn, cursor, statement, params, context, executemany):
        counter["n"] += 1

    event.listen(Engine, "after_cursor_execute", _count)
    try:
        r = client.get(f"/api/v1/projects/{project.id}/host-tests", params={"limit": 200})
        assert r.status_code == 200, r.text
    finally:
        event.remove(Engine, "after_cursor_execute", _count)
    return r.json(), counter["n"]


def test_host_test_listing_counts_evidence_in_sql_not_per_row(client, db_session, test_project):
    """The counts match the row-by-row arithmetic, and the number of
    statements does not grow with the number of tests listed."""
    small = _seed(db_session, test_project, 3, start_octet=1)
    body, few = _listing(client, test_project)
    assert body["total"] == 3
    by_id = {t["id"]: t["evidence_count"] for t in body["items"]}
    assert by_id == {t.id: i % 3 for i, t in enumerate(small)}

    large = _seed(db_session, test_project, 30, start_octet=2)
    body, many = _listing(client, test_project)
    assert body["total"] == 33
    by_id = {t["id"]: t["evidence_count"] for t in body["items"]}
    assert by_id == {
        **{t.id: i % 3 for i, t in enumerate(small)},
        **{t.id: i % 3 for i, t in enumerate(large)},
    }
    # Ten times the rows, the same statements: a per-test count (or a lazy
    # load of each test's host / assignee) would add at least one per row.
    assert many == few, f"{few} statements for 3 tests, {many} for 33"


def test_host_test_listing_of_an_empty_project_is_zeros(client, test_project):
    """No tests: an empty page and a zero total, not an error."""
    body, _ = _listing(client, test_project)
    assert body == {"items": [], "total": 0, "has_more": False}
