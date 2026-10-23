"""The agents' ingestion issues are the Ingestion Results page's service
(review 2026-10-10, R1b; the agents' list had no ``offset``, §6).

``GET /agent/assist/ingestion-issues`` re-implemented the page: it loaded
every failed job with ``.all()`` and told expired / discarded uploads apart by
testing their message text.  It now reads ``ingestion_results_service`` — the
page's view conditions and counts — and pages in SQL with ``limit`` and
``offset``.

``EXPECTED`` is what the old implementation answered on this fixture (run
against it before the change, with ``issues`` for ``items``); the paging
tests walk the list a page at a time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models_project import Project
from app.services.staged_import_service import DISCARDED_MESSAGE, EXPIRED_MESSAGE_PREFIX


def _assist(client, project_id):
    body = client.post(f"/api/v1/projects/{project_id}/assist/start", json={}).json()
    return {"X-API-Key": body["api_key"]}


T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _seed(db, pid):
    def at(minutes):
        return T0 + timedelta(minutes=minutes)

    def error(name, minutes, **over):
        row = models.ParseError(
            project_id=pid, filename=name, file_type="nmap_xml", error_type=over.pop("error_type", "parsing_error"),
            error_message=over.pop("error_message", "raw parser text"), status=over.pop("status", "unresolved"),
            created_at=at(minutes), **over,
        )
        db.add(row)
        db.flush()
        return row

    def job(name, minutes, **over):
        row = models.IngestionJob(
            project_id=pid, filename=name, original_filename=name, storage_path=f"/tmp/{name}",
            tool_name="nmap", created_at=at(minutes), **over,
        )
        db.add(row)
        db.flush()
        return row

    truncated_pe = error("cut.xml", 1, error_type="truncated_file", user_message="The file was cut short.")
    job("cut.xml", 2, status="failed", parse_error_id=truncated_pe.id, error_message="generic sentence")
    job("noerr.xml", 3, status="failed", error_message=None, last_error="worker crashed")
    job("late.xml", 4, status="failed", error_message=f"{EXPIRED_MESSAGE_PREFIX} not started within 24 hours")
    discarded_pe = error("dropped.xml", 5, user_message="claimed by a discarded job")
    job("dropped.xml", 6, status="failed", error_message=DISCARDED_MESSAGE, parse_error_id=discarded_pe.id)
    job("old.xml", 7, status="failed", error_message="not XML", dismissed_at=at(8))
    job("truncated.xml", 9, status="completed", partial=True, skipped_count=0,
        parser_warnings="parsing stopped early")
    job("thin.zip", 10, status="completed", skipped_count=3, parser_warnings="3 rows missing a url")
    job("clean.xml", 11, status="completed")
    job("waiting.xml", 12, status="queued")
    job("running.xml", 13, status="processing")
    error("orphan.json", 14, user_message="Not valid JSON.")
    error("resolved.json", 15, status="fixed")
    db.commit()


#: (kind, filename, needs_attention, dismissed, message, failure_kind, skipped_count), newest first.
EXPECTED = [
    ("parse_error", "orphan.json", False, False, "Not valid JSON.", None, 0),
    ("degraded", "thin.zip", False, False,
     "Parsed, but 3 row(s) were dropped — data from this file is incomplete.", None, 3),
    ("partial", "truncated.xml", True, False,
     "Finished partial — parsing stopped early, so part of this file is NOT in the project. "
     "parsing stopped early", None, 0),
    ("failed", "old.xml", False, True, "not XML", None, 0),
    ("expired", "late.xml", True, False, f"{EXPIRED_MESSAGE_PREFIX} not started within 24 hours", None, 0),
    ("failed", "noerr.xml", True, False, "worker crashed", None, 0),
    ("failed", "cut.xml", True, False, "The file was cut short.", "truncated_file", 0),
]
EXPECTED_COUNTS = {
    "queued": 1, "processing": 1, "failed": 3, "expired": 1, "discarded": 1, "needs_attention": 5,
    "partial": 1, "degraded": 1, "unresolved_parse_errors": 1, "unresolved_parse_errors_total": 3,
    "has_issues": True,
}


def _rows(body):
    rows = body["items"] if "items" in body else body["issues"]
    return [
        (r["kind"], r["filename"], r["needs_attention"], r["dismissed"], r["message"], r["failure_kind"],
         r["skipped_count"])
        for r in rows
    ]


def test_the_agents_issues_are_what_they_were(client, db_session, test_project):
    _seed(db_session, test_project.id)
    other = Project(name="other-r1b", slug="other-r1b")
    db_session.add(other)
    db_session.flush()
    db_session.add(models.IngestionJob(project_id=other.id, filename="x.xml", original_filename="x.xml",
                                       storage_path="/tmp/x.xml", status="failed", error_message="no"))
    db_session.commit()

    body = client.get("/api/v1/agent/assist/ingestion-issues", headers=_assist(client, test_project.id),
                      params={"limit": 100}).json()
    assert _rows(body) == EXPECTED
    assert {k: body[k] for k in EXPECTED_COUNTS} == EXPECTED_COUNTS
    assert body.get("total", body.get("total_issues")) == len(EXPECTED)

    # The counts are the page's own.
    summary = client.get(f"/api/v1/projects/{test_project.id}/parse-errors/ingestion-results").json()["summary"]
    assert (body["failed"], body["expired"], body["discarded"], body["needs_attention"],
            body["queued"], body["processing"]) == (
        summary["total_failed"], summary["total_expired"], summary["total_discarded"],
        summary["total_needs_attention"], summary["total_queued"], summary["total_processing"])


def test_the_agents_issues_page_by_limit_and_offset(client, db_session, test_project):
    _seed(db_session, test_project.id)
    headers = _assist(client, test_project.id)
    seen = []
    offset = 0
    while True:
        body = client.get("/api/v1/agent/assist/ingestion-issues", headers=headers,
                          params={"limit": 3, "offset": offset}).json()
        assert (body["total"], body["limit"], body["offset"], body["returned"]) == (
            len(EXPECTED), 3, offset, len(body["items"]))
        # The counts do not change with the page.
        assert {k: body[k] for k in EXPECTED_COUNTS} == EXPECTED_COUNTS
        seen += _rows(body)
        if not body["has_more"]:
            break
        offset += body["returned"]
    assert seen == EXPECTED


def test_a_project_with_nothing_wrong_has_no_issues(client, test_project):
    body = client.get("/api/v1/agent/assist/ingestion-issues", headers=_assist(client, test_project.id)).json()
    assert body["has_issues"] is False
    assert (body["items"], body["total"], body["has_more"]) == ([], 0, False)
