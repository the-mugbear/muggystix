"""The scope editor shows how many hosts each subnet holds.

Counts come from HostSubnetMapping — the same rows the /hosts subnet facet
counts — so the number on /scopes matches the one in the Hosts filter the
row links to. Computed per page in one grouped query.
"""
from app.db import models


def test_scope_subnets_carry_host_counts(client, db_session, test_project):
    scope = models.Scope(project_id=test_project.id, name="ignored")
    db_session.add(scope)
    db_session.commit()
    busy = models.Subnet(scope_id=scope.id, cidr="10.5.0.0/24")
    empty = models.Subnet(scope_id=scope.id, cidr="10.6.0.0/24")
    db_session.add_all([busy, empty])
    db_session.commit()
    for ip in ("10.5.0.1", "10.5.0.2"):
        host = models.Host(project_id=test_project.id, ip_address=ip, state="up")
        db_session.add(host)
        db_session.commit()
        db_session.add(models.HostSubnetMapping(host_id=host.id, subnet_id=busy.id))
    db_session.commit()

    for qs in ("", "?subnets_limit=1&subnets_skip=1"):
        body = client.get(f"/api/v1/projects/{test_project.id}/scopes/default{qs}").json()
        counts = {s["cidr"]: s["host_count"] for s in body["subnets"]}
        expected = {"10.5.0.0/24": 2, "10.6.0.0/24": 0}
        assert counts == ({"10.6.0.0/24": 0} if qs else expected), qs
