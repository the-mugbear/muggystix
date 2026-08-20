"""The /hosts list must not issue a query per host.

The endpoint carefully batches every enrichment (vuln summaries, note counts,
follows, test-plan counts, web interfaces, …) into page-wide maps, and says so
in a long comment. One relationship escaped that discipline: `host_scripts` is
a `HostSchema` field, so `serialize_host_base` reads it — and because the list
query neither eager-loaded nor suppressed it, SQLAlchemy loaded it **lazily,
one host at a time**. The list endpoint then discarded the result
(`serialized["host_scripts"] = []`).

Measured before the fix: 126 queries for a 100-row page, 426 for a 500-row
page — i.e. ~20 fixed plus one per host, with the per-host results thrown away.
After: 26, flat.

This test pins the *shape*, not a magic number: the query count for a large
page must equal the count for a small one. An absolute budget would either be
brittle or need updating with every legitimate new batch query; "does not grow
with the number of hosts" is the property that actually matters, and it fails
loudly the moment a relationship starts lazy-loading per row again.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import event

from app.db import models


def _seed_hosts(db, project_id, count, *, offset=0):
    scan = models.Scan(project_id=project_id, filename=f"budget-{offset}.xml", tool_name="nmap")
    db.add(scan)
    db.flush()
    for i in range(count):
        host = models.Host(
            project_id=project_id,
            ip_address=f"10.60.{offset}.{i + 1}",
            state="up",
            hostname=f"budget-{offset}-{i}.example",
        )
        db.add(host)
        db.flush()
        db.add(models.HostScanHistory(
            host_id=host.id, scan_id=scan.id, state_at_scan="up",
            discovered_at=datetime.now(timezone.utc),
        ))
        port = models.Port(
            host_id=host.id, port_number=443, protocol="tcp", state="open",
            service_name="https",
        )
        db.add(port)
        db.flush()
        # A host script per host — the relationship that was lazy-loading.
        # Without rows here the N+1 still fires, but this makes the fixture
        # represent the real shape rather than an empty edge case.
        db.add(models.HostScript(
            host_id=host.id, scan_id=scan.id,
            script_id="smb-os-discovery", output="OS: Windows",
        ))
    db.commit()


@pytest.fixture
def count_queries(db_session):
    """Count SQL statements issued during a block, on the test connection."""
    from tests.conftest import engine

    counter = {"n": 0}

    def _before(conn, cursor, statement, params, context, executemany):
        counter["n"] += 1

    def _run(fn):
        counter["n"] = 0
        event.listen(engine, "before_cursor_execute", _before)
        try:
            fn()
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        return counter["n"]

    return _run


def test_host_list_query_count_does_not_grow_with_page_size(
    client, db_session, test_project, count_queries
):
    pid = test_project.id
    _seed_hosts(db_session, pid, 5, offset=0)

    def _fetch(limit):
        resp = client.get(f"/api/v1/projects/{pid}/hosts/?limit={limit}")
        assert resp.status_code == 200, resp.text
        return resp

    small = count_queries(lambda: _fetch(100))

    # Ten times the hosts, same request.
    _seed_hosts(db_session, pid, 45, offset=1)
    large = count_queries(lambda: _fetch(100))

    assert large == small, (
        f"{small} queries for 5 hosts but {large} for 50 — the list is issuing "
        "roughly one query per host. Something the serializer reads is being "
        "lazy-loaded per row; add it to the noload() block in get_hosts_v2 or "
        "batch it into a page-wide map."
    )


def test_host_list_still_returns_the_hosts_it_stopped_querying_for(
    client, db_session, test_project
):
    """Guard the fix itself: suppressing a relationship must not empty the
    response. The list has always sent `host_scripts: []` — that is the
    documented list-weight payload, and the detail endpoint loads the real
    thing — so this pins the contract rather than the accident."""
    pid = test_project.id
    _seed_hosts(db_session, pid, 3, offset=2)

    body = client.get(f"/api/v1/projects/{pid}/hosts/?limit=100").json()
    # Filter to the seeded set — the project fixture carries hosts of its own.
    seeded = [h for h in body["items"] if h["ip_address"].startswith("10.60.2.")]
    assert len(seeded) == 3
    row = seeded[0]
    # Ports are still eager-loaded and serialized; scripts are deliberately not.
    assert row["ports"][0]["port_number"] == 443
    assert row["host_scripts"] == []
