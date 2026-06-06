"""Regression test for v2.90.1 — tool-ready ip-list export OOM fix.

Field report: a project with 42k hosts hit 502 (backend OOM-killed)
when generating an ip-list export.  Root cause was the unconditional
``selectinload(Host.ports).selectinload(Port.scripts)`` +
``selectinload(Host.host_scripts)`` on the tool-ready query — even
for IP-only formats (ip-list / nmap / metasploit / masscan) that
walk only ``host.ip_address``.  At 42k hosts × N ports × scripts
the eager-load hydrated gigabytes of ORM objects into a 2GB-cap
worker.

Fix: only eager-load the port/script graph when the format actually
consumes it (host-port / nuclei / json+include_ports).  The IP-only
formats now run with no relationship loads.

These tests are smoke-coverage: confirm the endpoint still returns
the right body for the IP-only path, and confirm the port-bearing
path still loads ports.  Memory-blowup is verified at deployment
scale; pinning a representative shape here.
"""
from __future__ import annotations

from app.db import models


def _seed_host(db_session, project, *, ip: str, hostname: str | None = None):
    host = models.Host(
        project_id=project.id,
        ip_address=ip,
        hostname=hostname,
        state="up",
    )
    db_session.add(host)
    db_session.flush()
    return host


def test_ip_list_format_returns_one_ip_per_line(client, db_session, test_project):
    """ip-list with 3 hosts emits a 3-line response — confirms the
    skip-eager-load path still produces the same body as before."""
    _seed_host(db_session, test_project, ip="10.0.0.1")
    _seed_host(db_session, test_project, ip="10.0.0.2")
    _seed_host(db_session, test_project, ip="10.0.0.3")
    db_session.flush()

    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/tool-ready/ip-list",
    )
    assert r.status_code == 200, r.text
    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    assert set(lines) == {"10.0.0.1", "10.0.0.2", "10.0.0.3"}
    # Total / Returned headers should reflect 3 / 3 (no cap hit).
    assert r.headers["X-Tool-Ready-Total"] == "3"
    assert r.headers["X-Tool-Ready-Returned"] == "3"
    assert "X-Tool-Ready-Truncated" not in r.headers


def test_nmap_format_skips_port_eager_load(client, db_session, test_project):
    """nmap format is space-separated IPs only — should produce the
    same shape as ip-list (one Host row, no port-graph load)."""
    _seed_host(db_session, test_project, ip="10.0.0.1")
    _seed_host(db_session, test_project, ip="10.0.0.2")
    db_session.flush()
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/tool-ready/nmap",
    )
    assert r.status_code == 200
    assert "10.0.0.1" in r.text
    assert "10.0.0.2" in r.text


def test_host_port_format_still_loads_ports(client, db_session, test_project):
    """The port-bearing format must still hydrate ports — confirm the
    fix didn't accidentally break the formats that need the join."""
    host = _seed_host(db_session, test_project, ip="10.0.0.7")
    db_session.add(
        models.Port(
            host_id=host.id, port_number=22, protocol="tcp",
            state="open", service_name="ssh",
        )
    )
    db_session.add(
        models.Port(
            host_id=host.id, port_number=80, protocol="tcp",
            state="open", service_name="http",
        )
    )
    db_session.flush()
    r = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/tool-ready/host-port",
    )
    assert r.status_code == 200
    lines = set(ln for ln in r.text.splitlines() if ln.strip())
    assert lines == {"10.0.0.7:22", "10.0.0.7:80"}
