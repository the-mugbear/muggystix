"""Regression tests for subnet search on the /scopes editor.

``GET /projects/{pid}/scopes/default`` gained a
``subnets_search`` param: a case-insensitive substring filter over
``cidr`` + ``description``, applied BEFORE pagination.

Two behaviours are the ones most likely to silently regress on a later
refactor, so they're pinned here:

  1. The filter is applied to the count query as well as the page query —
     ``subnets_total`` must describe the *filtered* set, otherwise the UI's
     "1–200 of T" and its pages lie.
  2. LIKE wildcards in the search term are escaped — a term containing
     ``%`` matches a literal ``%``, not "everything".
"""
from __future__ import annotations

import pytest

from app.db import models


def _default_scope_id(client, project_id: int) -> int:
    r = client.get(f"/api/v1/projects/{project_id}/scopes/default")
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed(db_session, scope_id: int, rows: list[tuple[str, str]]) -> None:
    db_session.add_all(
        [models.Subnet(scope_id=scope_id, cidr=c, description=d) for c, d in rows]
    )
    db_session.flush()


def test_subnet_search_narrows_total_and_page(client, db_session, test_project):
    """A search term filters both the returned page AND subnets_total, and
    matches across cidr + description case-insensitively."""
    scope_id = _default_scope_id(client, test_project.id)
    _seed(db_session, scope_id, [
        ("10.0.0.0/24", "UK DMZ"),
        ("10.0.1.0/24", "Prod database"),
        ("192.168.5.0/24", "Corp LAN"),
    ])

    # Unfiltered: all three, total == 3.
    base = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/default",
        params={"subnets_limit": 200},
    ).json()
    assert base["subnets_total"] == 3
    assert len(base["subnets"]) == 3

    # Match on cidr substring → 1 row, and subnets_total reflects the filter.
    by_cidr = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/default",
        params={"subnets_limit": 200, "subnets_search": "192.168"},
    ).json()
    assert by_cidr["subnets_total"] == 1
    assert len(by_cidr["subnets"]) == 1
    assert by_cidr["subnets"][0]["cidr"] == "192.168.5.0/24"

    # Match on description, case-insensitive ("dmz" → "UK DMZ").
    by_desc = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/default",
        params={"subnets_limit": 200, "subnets_search": "dmz"},
    ).json()
    assert by_desc["subnets_total"] == 1
    assert by_desc["subnets"][0]["description"] == "UK DMZ"

    # No match → empty page, zero total (not the full set).
    none = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/default",
        params={"subnets_limit": 200, "subnets_search": "nonexistent-zzz"},
    ).json()
    assert none["subnets_total"] == 0
    assert none["subnets"] == []


def test_subnet_search_escapes_like_wildcards(client, db_session, test_project):
    """A search term containing '%' matches a LITERAL '%', not every row.
    Without escaping, ilike('%%%') would match all subnets."""
    scope_id = _default_scope_id(client, test_project.id)
    _seed(db_session, scope_id, [
        ("10.0.0.0/24", "10% capacity"),   # contains a literal percent
        ("10.0.1.0/24", "ten percent"),    # the word, no '%' character
    ])

    r = client.get(
        f"/api/v1/projects/{test_project.id}/scopes/default",
        params={"subnets_limit": 200, "subnets_search": "%"},
    ).json()
    # Only the row with an actual '%' character matches.
    assert r["subnets_total"] == 1
    assert r["subnets"][0]["description"] == "10% capacity"


def test_the_scope_pages_totals_are_the_coverage_tiles(client, db_session, test_project):
    """The page's table and the coverage tile count the same entries: the
    Acceptance project's tile once said "Subnets 12" over a table of 9
    (analyst walkthrough 2026-10-10), because a seed had left a second scope
    whose entries were on no page.  A second scope cannot exist since 2.488.0
    (`uq_scopes_project`; `test_a_project_cannot_hold_a_second_scope`), so
    what is left to pin is that the two reads agree."""
    scope_id = _default_scope_id(client, test_project.id)
    _seed(db_session, scope_id, [("10.0.0.0/24", "A"), ("10.0.1.0/24", "B"), ("10.77.1.0/24", "East")])

    base = f"/api/v1/projects/{test_project.id}/scopes"
    page = client.get(f"{base}/default", params={"subnets_limit": 200}).json()
    assert page["id"] == scope_id and "name" not in page
    assert {s["cidr"] for s in page["subnets"]} == {"10.0.0.0/24", "10.0.1.0/24", "10.77.1.0/24"}
    # A row names no scope (2.489.0): there is one, and no path takes its id.
    assert all("scope_id" not in s for s in page["subnets"])
    assert page["subnets_total"] == 3 == client.get(f"{base}/coverage").json()["total_subnets"]
    # Search and paging are over the same set.
    assert client.get(f"{base}/default", params={"subnets_limit": 200, "subnets_search": "East"}).json()["subnets_total"] == 1
    assert len(client.get(f"{base}/default", params={"subnets_limit": 2}).json()["subnets"]) == 2

    # The same for the domains declared in scope (the tile said 4 over a list of 3).
    ScopeDomain = models.ScopeDomain
    db_session.add_all([
        ScopeDomain(scope_id=scope_id, domain="example.com", include_subdomains=True),
        ScopeDomain(scope_id=scope_id, domain="eval.test", include_subdomains=False),
    ])
    db_session.flush()
    domains = client.get(f"{base}/domains").json()
    assert {d["domain"] for d in domains["items"]} == {"example.com", "eval.test"}
    assert all("scope_id" not in d for d in domains["items"])
    assert domains["total"] == 2 == client.get(f"{base}/coverage").json()["total_domains"]


def test_a_project_cannot_hold_a_second_scope(client, db_session, test_project):
    """One scope per project, in the schema (owner, 2026-10-10: a second
    "shouldn't be possible").  The page's read creates it once and returns the
    same one after; a second row is refused by `uq_scopes_project`; the routes
    that listed scopes or deleted one are gone."""
    from sqlalchemy.exc import IntegrityError

    base = f"/api/v1/projects/{test_project.id}/scopes"
    first = _default_scope_id(client, test_project.id)
    assert _default_scope_id(client, test_project.id) == first
    assert db_session.query(models.Scope).filter(models.Scope.project_id == test_project.id).count() == 1

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(models.Scope(project_id=test_project.id))
            db_session.flush()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(models.Scope(project_id=None))
            db_session.flush()

    # No list of scopes, no deleting the one there is, and — since 2.489.0 —
    # no path that takes its id at all.
    assert client.get(f"{base}/").status_code in (404, 405)
    assert client.delete(f"{base}/{first}").status_code in (404, 405)
    assert db_session.query(models.Scope).filter(models.Scope.id == first).count() == 1


# ---------------------------------------------------------------------------
# 2.489.0 — the people's scope routes are keyed by the project alone
# ---------------------------------------------------------------------------

def _other_projects_entries(db_session):
    """Another project's scope with one subnet and one domain entry."""
    from app.db.models_project import Project

    other = Project(name="Other", slug="other-scope-routes")
    db_session.add(other)
    db_session.flush()
    scope = models.Scope(project_id=other.id)
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr="10.200.0.0/24", description="theirs")
    domain = models.ScopeDomain(scope_id=scope.id, domain="theirs.example", include_subdomains=False)
    db_session.add_all([subnet, domain])
    db_session.flush()
    return scope, subnet, domain


def test_scope_writes_take_no_scope_id_and_create_the_scope(client, db_session, test_project):
    """Add, edit and delete a subnet and a domain with the project alone in
    the path — on a project whose scope row does not exist yet, which the
    first write creates."""
    base = f"/api/v1/projects/{test_project.id}/scopes"
    assert db_session.query(models.Scope).filter(models.Scope.project_id == test_project.id).count() == 0

    added = client.post(f"{base}/subnets", json={"subnets": [{"cidr": "10.201.0.7/24", "description": "mine"}]})
    assert added.status_code == 201, added.text
    row = added.json()[0]
    assert row["cidr"] == "10.201.0.0/24" and "scope_id" not in row
    scope = db_session.query(models.Scope).filter(models.Scope.project_id == test_project.id).one()
    assert db_session.get(models.Subnet, row["id"]).scope_id == scope.id
    # The same CIDR again is refused, as before.
    assert client.post(f"{base}/subnets", json={"subnets": [{"cidr": "10.201.0.0/24"}]}).status_code == 400

    edited = client.patch(f"{base}/subnets/{row['id']}", json={"description": "renamed", "site": "East"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["description"] == "renamed" and edited.json()["site"] == "East"

    declared = client.post(f"{base}/domains", json={"domains": [{"domain": "mine.example"}]})
    assert declared.status_code == 200, declared.text
    domain_id = declared.json()["domains"][0]["id"]
    assert db_session.query(models.Scope).filter(models.Scope.project_id == test_project.id).count() == 1
    assert [d["domain"] for d in client.get(f"{base}/domains").json()["items"]] == ["mine.example"]

    assert client.delete(f"{base}/domains/{domain_id}").status_code == 200
    assert client.delete(f"{base}/subnets/{row['id']}").status_code == 200
    assert client.get(f"{base}/domains").json()["total"] == 0
    assert client.get(f"{base}/default").json()["subnets_total"] == 0


def test_a_domain_can_be_declared_on_a_project_with_no_scope_row(client, db_session, test_project):
    """The first write of a project may be a domain: it creates the scope."""
    base = f"/api/v1/projects/{test_project.id}/scopes"
    assert client.get(f"{base}/domains").json()["total"] == 0
    # The list is a read: it created nothing.
    assert db_session.query(models.Scope).filter(models.Scope.project_id == test_project.id).count() == 0
    r = client.post(f"{base}/domains", json={"domains": [{"domain": "*.first.example"}]})
    assert r.status_code == 200 and r.json()["added"] == 1, r.text
    assert db_session.query(models.Scope).filter(models.Scope.project_id == test_project.id).count() == 1


def test_another_projects_subnet_or_domain_id_is_not_found(client, db_session, test_project):
    """An entry is looked up inside THIS project's scope: another project's id
    is a 404 and is left exactly as it was."""
    base = f"/api/v1/projects/{test_project.id}/scopes"
    _default_scope_id(client, test_project.id)
    _scope, subnet, domain = _other_projects_entries(db_session)

    assert client.patch(f"{base}/subnets/{subnet.id}", json={"description": "taken"}).status_code == 404
    assert client.delete(f"{base}/subnets/{subnet.id}").status_code == 404
    assert client.delete(f"{base}/domains/{domain.id}").status_code == 404
    assert client.patch(f"{base}/subnets/{10 ** 9}", json={"description": "x"}).status_code == 404

    db_session.expire_all()
    assert db_session.get(models.Subnet, subnet.id).description == "theirs"
    assert db_session.get(models.ScopeDomain, domain.id).domain == "theirs.example"
    # ...and their entries are on none of this project's lists.
    assert client.get(f"{base}/default").json()["subnets"] == []
    assert client.get(f"{base}/domains").json()["items"] == []


@pytest.mark.parametrize("method, path", [
    ("get", "/scopes/{scope}"),
    ("get", "/scopes/{scope}/host-mappings"),
    ("post", "/scopes/{scope}/subnets"),
    ("patch", "/scopes/{scope}/subnets/{subnet}"),
    ("delete", "/scopes/{scope}/subnets/{subnet}"),
    ("get", "/scopes/{scope}/domains"),
    ("post", "/scopes/{scope}/domains"),
    ("delete", "/scopes/{scope}/domains/{domain}"),
    ("get", "/export/scope/{scope}"),
])
def test_the_paths_that_took_a_scope_id_are_gone(client, db_session, test_project, method, path):
    """No alias: the old spelling answers 404 / 405 and changes nothing."""
    scope_id = _default_scope_id(client, test_project.id)
    _seed(db_session, scope_id, [("10.202.0.0/24", "kept")])
    subnet = db_session.query(models.Subnet).filter(models.Subnet.scope_id == scope_id).one()
    domain = models.ScopeDomain(scope_id=scope_id, domain="kept.example", include_subdomains=False)
    db_session.add(domain)
    db_session.flush()
    url = f"/api/v1/projects/{test_project.id}" + path.format(scope=scope_id, subnet=subnet.id, domain=domain.id)
    body = {"json": {"subnets": [{"cidr": "10.203.0.0/24"}], "domains": [{"domain": "x.example"}], "description": "y"}}
    r = getattr(client, method)(url, **(body if method in ("post", "patch") else {}))
    assert r.status_code in (404, 405), (method, path, r.status_code)
    db_session.expire_all()
    assert [(s.cidr, s.description) for s in db_session.query(models.Subnet).filter(
        models.Subnet.scope_id == scope_id)] == [("10.202.0.0/24", "kept")]
    assert [d.domain for d in db_session.query(models.ScopeDomain).filter(
        models.ScopeDomain.scope_id == scope_id)] == ["kept.example"]


def test_the_scope_page_read_takes_no_with_findings_only(client, db_session, test_project):
    """Nobody asked for ``with_findings_only`` (only the removed
    ``GET /scopes/{scope_id}`` defaulted to it): the page's read lists every
    entry, mapped or not, and its total counts the same rows."""
    scope_id = _default_scope_id(client, test_project.id)
    _seed(db_session, scope_id, [("10.204.0.0/24", "busy"), ("10.204.1.0/24", "empty")])
    busy = db_session.query(models.Subnet).filter(models.Subnet.cidr == "10.204.0.0/24").one()
    host = models.Host(project_id=test_project.id, ip_address="10.204.0.9", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(models.HostSubnetMapping(host_id=host.id, subnet_id=busy.id))
    db_session.flush()

    from app.main import app
    params = app.openapi()["paths"]["/api/v1/projects/{project_id}/scopes/default"]["get"]["parameters"]
    assert "with_findings_only" not in {p["name"] for p in params}
    page = client.get(f"/api/v1/projects/{test_project.id}/scopes/default", params={"subnets_limit": 1}).json()
    assert page["subnets_total"] == 2 and len(page["subnets"]) == 1
