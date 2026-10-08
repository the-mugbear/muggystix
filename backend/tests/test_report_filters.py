"""Regression: both inventory downloads must honour the FULL filter context.

The Critical review finding was that report routes threaded only a subset of
filters into the query builder, so exports could include more hosts than the
visible /hosts list.  The routes now derive the builder kwargs from the shared
``HostFilterParams`` bundle; this test pins that the advanced filters (tags,
tech, assigned_to, q, and a has_* boolean) survive for each download.

Two paths:
- the **CSV** streams synchronously off the cheap id-only query — assert the
  context reaches ``_filtered_host_id_query``;
- the **JSON** enqueues a report job — assert the context is stored on the job
  (the report worker replays it), and that the worker hands exactly that
  context to the same id query.

The HTML report, the agent package and the Markdown bundle were retired with
"Export hosts" (owner, 2026-10-07); the last tests pin that they are refused
rather than silently produced.
"""

import pytest

from app.api.v1.endpoints import reports as reports_mod
from app.db.models import ReportJob
from app.services.report_job_service import ReportJobService

EXPECTED_PARAMS = {
    "tags": "3",
    "tech": "nginx",
    "assigned_to": "me",
    "q": "port:443",
    "has_exploit_available": "true",
    "has_web_interface": "true",
    "subnet_labels": "2",
}


def _assert_full_context(captured):
    assert captured.get("tags") == "3"
    assert captured.get("tech") == "nginx"
    assert captured.get("assigned_to") == "me"
    assert captured.get("q") == "port:443"
    assert captured.get("subnet_labels") == "2"
    assert captured.get("has_exploit_available") is True
    assert captured.get("has_web_interface") is True


class _EmptyIdQuery:
    def order_by(self, *_a):
        return self

    def all(self):
        return []


def _spy_on_the_id_query(monkeypatch):
    captured = {}

    def id_spy(self, filters):
        captured.clear()
        captured.update(filters)
        return _EmptyIdQuery()

    monkeypatch.setattr(
        reports_mod.ReportGenerator, "_filtered_host_id_query", id_spy, raising=True
    )
    return captured


def test_the_csv_threads_the_full_filter_context(client, test_project, monkeypatch):
    """The CSV streams off the id-only query — the full filter context must
    reach ``_filtered_host_id_query``."""
    captured = _spy_on_the_id_query(monkeypatch)
    resp = client.get(
        f"/api/v1/projects/{test_project.id}/reports/hosts/csv",
        params=EXPECTED_PARAMS,
    )
    assert resp.status_code == 200, resp.text
    _assert_full_context(captured)


def test_the_json_job_stores_and_replays_the_full_filter_context(
    client, db_session, test_project, monkeypatch,
):
    """The JSON is a job; the full filter context must be stored on it, and
    the worker must replay exactly those predicates — a download can never be
    wider than the visible /hosts list."""
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/reports/jobs",
        params={"format": "json", **EXPECTED_PARAMS},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["format"] == "json"

    job = db_session.query(ReportJob).filter(ReportJob.id == body["id"]).first()
    assert job is not None, "enqueued job should be persisted"
    _assert_full_context(job.filters)

    captured = _spy_on_the_id_query(monkeypatch)
    service = ReportJobService()
    assert service.poll_and_run_one() is True
    db_session.refresh(job)
    assert job.status == "completed", job.error_message
    _assert_full_context(captured)
    service._remove_artifact(job)


@pytest.mark.parametrize("fmt", ["agent-package", "markdown-bundle", "html", "pdf", "csv"])
def test_only_the_json_download_can_be_queued(fmt, client, db_session, test_project):
    """The retired bundles (and the formats that never were jobs) are refused
    at the door — no row is written for the worker to fail on."""
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/reports/jobs", params={"format": fmt},
    )
    assert resp.status_code == 422, resp.text
    assert db_session.query(ReportJob).count() == 0


@pytest.mark.parametrize("path", ["/reports/hosts/html", "/reports/limits"])
def test_the_retired_routes_are_gone(path, client, test_project):
    """The HTML host report, and the per-format host caps that only it and
    the Markdown bundle had."""
    assert client.get(f"/api/v1/projects/{test_project.id}{path}").status_code == 404
