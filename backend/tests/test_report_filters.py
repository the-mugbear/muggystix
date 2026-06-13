"""Regression: every host-report format must honour the FULL filter context.

The Critical review finding was that report routes threaded only a subset of
filters into the query builder, so exports could include more hosts than the
visible /hosts list.  The routes now derive the builder kwargs from the shared
``HostFilterParams`` bundle; this test pins that the advanced filters (tags,
tech, assigned_to, q, and a has_* boolean) survive for every format.

Two paths since reports went part-async (v2.196.0):
- **csv / html** stream synchronously off the cheap id-only query — assert the
  context reaches ``_filtered_host_id_query``.
- **pdf / json / agent-package / markdown-bundle** enqueue a report job — assert
  the context is stored on the job (the report worker replays it).
"""

import pytest

from app.api.v1.endpoints import reports as reports_mod
from app.db.models import ReportJob

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


@pytest.mark.parametrize("fmt", ["csv", "html"])
def test_sync_report_threads_full_filter_context(fmt, client, test_project, monkeypatch):
    """CSV + HTML stream off the id-only query — the full filter context must
    reach ``_filtered_host_id_query``."""
    captured = {}

    class _EmptyIdQuery:
        def all(self):
            return []

    def id_spy(self, filters):
        captured.clear()
        captured.update(filters)
        return _EmptyIdQuery()

    monkeypatch.setattr(
        reports_mod.ReportGenerator, "_filtered_host_id_query", id_spy, raising=True
    )

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/reports/hosts/{fmt}",
        params=EXPECTED_PARAMS,
    )
    assert resp.status_code == 200, resp.text
    _assert_full_context(captured)


@pytest.mark.parametrize("fmt", ["pdf", "json", "agent-package", "markdown-bundle"])
def test_async_report_job_stores_full_filter_context(fmt, client, db_session, test_project):
    """The heavy formats enqueue a job; the full filter context must be stored on
    the job so the worker replays the same predicates a generated report can't
    widen beyond the visible /hosts list."""
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/reports/jobs",
        params={"format": fmt, **EXPECTED_PARAMS},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["format"] == fmt

    job = db_session.query(ReportJob).filter(ReportJob.id == body["id"]).first()
    assert job is not None, "enqueued job should be persisted"
    _assert_full_context(job.filters)
