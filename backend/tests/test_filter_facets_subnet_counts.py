"""The subnet and site facets list the project's scope; filters only scope the counts.

Both facets used to filter rows by the active host filter, so a subnet or site
with no matching hosts dropped out of the picker. Picking one subnet hid every
other subnet (you could never add a second), and a filter nothing in scope
matched — out-of-scope-only, a search with no hits — emptied the list entirely,
which read as "this project has no subnets".
"""
from app.db import models


def _seed(db, project):
    """Two sites, three subnets. 10.1.0.0/24 holds one up + one down host,
    10.2.0.0/24 one down host, 10.3.0.0/24 nothing."""
    scope = models.Scope(project_id=project.id, name="ignored")
    db.add(scope)
    db.commit()
    subnets = {}
    for cidr, site in (("10.1.0.0/24", "HQ"), ("10.2.0.0/24", "Branch"), ("10.3.0.0/24", "Branch")):
        subnet = models.Subnet(scope_id=scope.id, cidr=cidr, site=site)
        db.add(subnet)
        db.commit()
        subnets[cidr] = subnet
    for ip, state, cidr in (
        ("10.1.0.5", "up", "10.1.0.0/24"),
        ("10.1.0.6", "down", "10.1.0.0/24"),
        ("10.2.0.5", "down", "10.2.0.0/24"),
    ):
        host = models.Host(project_id=project.id, ip_address=ip, state=state)
        db.add(host)
        db.commit()
        db.add(models.HostSubnetMapping(host_id=host.id, subnet_id=subnets[cidr].id))
        db.commit()


def _facets(client, project, qs=""):
    body = client.get(f"/api/v1/projects/{project.id}/hosts/filters/data{qs}").json()
    return (
        {s["cidr"]: s["host_count"] for s in body["subnets"]},
        {s["name"]: s["host_count"] for s in body["sites"]},
    )


def test_unfiltered_counts(client, db_session, test_project):
    _seed(db_session, test_project)
    subnets, sites = _facets(client, test_project)
    assert subnets == {"10.1.0.0/24": 2, "10.2.0.0/24": 1, "10.3.0.0/24": 0}
    assert sites == {"HQ": 2, "Branch": 1}


def test_filter_scopes_counts_without_dropping_entries(client, db_session, test_project):
    _seed(db_session, test_project)
    subnets, sites = _facets(client, test_project, "?state=up")
    assert subnets == {"10.1.0.0/24": 1, "10.2.0.0/24": 0, "10.3.0.0/24": 0}
    assert sites == {"HQ": 1, "Branch": 0}


def test_filter_matching_nothing_in_scope_keeps_the_picker(client, db_session, test_project):
    _seed(db_session, test_project)
    subnets, sites = _facets(client, test_project, "?out_of_scope_only=true")
    assert subnets == {"10.1.0.0/24": 0, "10.2.0.0/24": 0, "10.3.0.0/24": 0}
    assert sites == {"HQ": 0, "Branch": 0}


def test_selected_subnet_does_not_hide_the_others(client, db_session, test_project):
    _seed(db_session, test_project)
    subnets, sites = _facets(client, test_project, "?subnets=10.1.0.0/24")
    # The subnet facet ignores its own selection …
    assert subnets == {"10.1.0.0/24": 2, "10.2.0.0/24": 1, "10.3.0.0/24": 0}
    # … while other facets are narrowed by it.
    assert sites == {"HQ": 2, "Branch": 0}


def test_selected_site_does_not_hide_the_others(client, db_session, test_project):
    _seed(db_session, test_project)
    subnets, sites = _facets(client, test_project, "?sites=HQ")
    assert sites == {"HQ": 2, "Branch": 1}
    assert subnets == {"10.1.0.0/24": 2, "10.2.0.0/24": 0, "10.3.0.0/24": 0}


def test_own_selection_combines_with_other_filters(client, db_session, test_project):
    _seed(db_session, test_project)
    subnets, _ = _facets(client, test_project, "?subnets=10.1.0.0/24&state=down")
    assert subnets == {"10.1.0.0/24": 1, "10.2.0.0/24": 1, "10.3.0.0/24": 0}
