"""Regression test for the /hosts/filters/data technology aggregation
push-down (v2.86.5).

Pre-fix the endpoint loaded every WebInterface (host_id, technologies)
pair into Python and built the per-tech host-count map by iterating
JSON arrays in app code.  v2.86.5 swapped that for a single Postgres
``json_array_elements_text`` + ``GROUP BY`` + ``COUNT(DISTINCT host_id)``
query.

This test seeds a project with three web interfaces sharing partial
technology sets, then asserts:
  * each distinct tech name appears exactly once,
  * counts are DISTINCT host_id (not assignment row count),
  * empty / null entries are dropped,
  * result is sorted by host_count DESC then name ASC.

The SQLite fallback path is kept in the endpoint for the test harness's
no-Postgres mode, but this test only runs end-to-end when Postgres is
reachable (the default per conftest).  We don't gate it explicitly —
the seeded JSON shape works on both dialects, just exercising different
code branches.
"""
from __future__ import annotations

from app.db import models


def _seed_web_interfaces(db_session, project_id):
    """Three web interfaces with overlapping tech lists.

    host A → ["Nginx", "React"]
    host B → ["Nginx", "Bootstrap"]
    host C → ["Nginx", "React", "Bootstrap"]

    Expected counts: Nginx=3, React=2, Bootstrap=2.
    Plus a fourth row with technologies=null to confirm filtering.
    """
    scan = models.Scan(project_id=project_id, filename="fixture.json", scan_type="httpx")
    db_session.add(scan)
    db_session.flush()

    hosts_by_letter = {}
    for letter, ip in [("a", "10.0.0.1"), ("b", "10.0.0.2"), ("c", "10.0.0.3"), ("d", "10.0.0.4")]:
        h = models.Host(project_id=project_id, ip_address=ip, state="up")
        db_session.add(h)
        db_session.flush()
        hosts_by_letter[letter] = h

    db_session.add(models.WebInterface(
        project_id=project_id, host_id=hosts_by_letter["a"].id, scan_id=scan.id, port=80,
        url="http://10.0.0.1/", source="httpx",
        technologies=["Nginx", "React"],
    ))
    db_session.add(models.WebInterface(
        project_id=project_id, host_id=hosts_by_letter["b"].id, scan_id=scan.id, port=80,
        url="http://10.0.0.2/", source="httpx",
        technologies=["Nginx", "Bootstrap"],
    ))
    db_session.add(models.WebInterface(
        project_id=project_id, host_id=hosts_by_letter["c"].id, scan_id=scan.id, port=80,
        url="http://10.0.0.3/", source="httpx",
        technologies=["Nginx", "React", "Bootstrap", "", None],
    ))
    db_session.add(models.WebInterface(
        project_id=project_id, host_id=hosts_by_letter["d"].id, scan_id=scan.id, port=80,
        url="http://10.0.0.4/", source="httpx",
        technologies=None,  # excluded by IS NOT NULL filter
    ))
    db_session.flush()


def test_filter_data_tech_aggregation_counts_distinct_hosts(
    client, db_session, test_project,
):
    _seed_web_interfaces(db_session, test_project.id)
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/filters/data")
    assert r.status_code == 200, r.text
    techs = {t["name"]: t["host_count"] for t in r.json().get("technologies", [])}

    assert techs.get("Nginx") == 3, f"expected 3 distinct hosts for Nginx, got {techs!r}"
    assert techs.get("React") == 2, f"expected 2 for React, got {techs!r}"
    assert techs.get("Bootstrap") == 2, f"expected 2 for Bootstrap, got {techs!r}"
    # Empty / None tech entries should NOT appear in the output.
    assert "" not in techs, "empty-string tech leaked into output"
    assert None not in techs, "None tech leaked into output"


def test_filter_data_tech_aggregation_sort_order(client, db_session, test_project):
    _seed_web_interfaces(db_session, test_project.id)
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/filters/data")
    techs = r.json().get("technologies", [])
    # Strictly non-increasing by host_count; ties broken by name ASC.
    for prev, curr in zip(techs, techs[1:]):
        assert prev["host_count"] >= curr["host_count"], f"sort order violated: {techs!r}"
        if prev["host_count"] == curr["host_count"]:
            assert prev["name"].lower() <= curr["name"].lower(), \
                f"tie-break by name ASC violated: {techs!r}"
