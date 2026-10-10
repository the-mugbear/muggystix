"""``/agent/scope/named-targets.ndjson`` — the project's NAME scope.

Acceptance run 2026-09-30, finding R2: the scope target files are subnet
scope, so a domain-scoped engagement's authorised names (portal/api/admin
vhosts on a shared address that is NOT in subnet scope) were missing from
them — and widening an IP file to that address would authorise every other
name and service on it.  The named export is the separate answer.  These
tests pin what it must and must not say:

* a name is listed only because one of THIS scope's domain rules covers it —
  a co-hosted name, a certificate SAN or a wildcard pattern is not;
* each address is the name's CURRENT resolution (latest A/AAAA batch), and
  says whether the address is itself in subnet scope;
* a name with no address is listed as unresolved, with the reason;
* web interfaces reached as the name are carried;
* another project's names never appear (the key's project decides the scope —
  no route names one), and the file has the bulk-export floor.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import models
from app.db.models import Scan, Scope, Subnet, WebInterface
from app.db.models_project import Project, ProjectRole
from app.services import dns_name_service as names

from tests.test_agent_role_route_matrix import _key_for, _member

SHARED_IP = "203.0.113.20"
OLD_IP = "203.0.113.10"
INSIDE_IP = "10.99.2.5"
NOW = datetime.now(timezone.utc)


def _key(client, project):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert r.status_code == 201, r.text
    return {"X-API-Key": r.json()["api_key"]}


def _scan(db, project, filename):
    scan = Scan(filename=filename, scan_type="dnsx", project_id=project.id)
    db.add(scan)
    db.flush()
    return scan


@pytest.fixture
def named_scope(db_session, test_project):
    """Subnet 10.99.2.0/24 plus name rules: ``portal.example.test`` (exact),
    ``apps.example.test`` (+subdomains) and a declared ``legacy.example.test``
    nothing has observed."""
    db = db_session
    pid = test_project.id
    scope = Scope(description="fixture", project_id=pid)
    db.add(scope)
    db.flush()
    subnet = Subnet(scope_id=scope.id, cidr="10.99.2.0/24")
    db.add(subnet)
    db.flush()
    names.upsert_scope_domains(db, scope, [
        ("portal.example.test", False, None),
        ("apps.example.test", True, None),
        ("legacy.example.test", False, None),
    ])

    shared = models.Host(ip_address=SHARED_IP, state="up", project_id=pid)
    inside = models.Host(ip_address=INSIDE_IP, state="up", project_id=pid)
    db.add_all([shared, inside])
    db.flush()
    # Only the inside host is in subnet scope; the shared address is not.
    db.add(models.HostSubnetMapping(host_id=inside.id, subnet_id=subnet.id))
    port = models.Port(host_id=shared.id, port_number=8443, protocol="tcp",
                       state="open", service_name="https")
    db.add(port)
    db.flush()

    old_dns = _scan(db, test_project, "dnsx-old.jsonl")
    new_dns = _scan(db, test_project, "dnsx-new.jsonl")
    httpx = _scan(db, test_project, "httpx.jsonl")

    # portal moved: .10 in the old scan, .20 now.
    names.record_observation(db, project_id=pid, name="portal.example.test", record_type="A",
                             value=OLD_IP, scan_id=old_dns.id, observed_at=NOW - timedelta(days=20))
    for fqdn in ("portal.example.test", "api.apps.example.test", "shop.example.test"):
        names.record_observation(db, project_id=pid, name=fqdn, record_type="A",
                                 value=SHARED_IP, scan_id=new_dns.id, observed_at=NOW)
    names.record_observation(db, project_id=pid, name="intranet.apps.example.test", record_type="A",
                             value=INSIDE_IP, scan_id=new_dns.id, observed_at=NOW)
    # A certificate on the shared address names shop (NOT in name scope) and
    # ghost.apps (in scope by rule, but never resolved), plus a wildcard.
    for san in ("shop.example.test", "ghost.apps.example.test", "*.apps.example.test"):
        names.record_observation(db, project_id=pid, name=san, record_type=models.DNS_OBS_CERT,
                                 value=SHARED_IP, scan_id=httpx.id)

    # The portal vhost, reached by name on a non-standard TLS port.
    url = "https://portal.example.test:8443/"
    name_id = names.bind_url_name(db, project_id=pid, url=url, ip_address=SHARED_IP, scan_id=httpx.id)
    db.add(WebInterface(
        scan_id=httpx.id, host_id=shared.id, port_id=port.id, project_id=pid,
        source="httpx", url=url, protocol="https", port=8443, ip_address=SHARED_IP,
        name_id=name_id, status_code=200, title="Portal",
    ))
    # shop has a web interface too — it must not surface through any record.
    shop_url = "https://shop.example.test/"
    shop_id = names.bind_url_name(db, project_id=pid, url=shop_url, ip_address=SHARED_IP, scan_id=httpx.id)
    db.add(WebInterface(
        scan_id=httpx.id, host_id=shared.id, project_id=pid, source="httpx", url=shop_url,
        protocol="https", port=443, ip_address=SHARED_IP, name_id=shop_id, status_code=200,
    ))
    db.commit()
    return {"scope": scope, "shared": shared, "inside": inside}


def _records(client, headers):
    resp = client.get("/api/v1/agent/scope/named-targets.ndjson", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    return {rec["name"]: rec for rec in (json.loads(l) for l in resp.text.splitlines() if l.strip())}


def test_shared_address_lists_authorised_names_only(client, db_session, test_project, named_scope):
    recs = _records(client, _key(client, test_project))

    portal = recs["portal.example.test"]
    assert portal["scope_rule"] == {"domain": "portal.example.test", "include_subdomains": False, "match": "exact"}
    api = recs["api.apps.example.test"]
    assert api["scope_rule"] == {"domain": "apps.example.test", "include_subdomains": True, "match": "subdomain"}

    # Current resolution only: portal moved off .10.
    assert [a["ip_address"] for a in portal["addresses"]] == [SHARED_IP]
    assert portal["addresses"][0]["record_type"] == "A"
    assert portal["addresses"][0]["host_id"] == named_scope["shared"].id
    assert portal["addresses"][0]["in_subnet_scope"] is False
    assert portal["unresolved"] is False and portal["reason"] is None

    # Co-hosted and SAN-only names: not authorised by any rule, not listed.
    assert "shop.example.test" not in recs
    assert "*.apps.example.test" not in recs
    assert not any("shop.example.test" in w["url"] for r in recs.values() for w in r["web"])


def test_address_also_in_subnet_scope_is_flagged(client, db_session, test_project, named_scope):
    recs = _records(client, _key(client, test_project))
    addr = recs["intranet.apps.example.test"]["addresses"]
    assert addr == [{
        "ip_address": INSIDE_IP, "record_type": "A", "last_observed": addr[0]["last_observed"],
        "host_id": named_scope["inside"].id, "in_subnet_scope": True,
    }]


def test_an_address_with_no_host_row_is_judged_by_cidr(client, db_session, test_project, named_scope):
    names.record_observation(db_session, project_id=test_project.id, name="new.apps.example.test",
                             record_type="AAAA", value="2001:db8::5", observed_at=NOW)
    names.record_observation(db_session, project_id=test_project.id, name="lab.apps.example.test",
                             record_type="A", value="10.99.2.77", observed_at=NOW)
    db_session.commit()
    recs = _records(client, _key(client, test_project))
    v6 = recs["new.apps.example.test"]["addresses"][0]
    assert (v6["record_type"], v6["host_id"], v6["in_subnet_scope"]) == ("AAAA", None, False)
    lab = recs["lab.apps.example.test"]["addresses"][0]
    assert (lab["host_id"], lab["in_subnet_scope"]) == (None, True)


def test_unresolved_names_are_listed_with_a_reason(client, db_session, test_project, named_scope):
    recs = _records(client, _key(client, test_project))

    ghost = recs["ghost.apps.example.test"]
    assert ghost["unresolved"] is True and ghost["addresses"] == []
    assert "no A/AAAA" in ghost["reason"] and "CERT" in ghost["reason"]

    legacy = recs["legacy.example.test"]
    assert legacy["unresolved"] is True and legacy["name_id"] is None
    assert "no observation" in legacy["reason"]
    # A rule whose apex was never observed is still named, as itself.
    assert recs["apps.example.test"]["unresolved"] is True


def test_web_evidence_is_carried(client, db_session, test_project, named_scope):
    recs = _records(client, _key(client, test_project))
    web = recs["portal.example.test"]["web"]
    assert len(web) == 1
    w = web[0]
    assert (w["url"], w["scheme"], w["port"], w["ip_address"]) == (
        "https://portal.example.test:8443/", "https", 8443, SHARED_IP,
    )
    assert w["at_current_address"] is True
    assert (w["source"], w["status_code"], w["title"]) == ("httpx", 200, "Portal")
    assert recs["api.apps.example.test"]["web"] == []


def test_ip_files_are_unchanged_by_name_scope(client, db_session, test_project, named_scope):
    """The shared address stays out of the subnet-scope files."""
    headers = _key(client, test_project)
    live = client.get("/api/v1/agent/scope/live-hosts.txt", headers=headers).text
    assert live.split() == [INSIDE_IP]
    web = client.get("/api/v1/agent/scope/web-targets.txt", headers=headers).text
    assert SHARED_IP not in web and "portal" not in web


def test_a_scope_without_domain_rules_is_empty(client, db_session, test_project):
    scope = Scope(description="fixture", project_id=test_project.id)
    db_session.add(scope)
    db_session.commit()
    resp = client.get("/api/v1/agent/scope/named-targets.ndjson",
                      headers=_key(client, test_project))
    assert resp.status_code == 200 and resp.text == ""


def test_a_scope_of_another_project_is_never_read(client, db_session, test_project, named_scope):
    """No route names a scope, so the key's project is the only thing that
    picks one: another project's name rules and observations stay out of the
    file, and this project's are all still there."""
    before = _records(client, _key(client, test_project))
    elsewhere = Project(name="elsewhere-named", slug="elsewhere-named")
    db_session.add(elsewhere)
    db_session.flush()
    foreign = Scope(description="fixture", project_id=elsewhere.id)
    db_session.add(foreign)
    db_session.flush()
    names.upsert_scope_domains(db_session, foreign, [
        ("secret.example.test", False, None),
        # The same rule as this project's: its names are still theirs.
        ("apps.example.test", True, None),
    ])
    names.record_observation(db_session, project_id=elsewhere.id, name="secret.example.test",
                             record_type="A", value="203.0.113.99", observed_at=NOW)
    names.record_observation(db_session, project_id=elsewhere.id, name="theirs.apps.example.test",
                             record_type="A", value="203.0.113.98", observed_at=NOW)
    db_session.commit()

    resp = client.get("/api/v1/agent/scope/named-targets.ndjson", headers=_key(client, test_project))
    assert resp.status_code == 200, resp.text
    for theirs in ("secret.example.test", "theirs.apps.example.test", "203.0.113.99", "203.0.113.98"):
        assert theirs not in resp.text, f"another project's {theirs} is in this project's file"
    after = _records(client, _key(client, test_project))
    assert set(after) == set(before) and "portal.example.test" in after


@pytest.mark.parametrize("role,allowed", [
    (ProjectRole.AUDITOR, True),
    (ProjectRole.VIEWER, False),
])
def test_named_targets_carry_the_bulk_export_floor(
    client, db_session, test_project, named_scope, role, allowed,
):
    user = _member(db_session, test_project, role)
    headers = {"X-API-Key": _key_for(db_session, test_project, user)}
    resp = client.get("/api/v1/agent/scope/named-targets.ndjson", headers=headers)
    if allowed:
        assert resp.status_code == 200, resp.text
        assert "portal.example.test" in resp.text
    else:
        assert resp.status_code == 403, resp.text
        assert "portal.example.test" not in resp.text
