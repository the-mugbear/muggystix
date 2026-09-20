"""``web_interface_count`` counts interfaces, not per-scan rows (v2.362.0).

``web_interfaces`` keeps one row per scan, so a host whose two URLs were
captured by EyeWitness in two scans has four rows.  The inspector's section
shows the latest observation of each (tool, URL) — two — and the count beside
it, and the Hosts list badge, said four.  Pins:

* the same (tool, URL) seen by several scans counts once, on the detail
  endpoint AND the list (they are one number to the operator);
* a different tool or a different URL is a different interface;
* a host with none still reports zero, and another host's rows never leak in.
"""
from app.db import models
from app.db.models import WebInterface


def _host(db, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _scan(db, project_id, name):
    s = models.Scan(filename=name, project_id=project_id)
    db.add(s)
    db.flush()
    return s


def _iface(db, project_id, host, scan, url, source="eyewitness"):
    db.add(WebInterface(
        host_id=host.id, scan_id=scan.id, project_id=project_id,
        source=source, port=8443, url=url,
    ))
    db.flush()


def _seed(db, project):
    rescanned = _host(db, project.id, "10.44.0.1")
    bare = _host(db, project.id, "10.44.0.2")
    august, september = _scan(db, project.id, "aug.csv"), _scan(db, project.id, "sep.csv")
    for scan in (august, september):
        _iface(db, project.id, rescanned, scan, "https://10.44.0.1:8443/")
        _iface(db, project.id, rescanned, scan, "https://10.44.0.1:8443/login")
    # Another tool on a URL already seen is its own interface.
    _iface(db, project.id, rescanned, september, "https://10.44.0.1:8443/", source="httpx")
    db.commit()
    return rescanned, bare


def test_host_detail_counts_distinct_interfaces(client, db_session, test_project):
    rescanned, bare = _seed(db_session, test_project)
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{rescanned.id}")
    assert r.status_code == 200, r.text
    assert r.json()["web_interface_count"] == 3  # 5 rows: 2 URLs x 2 scans + 1 httpx

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{bare.id}")
    assert r.json()["web_interface_count"] == 0


def test_host_list_agrees_with_the_detail(client, db_session, test_project):
    _seed(db_session, test_project)
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/?limit=50")
    assert r.status_code == 200, r.text
    rows = {h["ip_address"]: h for h in r.json()["items"]}
    assert rows["10.44.0.1"]["web_interface_count"] == 3
    assert rows["10.44.0.2"]["web_interface_count"] == 0


# --- when was it OBSERVED (v2.364.0, code review finding 21) -----------------

def test_web_interfaces_say_when_they_were_observed_not_when_the_row_was_written(client, db_session, test_project):
    """``first_seen`` / ``last_seen`` are the database's clock (``last_seen``
    moves on any update), and the inspector now shows ONE row per (tool, URL) —
    ranking by them let an old scan imported later become "the latest".  The
    response carries the scan's own time when the tool recorded one."""
    from datetime import datetime

    host = _host(db_session, test_project.id, "10.44.1.1")
    timed = models.Scan(filename="aug.xml", project_id=test_project.id,
                        start_time=datetime(2026, 8, 7, 17, 0), end_time=datetime(2026, 8, 7, 18, 0),
                        time_source="tool_run")
    wall_clock = models.Scan(filename="clock.gnmap", project_id=test_project.id,
                             start_time=datetime(2026, 8, 9, 9, 30), time_source="tool_clock")
    untimed = models.Scan(filename="list.txt", project_id=test_project.id)
    db_session.add_all([timed, wall_clock, untimed])
    db_session.flush()
    _iface(db_session, test_project.id, host, timed, "https://10.44.1.1/a")
    _iface(db_session, test_project.id, host, wall_clock, "https://10.44.1.1/b")
    _iface(db_session, test_project.id, host, untimed, "https://10.44.1.1/c")
    db_session.commit()

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}/web-interfaces")
    assert r.status_code == 200, r.text
    by_url = {w["url"].rsplit("/", 1)[1]: w for w in r.json()}

    # The tool's own END time, as an absolute instant (UTC offset present).
    assert by_url["a"]["observed_at_basis"] == "scan"
    assert by_url["a"]["observed_at"].startswith("2026-08-07T18:00:00")
    assert by_url["a"]["observed_at"].endswith(("Z", "+00:00"))
    assert by_url["a"]["scan_filename"] == "aug.xml"
    # A zone-less scanner wall clock stays naive — never converted (scan_time rule).
    assert by_url["b"]["observed_at_basis"] == "scan"
    assert by_url["b"]["observed_at"] == "2026-08-09T09:30:00"
    # No scan time at all: all that is known is the import, and it says so.
    assert by_url["c"]["observed_at_basis"] == "import"
    assert by_url["c"]["observed_at"] == by_url["c"]["first_seen"]
