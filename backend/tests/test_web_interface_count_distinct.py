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
