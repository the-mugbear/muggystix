"""A scope's target files, and the web-interface page past host detail's cap.

``/agent/scopes/{scope_id}/hosts.ndjson|live-hosts.txt|web-targets.txt`` are
what an agent redirects to a file and pipes into its scanners (``nmap -iL``,
``httpx -l``).  Ported from ``test_recon_bulk_download.py`` and
``test_recon_planning_contract.py`` (deleted with recon runs in v2.433.0,
which took these guards with them although the files survived):

* the files hold the scope's hosts and nothing else — a host from another
  scope in a scanner's target file is a scan outside what the client
  authorised;
* ``live-hosts.txt`` is a usable ``-iL`` list, in IP order, and one bad
  address row cannot take the download down;
* ``hosts.ndjson`` is complete and carries each host's ports;
* web targets say https for a TLS-wrapped service whatever its port number.

Ported from ``test_mcp_review_followups.py``: host detail caps its web
interfaces, and ``/assist/hosts/{id}/web-interfaces`` (MCP
``assist_list_host_web_interfaces``) reaches the rest.
"""
import json

import pytest

from app.db import models
from app.db.models import HostScanHistory, Scan, Scope, Subnet, WebInterface


def _key(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


def _scope(db, project, name, cidr):
    scope = Scope(name=name, description="fixture", project_id=project.id)
    db.add(scope)
    db.flush()
    db.add(Subnet(scope_id=scope.id, cidr=cidr))
    db.commit()
    return scope


def _seed_hosts(db, project, scope, ips, *, ports=(22, 80)):
    """Hosts wired the way ingestion wires them: mapped into the scope's
    subnet, with open ports and a scan history row."""
    subnet = db.query(Subnet).filter(Subnet.scope_id == scope.id).first()
    scan = Scan(filename="nmap.xml", scan_type="nmap", project_id=project.id)
    db.add(scan)
    db.flush()
    for ip in ips:
        host = models.Host(ip_address=ip, state="up", project_id=project.id)
        db.add(host)
        db.flush()
        db.add(models.HostSubnetMapping(host_id=host.id, subnet_id=subnet.id))
        for p in ports:
            db.add(models.Port(
                host_id=host.id, port_number=p, protocol="tcp", state="open",
                service_name={22: "ssh", 80: "http", 443: "https"}.get(p, "x"),
            ))
        db.add(HostScanHistory(host_id=host.id, scan_id=scan.id))
    db.commit()
    return scan


@pytest.fixture
def scope(db_session, test_project):
    return _scope(db_session, test_project, "dl-scope", "10.99.1.0/24")


def _get(client, headers, scope_id, name):
    return client.get(f"/api/v1/agent/scopes/{scope_id}/{name}", headers=headers)


# ---------------------------------------------------------------------------
# The files hold the scope and nothing else
# ---------------------------------------------------------------------------

def test_downloads_keep_the_scope_bounding(client, db_session, test_project, scope):
    other = _scope(db_session, test_project, "other-scope", "10.55.0.0/24")
    _seed_hosts(db_session, test_project, other, ["10.55.0.9"])
    _seed_hosts(db_session, test_project, scope, ["10.99.1.9"])
    headers = _key(client, test_project)

    for name in ("live-hosts.txt", "hosts.ndjson", "web-targets.txt"):
        body = _get(client, headers, scope.id, name).text
        assert "10.99.1.9" in body, name
        assert "10.55.0.9" not in body, f"out-of-scope host leaked into {name}"


def test_a_scope_of_another_project_is_not_found(client, db_session, test_project):
    """Nothing binds a key to a scope any more, so this filter is the only
    thing between a key and another project's hosts."""
    from app.db.models_project import Project
    elsewhere = Project(name="elsewhere", slug="elsewhere")
    db_session.add(elsewhere)
    db_session.commit()
    foreign = _scope(db_session, elsewhere, "foreign", "10.66.0.0/24")
    _seed_hosts(db_session, elsewhere, foreign, ["10.66.0.1"])
    headers = _key(client, test_project)

    for name in ("live-hosts.txt", "hosts.ndjson", "web-targets.txt", "subnets"):
        resp = _get(client, headers, foreign.id, name)
        assert resp.status_code == 404, (name, resp.status_code)
        assert "10.66.0.1" not in resp.text


# ---------------------------------------------------------------------------
# live-hosts.txt is a usable -iL list
# ---------------------------------------------------------------------------

def test_live_hosts_download_is_a_usable_target_file(client, db_session, test_project, scope):
    _seed_hosts(db_session, test_project, scope, ["10.99.1.3", "10.99.1.1", "10.99.1.2"])
    resp = _get(client, _key(client, test_project), scope.id, "live-hosts.txt")
    assert resp.status_code == 200
    assert resp.text == "10.99.1.1\n10.99.1.2\n10.99.1.3\n"


def test_hosts_sort_numerically_not_lexicographically(client, db_session, test_project, scope):
    """IP order, not string order (which puts .100 before .2)."""
    _seed_hosts(db_session, test_project, scope, ["10.99.1.100", "10.99.1.2", "10.99.1.20"])
    resp = _get(client, _key(client, test_project), scope.id, "live-hosts.txt")
    assert resp.text.split() == ["10.99.1.2", "10.99.1.20", "10.99.1.100"]


def test_an_unparseable_ip_address_does_not_take_down_the_download(
    client, db_session, test_project, scope,
):
    """The ordering casts to inet guarded by pg_input_is_valid.  The column
    has carried non-addresses (``localhost`` from httpx TLS-SAN expansion); a
    bare cast would raise on that row and 500 the whole file."""
    scan = _seed_hosts(db_session, test_project, scope, ["10.99.1.5"])
    subnet = db_session.query(Subnet).filter(Subnet.scope_id == scope.id).first()
    bad = models.Host(ip_address="localhost", state="up", project_id=test_project.id)
    db_session.add(bad)
    db_session.flush()
    db_session.add(models.HostSubnetMapping(host_id=bad.id, subnet_id=subnet.id))
    db_session.add(HostScanHistory(host_id=bad.id, scan_id=scan.id))
    db_session.commit()

    resp = _get(client, _key(client, test_project), scope.id, "live-hosts.txt")
    assert resp.status_code == 200, resp.text
    lines = resp.text.split()
    assert "10.99.1.5" in lines and "localhost" in lines
    # Real addresses sort ahead of the unparseable bucket.
    assert lines.index("10.99.1.5") < lines.index("localhost")


# ---------------------------------------------------------------------------
# hosts.ndjson is complete; web targets are right
# ---------------------------------------------------------------------------

def test_hosts_ndjson_streams_every_host_one_per_line(client, db_session, test_project, scope):
    ips = [f"10.99.1.{i}" for i in range(1, 41)]
    _seed_hosts(db_session, test_project, scope, ips)
    resp = _get(client, _key(client, test_project), scope.id, "hosts.ndjson")
    assert resp.status_code == 200
    lines = [line for line in resp.text.splitlines() if line.strip()]
    assert len(lines) == len(ips), "every host, uncapped"
    first = json.loads(lines[0])
    assert {"host_id", "ip_address", "open_ports", "services"} <= set(first)
    assert first["open_ports"], "per-port detail must survive the streaming path"


def test_web_targets_download_yields_urls(client, db_session, test_project, scope):
    _seed_hosts(db_session, test_project, scope, ["10.99.1.4"], ports=(80, 443))
    resp = _get(client, _key(client, test_project), scope.id, "web-targets.txt")
    assert resp.status_code == 200
    assert set(resp.text.split()) == {"http://10.99.1.4/", "https://10.99.1.4/"}


def _brief(port, service, tunnel=None):
    from app.services.scope_targets_service import ScopeHostBrief, ScopePortBrief
    return ScopeHostBrief(
        host_id=1, ip_address="192.168.7.245", hostname=None,
        open_ports=[ScopePortBrief(port=port, service=service, tunnel=tunnel)],
    )


def test_an_ssl_http_service_derives_an_https_url():
    """nmap's XML reports `ssl/http` as name="http" tunnel="ssl"; the derived
    target must be https, not http://…:3000/."""
    from app.services.scope_targets_service import web_targets_from_hosts

    targets = web_targets_from_hosts([_brief(3000, "http", tunnel="ssl")])
    assert len(targets) == 1
    assert targets[0].protocol == "https"
    assert targets[0].url == "https://192.168.7.245:3000/"


def test_observed_service_beats_the_port_number_guess():
    from app.services.scope_targets_service import web_targets_from_hosts

    assert web_targets_from_hosts([_brief(8080, "http", tunnel="ssl")])[0].protocol == "https"
    # And the reverse: plaintext on 443 is reported as what it is.
    assert web_targets_from_hosts([_brief(443, "http")])[0].protocol == "http"


def test_the_port_table_still_covers_unprobed_ports():
    """A discovery sweep with no -sV still yields usable targets."""
    from app.services.scope_targets_service import web_targets_from_hosts

    targets = web_targets_from_hosts([_brief(443, None)])
    assert targets[0].protocol == "https"
    assert targets[0].url == "https://192.168.7.245/"


def test_the_parser_keeps_the_tunnel_attribute(db_session):
    """Root cause of the above: without the stored tunnel every web target
    falls back to guessing from the port number."""
    from lxml import etree
    from app.parsers.nmap_parser import NmapXMLParser

    port_xml = etree.fromstring(
        b'<port protocol="tcp" portid="3000">'
        b'<state state="open" reason="syn-ack"/>'
        b'<service name="http" product="nginx" tunnel="ssl" method="probed" conf="10"/>'
        b'</port>'
    )
    data = NmapXMLParser(db_session)._extract_port_data(port_xml)
    assert data["service_tunnel"] == "ssl"
    assert data["service_name"] == "http"


# ---------------------------------------------------------------------------
# Web interfaces past host detail's cap
# ---------------------------------------------------------------------------

def _host_with_interfaces(db, project, count):
    host = models.Host(project_id=project.id, ip_address="10.97.9.9", state="up")
    scan = Scan(project_id=project.id, filename="w.json", scan_type="httpx", tool_name="httpx")
    db.add_all([host, scan])
    db.flush()
    for i in range(count):
        db.add(WebInterface(
            host_id=host.id, scan_id=scan.id, project_id=project.id, source="httpx",
            port=8000 + i, url=f"http://10.97.9.9:{8000 + i}/",
            screenshot_path="shots/x.png" if i == count - 1 else None,
        ))
    db.commit()
    return host


def test_web_interfaces_page_reaches_what_host_detail_capped(client, db_session, test_project):
    from app.api.v1.endpoints.agent_assist import _WEB_INTERFACE_CAP
    total = _WEB_INTERFACE_CAP + 3
    host = _host_with_interfaces(db_session, test_project, total)
    headers = _key(client, test_project)

    detail = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=headers).json()
    assert detail["web_interfaces_truncated"] is True

    seen, offset = [], 0
    while True:
        page = client.get(
            f"/api/v1/agent/assist/hosts/{host.id}/web-interfaces", headers=headers,
            params={"limit": _WEB_INTERFACE_CAP, "offset": offset},
        )
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["total"] == total
        seen.extend(w["url"] for w in body["items"])
        if not body["has_more"]:
            break
        offset += len(body["items"])
    assert len(seen) == total and len(set(seen)) == total
    # The screenshotted interface sorts first and carries its download path.
    first = client.get(
        f"/api/v1/agent/assist/hosts/{host.id}/web-interfaces", headers=headers,
        params={"limit": 1},
    ).json()["items"][0]
    assert first["screenshot_download_path"].endswith("/screenshot")


def test_web_interfaces_page_is_an_mcp_tool(client, db_session, test_project):
    host = _host_with_interfaces(db_session, test_project, 3)
    headers = _key(client, test_project)

    def mcp(method, **params):
        r = client.post("/api/v1/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
        })
        assert r.status_code == 200, r.text
        return r.json()

    names = {t["name"] for t in mcp("tools/list")["result"]["tools"]}
    assert "assist_list_host_web_interfaces" in names
    out = mcp("tools/call", name="assist_list_host_web_interfaces",
              arguments={"host_id": host.id, "limit": 2})
    data = out["result"]["structuredContent"]
    assert data["total"] == 3 and data["has_more"] is True and len(data["items"]) == 2
    # Project-bounded like every assist read.
    other = mcp("tools/call", name="assist_list_host_web_interfaces",
                arguments={"host_id": host.id + 100000})
    assert other["result"]["isError"] is True
