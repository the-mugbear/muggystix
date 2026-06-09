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
