"""Review 2026-10-10 — three Hosts reads that kept a second copy of a rule.

* H8  — the tool-ready export narrowed ports with its own Python copy of the
  port filter; ``search`` stripped the ports it should emit.
* H17 — the Hosts row's site did not inherit (a second site rule).
* H18 — the host DNS card stated the length of a preview as the total.
"""
from app.db import models
from app.services.default_scope import get_or_create_default_scope
from app.services.subnet_insight_service import resolve_host_locations


def _host(db, project, ip, ports=()):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    for number, state, service in ports:
        db.add(models.Port(
            host_id=host.id, port_number=number, protocol="tcp", state=state, service_name=service,
        ))
    db.flush()
    return host


# --------------------------------------------------------------------------- H8

def _export(client, project, fmt, **params):
    resp = client.get(f"/api/v1/projects/{project.id}/hosts/tool-ready/{fmt}", params=params)
    assert resp.status_code == 200, resp.text
    return resp


def _lines(client, project, fmt, **params):
    return [ln for ln in _export(client, project, fmt, **params).text.splitlines() if ln.strip()]


def test_a_search_that_matched_the_address_keeps_the_hosts_ports(client, db_session, test_project):
    """``search=10.0.5`` matched the ADDRESS; the export then kept only ports
    whose service text contains "10.0.5" — none — and the file was empty
    beside ``X-Tool-Ready-Total: 1``."""
    _host(db_session, test_project, "10.0.5.9", [(443, "open", "https"), (80, "open", "http")])
    _host(db_session, test_project, "10.0.6.1", [(443, "open", "https")])
    db_session.commit()

    resp = _export(client, test_project, "web-targets", search="10.0.5")
    assert resp.headers["X-Tool-Ready-Total"] == "1"
    assert sorted(resp.text.splitlines()) == ["http://10.0.5.9", "https://10.0.5.9"]
    assert sorted(_lines(client, test_project, "host-port", search="10.0.5")) == ["10.0.5.9:443", "10.0.5.9:80"]


def test_services_narrows_by_the_identified_service_not_a_port_table(client, db_session, test_project):
    """``services=http`` is the service the scanner identified, on any port —
    the host filter's rule.  The export still added the "standard" ports of
    the name (ssh identified on 8080 was emitted as a web target)."""
    _host(db_session, test_project, "10.0.7.1", [
        (80, "open", "http"),
        (8080, "open", "ssh"),          # in the old http port table, not http
        (9000, "open", "http-alt"),     # http by name, on no "standard" port
        (8000, "closed", "http"),       # http, but not open
    ])
    db_session.commit()

    assert sorted(_lines(client, test_project, "host-port", services="http")) == ["10.0.7.1:80", "10.0.7.1:9000"]
    assert _lines(client, test_project, "web-targets", services="http") == ["http://10.0.7.1"]
    # ports= and port_states= are the same conditions the list uses.
    assert _lines(client, test_project, "host-port", ports="8080") == ["10.0.7.1:8080"]
    assert _lines(client, test_project, "host-port", has_open_ports="true", ports="8000") == []


def test_a_query_chooses_hosts_and_narrows_no_port(client, db_session, test_project):
    """``q=`` (like ``search``) selects HOSTS; the ports emitted are the
    host's.  Only the discrete port filters narrow them."""
    _host(db_session, test_project, "10.0.8.1", [(443, "open", "https"), (80, "open", "http")])
    _host(db_session, test_project, "10.0.8.2", [(80, "open", "http")])
    db_session.commit()

    assert sorted(_lines(client, test_project, "web-targets", q="port:443")) == [
        "http://10.0.8.1", "https://10.0.8.1",
    ]
    # ... and the discrete filter beside it does narrow.
    assert _lines(client, test_project, "web-targets", q="port:443", ports="443") == ["https://10.0.8.1"]


# -------------------------------------------------------------------------- H17

def _scoped(db, project, user):
    """10.0.0.0/16 "DC-East" ⊃ 10.0.5.0/24 (no site); 10.9.0.0/16 "DC-West"
    ⊃ 10.9.1.0/24 "Lab"; one host mapped nowhere."""
    scope = get_or_create_default_scope(db, project.id, user.id)
    subnets = {}
    for cidr, site in (
        ("10.0.0.0/16", "DC-East"), ("10.0.5.0/24", None),
        ("10.9.0.0/16", "DC-West"), ("10.9.1.0/24", "Lab"),
    ):
        subnet = models.Subnet(scope_id=scope.id, cidr=cidr, site=site)
        db.add(subnet)
        subnets[cidr] = subnet
    db.flush()
    hosts = {}
    for ip, cidrs in (
        ("10.0.5.9", ("10.0.0.0/16", "10.0.5.0/24")),   # the review's inheriting case
        ("10.0.9.1", ("10.0.0.0/16",)),
        ("10.9.1.7", ("10.9.0.0/16", "10.9.1.0/24")),
        ("10.9.2.2", ("10.9.0.0/16",)),
        ("172.16.0.1", ()),
    ):
        host = _host(db, project, ip)
        hosts[ip] = host
        for cidr in cidrs:
            db.add(models.HostSubnetMapping(host_id=host.id, subnet_id=subnets[cidr].id))
    db.commit()
    return hosts


def test_the_hosts_row_site_is_the_one_site_rule(client, db_session, test_project, test_user):
    hosts = _scoped(db_session, test_project, test_user)
    rows = {
        h["ip_address"]: h
        for h in client.get(f"/api/v1/projects/{test_project.id}/hosts/").json()["items"]
    }
    whole = resolve_host_locations(db_session, test_project.id)

    # The row is the rule, host by host — site AND subnet.
    for ip, host in hosts.items():
        loc = whole.get(host.id)
        assert rows[ip]["primary_site"] == (loc["site"] if loc else None), ip
        assert rows[ip]["primary_subnet"] == (loc["cidr"] if loc else None), ip

    # The case the two copies disagreed on: an unlabelled /24 inside a sited /16.
    assert rows["10.0.5.9"]["primary_subnet"] == "10.0.5.0/24"
    assert rows["10.0.5.9"]["primary_site"] == "DC-East"        # was None
    # ... and it is the site the filter lists the host under.
    listed = client.get(
        f"/api/v1/projects/{test_project.id}/hosts/", params={"q": 'site:"DC-East"'},
    ).json()["items"]
    assert "10.0.5.9" in {h["ip_address"] for h in listed}
    # Where the two always agreed (old == new).
    assert (rows["10.0.9.1"]["primary_subnet"], rows["10.0.9.1"]["primary_site"]) == ("10.0.0.0/16", "DC-East")
    assert (rows["10.9.1.7"]["primary_subnet"], rows["10.9.1.7"]["primary_site"]) == ("10.9.1.0/24", "Lab")
    assert (rows["10.9.2.2"]["primary_subnet"], rows["10.9.2.2"]["primary_site"]) == ("10.9.0.0/16", "DC-West")
    assert (rows["172.16.0.1"]["primary_subnet"], rows["172.16.0.1"]["primary_site"]) == (None, None)


def test_locations_for_some_hosts_equal_the_whole_projects(db_session, test_project, test_user):
    """``host_ids=`` is the same rule over fewer rows: for any subset it
    answers exactly what the whole-project call answers for those hosts."""
    hosts = _scoped(db_session, test_project, test_user)
    whole = resolve_host_locations(db_session, test_project.id)
    ids = [h.id for h in hosts.values()]
    for subset in (ids, ids[:1], ids[1:3], [hosts["172.16.0.1"].id], []):
        some = resolve_host_locations(db_session, test_project.id, host_ids=subset)
        assert some == {hid: whole[hid] for hid in subset if hid in whole}
    # Another project's host id answers nothing.
    assert resolve_host_locations(db_session, test_project.id + 999, host_ids=ids) == {}


# ------------------------------------------------ nitpick: confirming a hostname

def test_an_agent_confirming_the_name_a_host_has_pins_it(client, db_session, test_project):
    """Sending the hostname the host already has changed no value, so nothing
    was committed — the "operator" source with it — and the next scan could
    rename a host its operator had just confirmed."""
    host = models.Host(project_id=test_project.id, ip_address="10.0.30.1", state="up",
                       hostname="web01.corp.example", hostname_source="scanner")
    db_session.add(host)
    db_session.commit()
    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    key = {"X-API-Key": started.json()["api_key"]}

    same = client.patch(f"/api/v1/agent/hosts/{host.id}", headers=key, json={"hostname": "web01.corp.example"})
    assert same.status_code == 200, same.text
    assert same.json()["changed"] == []
    db_session.rollback()          # whatever the route left uncommitted is gone
    db_session.expire_all()
    assert db_session.get(models.Host, host.id).hostname_source == "operator"

    # A real change, and clearing, behave as before.
    renamed = client.patch(f"/api/v1/agent/hosts/{host.id}", headers=key, json={"hostname": "web02.corp.example"})
    assert renamed.json()["changed"] == ["hostname"]
    cleared = client.patch(f"/api/v1/agent/hosts/{host.id}", headers=key, json={"hostname": ""})
    assert cleared.json()["changed"] == ["hostname"]
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(models.Host, host.id).hostname_source is None


# -------------------------------------------------------------------------- H18

def test_the_dns_card_total_is_every_record_not_the_preview(client, db_session, test_project):
    """205 records for one address, default limit 200: the card printed
    "200 records" and summarised the resolvers and types of the 200."""
    host = _host(db_session, test_project, "10.0.20.1")
    for i in range(204):
        db_session.add(models.DNSRecord(
            project_id=test_project.id, domain=f"a{i:03d}.example.com", record_type="A",
            value="10.0.20.1", resolver_name="r-one",
        ))
    # Sorts last (record type, then domain): outside the first 200.
    db_session.add(models.DNSRecord(
        project_id=test_project.id, domain="zz.example.com", record_type="PTR",
        value="10.0.20.1", resolver_name="r-two",
    ))
    db_session.add(models.DNSRecord(
        project_id=test_project.id, domain="other.example.com", record_type="A", value="10.0.20.2",
    ))
    db_session.commit()

    url = f"/api/v1/projects/{test_project.id}/hosts/{host.id}/dns-records"
    body = client.get(url).json()
    assert len(body["items"]) == 200
    assert body["total"] == 205
    assert body["resolvers"] == ["r-one", "r-two"]
    assert body["record_types"] == ["A", "PTR"]
    assert body["project_total"] == 206

    small = client.get(url, params={"limit": 3}).json()
    assert (len(small["items"]), small["total"]) == (3, 205)
    everything = client.get(url, params={"limit": 2000}).json()
    assert (len(everything["items"]), everything["total"]) == (205, 205)
    assert everything["record_types"] == ["A", "PTR"]
