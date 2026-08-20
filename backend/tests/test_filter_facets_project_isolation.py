"""Regression: the subnet facet must not leak another project's network layout.

`GET /projects/{id}/hosts/filters/data` builds a subnet facet with

    or_(host_scope, HostSubnetMapping.host_id.is_(None))

so that a scoped-but-empty subnet still appears in the filter list. The base
query carried no `Scope.project_id` restriction, so that second arm also matched
empty subnets belonging to OTHER projects — exposing their CIDR and scope name
to any user of this project. A *populated* foreign subnet was excluded (its
hosts fail host_scope), which is why it went unnoticed: the leak is invisible
unless a neighbouring project happens to have an empty subnet.

CIDRs and scope names are network-boundary data about someone else's
engagement, so this is a cross-tenant disclosure, not a cosmetic bug.
"""
from app.db import models
from app.db.models_project import Project


def _seed_project_with_subnets(db, *, name, slug, cidrs, populated_cidr=None):
    project = Project(name=name, slug=slug)
    db.add(project)
    db.commit()
    scope = models.Scope(project_id=project.id, name=f"{slug}-scope")
    db.add(scope)
    db.commit()
    subnets = {}
    for cidr in cidrs:
        subnet = models.Subnet(scope_id=scope.id, cidr=cidr)
        db.add(subnet)
        db.commit()
        subnets[cidr] = subnet
    if populated_cidr:
        host = models.Host(project_id=project.id, ip_address="10.77.0.5", state="up")
        db.add(host)
        db.commit()
        db.add(models.HostSubnetMapping(
            host_id=host.id, subnet_id=subnets[populated_cidr].id,
        ))
        db.commit()
    return project, subnets


def test_empty_subnets_from_another_project_are_not_disclosed(
    client, db_session, test_project
):
    _seed_project_with_subnets(
        db_session,
        name="Confidential client",
        slug="confidential-client",
        # One empty (the leaking shape) and one populated (the control).
        cidrs=["192.168.44.0/24", "10.77.0.0/24"],
        populated_cidr="10.77.0.0/24",
    )

    body = client.get(f"/api/v1/projects/{test_project.id}/hosts/filters/data").json()
    cidrs = {s["cidr"] for s in body["subnets"]}
    scope_names = {s.get("scope_name") for s in body["subnets"]}

    assert "192.168.44.0/24" not in cidrs, (
        "an empty subnet from another project leaked through the "
        "`host_id IS NULL` arm of the facet filter"
    )
    assert "10.77.0.0/24" not in cidrs
    assert "confidential-client-scope" not in scope_names


def test_this_projects_empty_subnets_are_still_listed(client, db_session, test_project):
    """The other half of the contract. The `host_id IS NULL` arm exists so an
    operator can filter by a scoped range that has not been discovered yet —
    fixing the leak must not take that away, or a scoping gap becomes
    invisible."""
    scope = models.Scope(project_id=test_project.id, name="mine")
    db_session.add(scope)
    db_session.commit()
    db_session.add(models.Subnet(scope_id=scope.id, cidr="172.31.9.0/24"))
    db_session.commit()

    body = client.get(f"/api/v1/projects/{test_project.id}/hosts/filters/data").json()
    listed = {s["cidr"]: s for s in body["subnets"]}
    assert "172.31.9.0/24" in listed
    assert listed["172.31.9.0/24"]["host_count"] == 0
