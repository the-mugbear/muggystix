"""``version:`` and ``path:`` treat LIKE wildcards in the value as literal
characters (review 2026-09-23 R12), like ``service:`` always did."""
from __future__ import annotations

from app.db import models


def _hosts(db_session, project):
    scan = models.Scan(project_id=project.id, filename="d.json", scan_type="content_discovery")
    db_session.add(scan)
    db_session.flush()
    out = {}
    for ip, product, path in (("10.61.0.1", "App_1", "/admin_x"), ("10.61.0.2", "AppZ1", "/adminYx")):
        h = models.Host(project_id=project.id, ip_address=ip, state="up")
        db_session.add(h)
        db_session.flush()
        db_session.add(models.Port(host_id=h.id, port_number=80, protocol="tcp", state="open",
                                   service_name="http", service_product=product))
        db_session.add(models.WebPath(project_id=project.id, host_id=h.id, scan_id=scan.id, source="gobuster",
                                      url=f"http://{ip}{path}", path=path))
        out[ip] = h
    db_session.commit()
    return out


def _ips(client, project, q):
    r = client.get(f"/api/v1/projects/{project.id}/hosts/", params={"q": q})
    assert r.status_code == 200, r.text
    body = r.json()
    return sorted(h["ip_address"] for h in (body["items"] if isinstance(body, dict) else body))


def test_underscore_and_percent_are_literal(client, db_session, test_project):
    _hosts(db_session, test_project)
    assert _ips(client, test_project, 'path:"/admin_x"') == ["10.61.0.1"]
    assert _ips(client, test_project, 'version:"App_1"') == ["10.61.0.1"]
    assert _ips(client, test_project, 'path:"%dmin"') == []
