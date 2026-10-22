""""Outside scope" is one rule (review 2026-10-10, R1a).

Three places wrote it: ``scope_coverage._base_query`` (the out-of-scope list
and export), the DSL's ``scope:none`` and the Scope page's arithmetic
(``total - scoped - name-reachable``).  The list and the Scope page now take
the DSL's predicate.  The old implementations are written out here as they
stood, and compared with the new over mapped, name-only, mapped-and-named,
unmapped hosts, another project's hosts, and a project with no scope.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.db import models
from app.db.models_project import Project
from app.services import dns_name_service, scope_coverage
from app.services.host_query import build_filtered_host_query


# ---------------------------------------------------------------------------
# The replaced implementations, as they stood
# ---------------------------------------------------------------------------

def _old_base_query_ids(db, project_id):
    mapped = select(models.HostSubnetMapping.id).where(
        models.HostSubnetMapping.host_id == models.Host.id
    )
    return {
        hid for (hid,) in db.query(models.Host.id)
        .filter(models.Host.project_id == project_id)
        .filter(~mapped.exists())
        .filter(~dns_name_service.host_reachable_via_in_scope_name_condition(project_id))
    }


def _old_scope_page_counts(db, project_id):
    HSM = models.HostSubnetMapping
    total_hosts = db.query(func.count(models.Host.id)).filter(models.Host.project_id == project_id).scalar() or 0
    scoped = (
        db.query(func.count(func.distinct(HSM.host_id)))
        .join(models.Host, models.Host.id == HSM.host_id)
        .filter(models.Host.project_id == project_id)
        .scalar() or 0
    )
    total_domains = (
        db.query(func.count(models.ScopeDomain.id))
        .join(models.Scope, models.Scope.id == models.ScopeDomain.scope_id)
        .filter(models.Scope.project_id == project_id)
        .scalar() or 0
    )
    cond = dns_name_service.host_reachable_via_in_scope_name_condition(project_id)
    named = 0
    if total_domains:
        named = (
            db.query(func.count(models.Host.id))
            .outerjoin(HSM, HSM.host_id == models.Host.id)
            .filter(models.Host.project_id == project_id, HSM.host_id.is_(None), cond)
            .scalar() or 0
        )
    scan_alias = aliased(models.Scan)
    recent = {
        r.host_id for r in db.query(models.Host.id.label("host_id"))
        .outerjoin(HSM, HSM.host_id == models.Host.id)
        .outerjoin(scan_alias, scan_alias.id == models.Host.last_updated_scan_id)
        .filter(HSM.host_id.is_(None))
        .filter(~cond)
        .filter(models.Host.project_id == project_id)
    }
    return scoped, named, max(total_hosts - scoped - named, 0), recent


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def _seed(db, pid):
    scope = models.Scope(project_id=pid)
    db.add(scope)
    db.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr="10.1.0.0/24")
    inner = models.Subnet(scope_id=scope.id, cidr="10.1.0.0/28")
    db.add_all([subnet, inner])
    db.add(models.ScopeDomain(scope_id=scope.id, domain="example.com", include_subdomains=True))
    db.flush()
    now = datetime.now(timezone.utc)
    hosts = {}
    for i, ip in enumerate(("10.1.0.5", "10.1.0.6", "203.0.113.5", "198.51.100.9", "198.51.100.10")):
        h = models.Host(
            project_id=pid, ip_address=ip, state="up",
            last_seen=now - timedelta(minutes=i),
        )
        db.add(h)
        db.flush()
        hosts[ip] = h
    # Mapped (two mappings: nested subnets), and mapped AND named.
    db.add(models.HostSubnetMapping(host_id=hosts["10.1.0.5"].id, subnet_id=subnet.id))
    db.add(models.HostSubnetMapping(host_id=hosts["10.1.0.5"].id, subnet_id=inner.id))
    db.add(models.HostSubnetMapping(host_id=hosts["10.1.0.6"].id, subnet_id=subnet.id))
    for name, ip in (("www.example.com", "203.0.113.5"), ("app.example.com", "10.1.0.6"),
                     ("other.test", "198.51.100.9")):
        dns_name_service.record_observation(db, project_id=pid, name=name, record_type="A", value=ip)
    db.commit()
    return hosts


def test_outside_scope_is_one_rule_old_equals_new(client, db_session, test_project, test_user):
    pid = test_project.id
    hosts = _seed(db_session, pid)
    # Another project's unmapped host never counts here.
    other = Project(name="other-r1a", slug="other-r1a")
    db_session.add(other)
    db_session.flush()
    db_session.add(models.Host(project_id=other.id, ip_address="198.51.100.9", state="up"))
    db_session.commit()

    def dsl(q):
        return {h.id for h in build_filtered_host_query(db_session, test_user, q=q, project_id=pid)}

    expected_outside = {hosts["198.51.100.9"].id, hosts["198.51.100.10"].id}
    old_ids = _old_base_query_ids(db_session, pid)
    new_ids = {r.id for r in scope_coverage._base_query(db_session, pid)}
    assert old_ids == new_ids == dsl("scope:none") == expected_outside

    scoped, named, outside, recent = _old_scope_page_counts(db_session, pid)
    assert (scoped, named, outside) == (2, 1, 2)
    body = client.get(f"/api/v1/projects/{pid}/scopes/coverage").json()
    assert (body["scoped_hosts"], body["name_reachable_hosts"], body["out_of_scope_hosts"]) == (scoped, named, outside)
    assert {h["host_id"] for h in body["recent_out_of_scope_hosts"]} == recent == expected_outside
    # Each figure is the size of the list it opens.
    assert body["scoped_hosts"] == len(dsl("scope:subnet"))
    assert body["name_reachable_hosts"] == len(dsl("scope:name"))
    hosts_page = client.get(f"/api/v1/projects/{pid}/hosts/", params={"out_of_scope_only": "true"}).json()
    assert body["out_of_scope_hosts"] == hosts_page["total"]


def test_a_project_with_no_scope_counts_what_its_link_lists(client, db_session, test_project, test_user):
    pid = test_project.id
    for ip in ("10.9.0.1", "10.9.0.2"):
        db_session.add(models.Host(project_id=pid, ip_address=ip, state="up"))
    db_session.commit()
    scoped, named, outside, _ = _old_scope_page_counts(db_session, pid)
    body = client.get(f"/api/v1/projects/{pid}/scopes/coverage").json()
    assert (body["scoped_hosts"], body["name_reachable_hosts"], body["out_of_scope_hosts"]) == (scoped, named, outside) == (0, 0, 2)
    hosts_page = client.get(f"/api/v1/projects/{pid}/hosts/", params={"out_of_scope_only": "true"}).json()
    assert body["out_of_scope_hosts"] == hosts_page["total"]
