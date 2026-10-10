"""Regression tests for subnet search on the /scopes editor.

``GET /projects/{pid}/scopes/default`` (and ``/scopes/{id}``) gained a
``subnets_search`` param: a case-insensitive substring filter over
``cidr`` + ``description``, applied BEFORE pagination.

Two behaviours are the ones most likely to silently regress on a later
refactor, so they're pinned here:

  1. The filter is applied to the count query as well as the page query —
     ``subnets_total`` must describe the *filtered* set, otherwise the UI's
     "Showing N of T" + "Load more" affordance lies.
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
    assert {(s["cidr"], s["scope_id"]) for s in page["subnets"]} == {
        ("10.0.0.0/24", scope_id), ("10.0.1.0/24", scope_id), ("10.77.1.0/24", scope_id)}
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
    domains = client.get(f"{base}/{scope_id}/domains").json()
    assert {d["domain"] for d in domains["items"]} == {"example.com", "eval.test"}
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

    # No list of scopes, and no deleting the one there is.
    assert client.get(f"{base}/").status_code in (404, 405)
    assert client.delete(f"{base}/{first}").status_code == 405
    assert db_session.query(models.Scope).filter(models.Scope.id == first).count() == 1
