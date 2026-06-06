"""Regression tests for v2.86.9 scan-host filter additions.

``GET /projects/{pid}/hosts/scan/{scan_id}`` previously accepted only
``state`` + skip + limit.  v2.86.9 adds:

  * ``search`` — substring match on IP / hostname / OS name (server-
    side, LIKE-escaped).
  * ``port`` — restrict to hosts that have at least one Port row at
    the given port number on this scan.

The reviewer asked for ``severity`` too, but that depends on the
Vulnerability join shape — deferred to a follow-up.
"""
from __future__ import annotations

from app.db import models


def _seed_scan_with_hosts(db_session, project_id):
    """One scan, four hosts:

    * 10.0.0.1 / web.example  — port 80, OS Linux
    * 10.0.0.2 / app.example  — port 22, OS Linux
    * 10.0.0.3 / db.example   — port 5432, OS Windows
    * 10.0.0.4 (no hostname)  — port 80, OS Linux
    """
    scan = models.Scan(project_id=project_id, filename="fix.xml", scan_type="nmap")
    db_session.add(scan)
    db_session.flush()

    def _host(ip, hostname, os_name, port_num):
        h = models.Host(
            project_id=project_id, ip_address=ip, hostname=hostname,
            os_name=os_name, state="up",
        )
        db_session.add(h)
        db_session.flush()
        db_session.add(models.Port(
            host_id=h.id, port_number=port_num,
            protocol="tcp", state="open",
        ))
        db_session.add(models.HostScanHistory(
            host_id=h.id, scan_id=scan.id, state_at_scan="up",
        ))
        return h

    _host("10.0.0.1", "web.example", "Linux", 80)
    _host("10.0.0.2", "app.example", "Linux", 22)
    _host("10.0.0.3", "db.example",  "Windows", 5432)
    _host("10.0.0.4", None,           "Linux", 80)
    db_session.flush()
    return scan


def test_scan_host_search_matches_hostname(client, db_session, test_project):
    scan = _seed_scan_with_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/scan/{scan.id}",
        params={"search": "web"},
    )
    assert r.status_code == 200, r.text
    ips = {h["ip_address"] for h in r.json()}
    assert ips == {"10.0.0.1"}, ips


def test_scan_host_search_matches_ip_fragment(client, db_session, test_project):
    scan = _seed_scan_with_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/scan/{scan.id}",
        params={"search": "10.0.0.2"},
    )
    ips = {h["ip_address"] for h in r.json()}
    assert ips == {"10.0.0.2"}, ips


def test_scan_host_search_matches_os(client, db_session, test_project):
    scan = _seed_scan_with_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/scan/{scan.id}",
        params={"search": "Windows"},
    )
    ips = {h["ip_address"] for h in r.json()}
    assert ips == {"10.0.0.3"}, ips


def test_scan_host_port_filter(client, db_session, test_project):
    scan = _seed_scan_with_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/scan/{scan.id}",
        params={"port": 80},
    )
    ips = {h["ip_address"] for h in r.json()}
    assert ips == {"10.0.0.1", "10.0.0.4"}, ips


def test_scan_host_search_and_port_combine_as_and(client, db_session, test_project):
    """When both filters are set, results must satisfy BOTH (AND)."""
    scan = _seed_scan_with_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/scan/{scan.id}",
        params={"search": "web", "port": 80},
    )
    ips = {h["ip_address"] for h in r.json()}
    assert ips == {"10.0.0.1"}, "search AND port should intersect, not union"


def test_scan_host_rejects_invalid_port(client, db_session, test_project):
    scan = _seed_scan_with_hosts(db_session, test_project.id)
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/scan/{scan.id}",
        params={"port": 99999},
    )
    assert r.status_code == 422, r.text
