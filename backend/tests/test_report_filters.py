"""Regression: every host-report format must honour the FULL filter context.

The Critical review finding was that report routes threaded only a subset of
filters into the query builder, so CSV/JSON/HTML/agent-package/markdown exports
could include more hosts than the visible /hosts list. The routes now derive the
builder kwargs from the shared ``HostFilterParams`` bundle; this test pins that
the advanced filters (tags, tech, assigned_to, q, and a has_* boolean) actually
reach ``get_hosts_for_report`` for every format.
"""

import pytest

from app.api.v1.endpoints import reports as reports_mod


@pytest.mark.parametrize(
    "fmt",
    ["csv", "html", "json", "agent-package", "markdown-bundle"],
)
def test_report_threads_full_filter_context(fmt, client, test_project, monkeypatch):
    captured = {}

    def spy(self, filters):
        captured.clear()
        captured.update(filters)
        return []  # empty host set — generators handle it; we only assert filters

    monkeypatch.setattr(
        reports_mod.ReportGenerator, "get_hosts_for_report", spy, raising=True
    )

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/reports/hosts/{fmt}",
        params={
            "tags": "3",
            "tech": "nginx",
            "assigned_to": "me",
            "q": "port:443",
            "has_exploit_available": "true",
            "has_web_interface": "true",
            "subnet_labels": "2",
        },
    )

    assert resp.status_code == 200, resp.text
    # All previously-dropped advanced filters must reach the query builder.
    assert captured.get("tags") == "3"
    assert captured.get("tech") == "nginx"
    assert captured.get("assigned_to") == "me"
    assert captured.get("q") == "port:443"
    assert captured.get("subnet_labels") == "2"
    assert captured.get("has_exploit_available") is True
    assert captured.get("has_web_interface") is True
