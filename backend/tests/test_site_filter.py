"""Filter hosts by site — a host matches if ANY of its subnets belongs to the
named site (the subnet's ``site`` string), via HostSubnetMapping.
"""
from app.db import models
from app.services.host_query import build_filtered_host_query


def _host_in_subnet(db, project_id, ip, subnet):
    h = models.Host(ip_address=ip, state="up", project_id=project_id)
    db.add(h)
    db.flush()
    db.add(models.HostSubnetMapping(host_id=h.id, subnet_id=subnet.id))
    db.flush()
    return h


def test_site_filter_matches_only_hosts_in_that_site(db_session, test_project, test_user):
    pid = test_project.id
    scope = models.Scope(project_id=pid, name="sc")
    db_session.add(scope)
    db_session.flush()
    east = models.Subnet(scope_id=scope.id, cidr="10.7.0.0/24", site="DC-East")
    west = models.Subnet(scope_id=scope.id, cidr="10.8.0.0/24", site="DC-West")
    db_session.add_all([east, west])
    db_session.flush()

    h_east = _host_in_subnet(db_session, pid, "10.7.0.5", east)
    h_west = _host_in_subnet(db_session, pid, "10.8.0.5", west)

    q = build_filtered_host_query(db_session, test_user, sites="DC-East", project_id=pid)
    ids = {h.id for h in q.all()}
    assert h_east.id in ids
    assert h_west.id not in ids

    # OR within the group — both sites selected returns both hosts.
    q2 = build_filtered_host_query(db_session, test_user, sites="DC-East,DC-West", project_id=pid)
    ids2 = {h.id for h in q2.all()}
    assert {h_east.id, h_west.id} <= ids2
