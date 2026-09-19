"""Which scope entries cover a host (v2.342.0).

The host inspector lists every scope subnet a host sits in and every in-scope
name that currently resolves to it, plus the coverage state.  Pins:

* subnets come from ``host_subnet_mappings`` (the same fact scope coverage
  reads), most-specific first, with their labels and site;
* a name-only host is ``coverage == "name"`` and gets no subnet;
* an uncovered host on a project with a scope is ``none`` with
  ``project_has_scope`` True; on a project with no scope at all it is False;
* the detail endpoint carries the block;
* NetExec, the one host-creating parser that never correlated, now does.
"""
import os

from app.db import models
from app.services import dns_name_service
from app.services.scope_coverage import host_scope_membership


def _scope(db, project_id):
    scope = models.Scope(project_id=project_id, name="s")
    db.add(scope)
    db.flush()
    return scope


def _subnet(db, scope, cidr, **kw):
    s = models.Subnet(scope_id=scope.id, cidr=cidr, **kw)
    db.add(s)
    db.flush()
    return s


def _host(db, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _map(db, host, subnet):
    db.add(models.HostSubnetMapping(host_id=host.id, subnet_id=subnet.id))
    db.flush()


def test_subnet_membership_lists_every_containing_entry_most_specific_first(db_session, test_project):
    scope = _scope(db_session, test_project.id)
    wide = _subnet(db_session, scope, "10.0.0.0/8", description="corp")
    narrow = _subnet(db_session, scope, "10.1.2.0/24", description="dmz", site="London DC")
    label = models.SubnetLabel(project_id=test_project.id, name="prod", color="#f00")
    db_session.add(label)
    db_session.flush()
    db_session.add(models.SubnetLabelAssignment(subnet_id=narrow.id, label_id=label.id))
    h = _host(db_session, test_project.id, "10.1.2.3")
    _map(db_session, h, wide)
    _map(db_session, h, narrow)
    db_session.commit()

    m = host_scope_membership(db_session, h)

    assert m["coverage"] == "subnet"
    assert m["project_has_scope"] is True
    assert [s["cidr"] for s in m["subnets"]] == ["10.1.2.0/24", "10.0.0.0/8"]
    assert m["subnets"][0]["site"] == "London DC"
    assert m["subnets"][0]["description"] == "dmz"
    assert m["subnets"][0]["labels"] == [{"id": label.id, "name": "prod", "color": "#f00"}]
    assert m["subnets"][1]["labels"] == []
    assert m["names"] == []


def test_membership_ignores_another_projects_subnet(db_session, test_project):
    """A stray cross-project mapping (written by the pre-2.342.0 scan path)
    must not read as coverage until the cleanup migration removes it."""
    from app.db.models_project import Project

    other = Project(name="other", slug="other", description="", is_default=False)
    db_session.add(other)
    db_session.flush()
    stray = _subnet(db_session, _scope(db_session, other.id), "10.0.0.0/8")
    h = _host(db_session, test_project.id, "10.1.2.3")
    _map(db_session, h, stray)
    db_session.commit()

    m = host_scope_membership(db_session, h)
    assert m["coverage"] == "none"
    assert m["subnets"] == []
    assert m["project_has_scope"] is False


def test_name_only_coverage_lists_the_admitting_domain(db_session, test_project):
    scope = _scope(db_session, test_project.id)
    db_session.add(models.ScopeDomain(scope_id=scope.id, domain="example.com", include_subdomains=True))
    db_session.flush()
    h = _host(db_session, test_project.id, "203.0.113.5")
    dns_name_service.record_observation(
        db_session, project_id=test_project.id, name="www.example.com", record_type="A", value="203.0.113.5",
    )
    # A name that resolves here but is NOT in scope must not appear.
    dns_name_service.record_observation(
        db_session, project_id=test_project.id, name="cdn.other.net", record_type="A", value="203.0.113.5",
    )
    db_session.commit()

    m = host_scope_membership(db_session, h)

    assert m["coverage"] == "name"
    assert m["subnets"] == []
    assert m["names"] == [{"fqdn": "www.example.com", "domain": "example.com", "include_subdomains": True}]


def test_name_that_moved_away_no_longer_covers(db_session, test_project):
    scope = _scope(db_session, test_project.id)
    db_session.add(models.ScopeDomain(scope_id=scope.id, domain="moved.example.com", include_subdomains=False))
    db_session.flush()
    h = _host(db_session, test_project.id, "203.0.113.9")
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dns_name_service.record_observation(
        db_session, project_id=test_project.id, name="moved.example.com", record_type="A",
        value="203.0.113.9", observed_at=t0,
    )
    dns_name_service.record_observation(
        db_session, project_id=test_project.id, name="moved.example.com", record_type="A",
        value="203.0.113.10", observed_at=t0 + timedelta(days=1),
    )
    db_session.commit()

    m = host_scope_membership(db_session, h)
    assert m["coverage"] == "none"
    assert m["names"] == []
    assert m["project_has_scope"] is True


def test_uncovered_host_distinguishes_no_scope_from_out_of_scope(db_session, test_project):
    h = _host(db_session, test_project.id, "198.51.100.1")
    db_session.commit()
    assert host_scope_membership(db_session, h) == {
        "coverage": "none", "project_has_scope": False, "subnets": [], "names": [],
    }

    _subnet(db_session, _scope(db_session, test_project.id), "10.0.0.0/8")
    db_session.commit()
    m = host_scope_membership(db_session, h)
    assert m["coverage"] == "none"
    assert m["project_has_scope"] is True


def test_host_detail_carries_scope_membership(client, db_session, test_project):
    scope = _scope(db_session, test_project.id)
    s = _subnet(db_session, scope, "10.1.0.0/16", site="Leeds")
    h = _host(db_session, test_project.id, "10.1.4.4")
    _map(db_session, h, s)
    db_session.commit()

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{h.id}")
    assert r.status_code == 200, r.text
    body = r.json()["scope_membership"]
    assert body["coverage"] == "subnet"
    assert body["project_has_scope"] is True
    assert [(x["cidr"], x["site"]) for x in body["subnets"]] == [("10.1.0.0/16", "Leeds")]
    assert body["names"] == []


def test_host_list_carries_the_three_coverage_states(client, db_session, test_project):
    """v2.344.0 — the list row says the same thing the detail card says.

    Before: every host without a subnet mapping was printed "out of scope" in
    the Hosts table, including one an approved name resolves to, and
    including every host on a project that has declared no scope at all.
    """
    scope = _scope(db_session, test_project.id)
    s = _subnet(db_session, scope, "10.1.0.0/16")
    db_session.add(models.ScopeDomain(scope_id=scope.id, domain="example.com", include_subdomains=True))
    db_session.flush()
    by_subnet = _host(db_session, test_project.id, "10.1.4.4")
    _map(db_session, by_subnet, s)
    by_name = _host(db_session, test_project.id, "203.0.113.5")
    dns_name_service.record_observation(
        db_session, project_id=test_project.id, name="www.example.com", record_type="A", value="203.0.113.5",
    )
    uncovered = _host(db_session, test_project.id, "198.51.100.1")
    db_session.commit()

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/?limit=50")
    assert r.status_code == 200, r.text
    rows = {h["ip_address"]: h for h in r.json()["items"]}
    assert rows["10.1.4.4"]["scope_coverage"] == "subnet"
    assert rows["10.1.4.4"]["primary_subnet"] == "10.1.0.0/16"
    assert rows["203.0.113.5"]["scope_coverage"] == "name"
    assert rows["203.0.113.5"]["primary_subnet"] is None
    assert rows["198.51.100.1"]["scope_coverage"] == "none"
    assert all(h["project_has_scope"] is True for h in rows.values())


def test_host_list_says_no_scope_defined_rather_than_out_of_scope(client, db_session, test_project):
    _host(db_session, test_project.id, "198.51.100.1")
    db_session.commit()
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/?limit=50")
    assert r.status_code == 200, r.text
    (row,) = r.json()["items"]
    assert row["scope_coverage"] == "none"
    assert row["project_has_scope"] is False


def test_netexec_upload_correlates_its_hosts(db_session, test_project, tmp_path):
    """NetExec created hosts but never ran scope correlation, so a host first
    seen by it stayed out of scope until something else re-correlated."""
    from app.parsers.netexec_parser import NetexecParser

    s = _subnet(db_session, _scope(db_session, test_project.id), "10.0.0.0/24")
    db_session.commit()

    path = tmp_path / "nxc.txt"
    path.write_text("SMB  10.0.0.5  445  HOSTX  [+] CORP\\administrator:Passw0rd (Pwn3d!)\n")
    scan = NetexecParser(db_session).parse_file(str(path), os.path.basename(path), project_id=test_project.id)

    host = (
        db_session.query(models.Host)
        .filter(models.Host.project_id == test_project.id, models.Host.ip_address == "10.0.0.5")
        .one()
    )
    assert scan.id is not None
    mapped = (
        db_session.query(models.HostSubnetMapping)
        .filter(models.HostSubnetMapping.host_id == host.id)
        .all()
    )
    assert [m.subnet_id for m in mapped] == [s.id]
