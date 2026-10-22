"""The Scope page's export is the declared scope (2.521.0).

Owner, 2026-10-22: the export listed the hosts discovered inside the scope —
a duplicate of the Hosts page's export, and not what "export the scope" means.
It now writes the networks and domains, each format a scope file that
``POST /scopes/upload-subnets`` reads back as the same entries: what goes out
of one project comes back in as the same scope.
"""
from __future__ import annotations

import io

from app.db import models

UPLOADED = (
    "subnet,labels,description,site\n"
    "10.50.0.10/32,dmz prod,Mail relay,London DC\n"
    "10.50.0.2,,Single address,\n"
    "10.50.0.0/24,lab,,Manchester DC\n"
    "*.example.com,,Every name under it,\n"
    "portal.example.org,,Exact name only,\n"
)


def _upload(client, project_id, name, content):
    r = client.post(
        f"/api/v1/projects/{project_id}/scopes/upload-subnets",
        files={"file": (name, io.BytesIO(content.encode()), "text/plain")},
    )
    assert r.status_code == 200, r.text
    return r.json()["scope_id"]


def _export(client, project_id, format_type):
    r = client.get(f"/api/v1/projects/{project_id}/export/scope", params={"format_type": format_type})
    assert r.status_code == 200, r.text
    return r


def _empty_scope(db, scope_id):
    db.query(models.Subnet).filter(models.Subnet.scope_id == scope_id).delete()
    db.query(models.ScopeDomain).filter(models.ScopeDomain.scope_id == scope_id).delete()
    db.commit()


def test_each_format_lists_the_declared_entries(client, db_session, test_project):
    _upload(client, test_project.id, "scope.csv", UPLOADED)

    cidrs = _export(client, test_project.id, "cidrs")
    assert cidrs.text == "10.50.0.0/24\n10.50.0.2/32\n10.50.0.10/32\n"
    assert "project-scope_cidrs.txt" in cidrs.headers["content-disposition"]

    domains = _export(client, test_project.id, "domains")
    # Include-subdomains is written as the upload spells it.
    assert domains.text == "*.example.com\nportal.example.org\n"
    assert "project-scope_domains.txt" in domains.headers["content-disposition"]

    csv = _export(client, test_project.id, "csv")
    assert csv.text.splitlines() == [
        "entry,labels,description,site",
        "10.50.0.0/24,lab,,Manchester DC",
        "10.50.0.2/32,,Single address,",
        "10.50.0.10/32,dmz prod,Mail relay,London DC",
        "*.example.com,,Every name under it,",
        "portal.example.org,,Exact name only,",
    ]
    assert "project-scope.csv" in csv.headers["content-disposition"]


def test_a_discovered_host_is_not_part_of_the_scope_export(client, db_session, test_project):
    scope_id = _upload(client, test_project.id, "scope.txt", "10.50.0.0/24\n")
    host = models.Host(ip_address="10.50.0.7", state="up", project_id=test_project.id)
    db_session.add(host)
    db_session.flush()
    subnet = db_session.query(models.Subnet).filter(models.Subnet.scope_id == scope_id).one()
    db_session.add(models.HostSubnetMapping(host_id=host.id, subnet_id=subnet.id))
    db_session.commit()
    for format_type in ("cidrs", "domains", "csv"):
        assert "10.50.0.7" not in _export(client, test_project.id, format_type).text, format_type


def test_every_export_uploads_back_as_the_same_scope(client, db_session, test_project):
    """Export, empty the scope, upload the export: the scope is what it was,
    labels, descriptions, sites and include-subdomains included."""
    scope_id = _upload(client, test_project.id, "scope.csv", UPLOADED)
    before = {f: _export(client, test_project.id, f).text for f in ("cidrs", "domains", "csv")}

    _empty_scope(db_session, scope_id)
    _upload(client, test_project.id, "project-scope.csv", before["csv"])
    assert {f: _export(client, test_project.id, f).text for f in before} == before

    # The two plain lists together carry every entry (no labels, descriptions
    # or sites — a plain list has none).
    _empty_scope(db_session, scope_id)
    _upload(client, test_project.id, "project-scope_cidrs.txt", before["cidrs"])
    _upload(client, test_project.id, "project-scope_domains.txt", before["domains"])
    assert _export(client, test_project.id, "cidrs").text == before["cidrs"]
    assert _export(client, test_project.id, "domains").text == before["domains"]


def test_another_projects_scope_is_not_in_the_export(client, db_session, test_project):
    from app.db.models_project import Project

    _upload(client, test_project.id, "scope.txt", "10.50.0.0/24\n")
    other = Project(name="other-scope-export", slug="other-scope-export", description="x")
    db_session.add(other)
    db_session.flush()
    theirs = models.Scope(project_id=other.id)
    db_session.add(theirs)
    db_session.flush()
    db_session.add(models.Subnet(scope_id=theirs.id, cidr="172.20.0.0/16"))
    db_session.add(models.ScopeDomain(scope_id=theirs.id, domain="theirs.example", include_subdomains=False))
    db_session.commit()
    assert _export(client, test_project.id, "cidrs").text == "10.50.0.0/24\n"
    assert _export(client, test_project.id, "domains").text == ""
