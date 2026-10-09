"""A Hosts-list row's two test counts, by the names they carry (v2.473.0).

Until v2.473.0 the row called them ``test_plan_entry_count`` and
``test_execution_count`` — names from the test plans removed in v2.442.0.
They are ``planned_test_count`` (the host's tests proposed or in progress)
and ``tested_record_count`` (its evidence records with a tested outcome).
Pins:

* the row carries the new names, with the counts the Hosts page marks rows by;
* the retired names are gone (no alias: the Hosts page was the one reader);
* "planned" is a test still to do, "tested" is a record that tested something
  — a dismissed test and a failed or informational record count as neither.
"""
from datetime import datetime, timezone

from app.db import models
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord


def _host(db, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _test(db, project_id, host, status, key):
    db.add(HostTest(
        project_id=project_id, host_id=host.id, priority="high", tool="t",
        description="d", rationale="r", status=status, source="person",
        request_key=key, request_hash=key,
        **({"dismissed_reason": "not needed"} if status == "dismissed" else {}),
    ))


def _evidence(db, project_id, host, outcome):
    db.add(EvidenceRecord(
        project_id=project_id, host_id=host.id, tool="nmap", outcome=outcome, summary="s",
        executed_at=datetime.now(timezone.utc),
    ))


def test_a_host_row_names_its_planned_and_tested_counts(client, db_session, test_project):
    worked = _host(db_session, test_project.id, "10.61.0.1")
    _host(db_session, test_project.id, "10.61.0.2")   # a host with neither
    _test(db_session, test_project.id, worked, "proposed", "k-1")
    _test(db_session, test_project.id, worked, "in_progress", "k-2")
    _test(db_session, test_project.id, worked, "dismissed", "k-3")
    for outcome in ("finding", "no_finding", "inconclusive", "failed", "info"):
        _evidence(db_session, test_project.id, worked, outcome)
    db_session.commit()

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/?limit=50")
    assert r.status_code == 200, r.text
    rows = {h["ip_address"]: h for h in r.json()["items"]}

    assert rows["10.61.0.1"]["planned_test_count"] == 2      # proposed + in progress; not the dismissed one
    assert rows["10.61.0.1"]["tested_record_count"] == 3     # finding / no_finding / inconclusive
    assert rows["10.61.0.2"]["planned_test_count"] == 0
    assert rows["10.61.0.2"]["tested_record_count"] == 0

    for row in rows.values():
        assert "test_plan_entry_count" not in row
        assert "test_execution_count" not in row
