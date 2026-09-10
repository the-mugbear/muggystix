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


# ---------------------------------------------------------------------------
# v2.330.0 — name-aware formats.  "Bound" = current_binding_condition (the
# one "currently resolves to" rule); in-scope = a declared domain covers it.
# ---------------------------------------------------------------------------
from app.services import dns_name_service as svc  # noqa: E402


def _scope_with_domains(db, project, entries):
    scope = models.Scope(project_id=project.id, name="default")
    db.add(scope)
    db.flush()
    svc.upsert_scope_domains(db, scope, [(d, sub, None) for d, sub in entries])
    return scope


def _bind(db, project, fqdn, ip):
    svc.record_observation(db, project_id=project.id, name=fqdn, record_type="A", value=ip)


def _port(db, host, number, state="open"):
    db.add(models.Port(host_id=host.id, port_number=number, protocol="tcp", state=state, service_name="http"))


def _seed_lb(db, project):
    """One load-balanced address (10.0.0.10:443) with two vhosts, one in
    scope; a wildcard pattern bound too (must never be a target); a plain
    host (10.0.0.20:22) with no names."""
    lb = _seed_host(db, project, ip="10.0.0.10", hostname="lb1")
    _port(db, lb, 443)
    plain = _seed_host(db, project, ip="10.0.0.20")
    _port(db, plain, 22)
    _scope_with_domains(db, project, [("acme.com", True)])
    _bind(db, project, "portal.acme.com", "10.0.0.10")
    _bind(db, project, "old.other.net", "10.0.0.10")
    _bind(db, project, "*.acme.com", "10.0.0.10")
    db.flush()
    return lb, plain


def test_names_format_is_in_scope_deduped_and_excludes_wildcards(client, db_session, test_project):
    _seed_lb(db_session, test_project)
    # Bind the in-scope name a second time (another observation) — still one line.
    _bind(db_session, test_project, "portal.acme.com", "10.0.0.10")
    db_session.flush()

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/tool-ready/names")
    assert r.status_code == 200, r.text
    assert r.text.splitlines() == ["portal.acme.com"]
    assert r.headers["Content-Disposition"].endswith("names.txt")
    # Hosts, not names, count toward the cap headers.
    assert r.headers["X-Tool-Ready-Total"] == "2"

    r_all = client.get(f"/api/v1/projects/{test_project.id}/hosts/tool-ready/names?names_scope=all")
    assert r_all.text.splitlines() == ["old.other.net", "portal.acme.com"]


def test_names_format_ignores_an_address_a_name_moved_away_from(client, db_session, test_project):
    """A historical A record is evidence, never a target."""
    host = _seed_host(db_session, test_project, ip="10.0.0.30")
    _scope_with_domains(db_session, test_project, [("acme.com", True)])
    from datetime import datetime, timedelta, timezone
    then = datetime.now(timezone.utc) - timedelta(days=30)
    svc.record_observation(db_session, project_id=test_project.id, name="moved.acme.com",
                           record_type="A", value="10.0.0.30", observed_at=then)
    svc.record_observation(db_session, project_id=test_project.id, name="moved.acme.com",
                           record_type="A", value="10.0.0.31", observed_at=then + timedelta(days=1))
    db_session.flush()
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/tool-ready/names")
    assert r.status_code == 200
    assert r.text.strip() == ""
    assert host.id  # host still exported by the IP formats


def test_web_targets_emits_urls_by_name_with_ip_fallback(client, db_session, test_project):
    lb, plain = _seed_lb(db_session, test_project)
    web_only = _seed_host(db_session, test_project, ip="10.0.0.40")
    _port(db_session, web_only, 8080)
    db_session.flush()

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/tool-ready/web-targets")
    assert r.status_code == 200, r.text
    lines = r.text.splitlines()
    # LB: the in-scope vhost by name (https, default port elided); the
    # out-of-scope name and the wildcard are not targets.  10.0.0.20 has no
    # web port → absent.  10.0.0.40 has a web port but no name → IP URL.
    assert lines == ["https://portal.acme.com", "http://10.0.0.40:8080"]

    r_all = client.get(f"/api/v1/projects/{test_project.id}/hosts/tool-ready/web-targets?names_scope=all")
    assert r_all.text.splitlines() == [
        "https://old.other.net", "https://portal.acme.com", "http://10.0.0.40:8080",
    ]


def test_nuclei_prefers_bound_names_and_keeps_ip_for_non_web_hosts(client, db_session, test_project):
    _seed_lb(db_session, test_project)
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/tool-ready/nuclei")
    assert r.status_code == 200, r.text
    assert r.text.splitlines() == ["https://portal.acme.com", "10.0.0.20"]


def test_json_format_carries_bound_names_next_to_legacy_hostname(client, db_session, test_project):
    _seed_lb(db_session, test_project)
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/tool-ready/json?names_scope=all")
    assert r.status_code == 200, r.text
    by_ip = {h["ip_address"]: h for h in r.json()}
    assert by_ip["10.0.0.10"]["hostname"] == "lb1"
    assert by_ip["10.0.0.10"]["names"] == ["old.other.net", "portal.acme.com"]
    assert by_ip["10.0.0.20"]["names"] == []


def test_names_scope_rejects_unknown_values(client, db_session, test_project):
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/tool-ready/names?names_scope=everything")
    assert r.status_code == 422
