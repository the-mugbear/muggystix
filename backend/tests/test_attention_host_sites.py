"""attention_service._resolve_host_sites — site attribution rule.

Now a thin projection over subnet_insight_service.resolve_host_locations (one
implementation of the longest-prefix site-inheritance rule). Pins the behavior:
most-specific site-bearing subnet wins, an unlabelled child inherits its
labelled parent's site, and a host in no site-bearing subnet is absent.
"""
from app.db import models
from app.services.attention_service import _resolve_host_sites
from app.services.subnet_correlation import SubnetCorrelationService


def _setup(db, project_id, subnet_specs):
    scope = models.Scope(project_id=project_id, name="s")
    db.add(scope)
    db.flush()
    ids = {}
    for cidr, site in subnet_specs:
        sub = models.Subnet(scope_id=scope.id, cidr=cidr, site=site)
        db.add(sub)
        db.flush()
        ids[cidr] = sub.id
    return ids


def _host(db, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def test_resolve_host_sites_rules(db_session, test_project):
    pid = test_project.id
    _setup(db_session, pid, [
        ("10.0.0.0/16", "DC-East"),     # labelled parent
        ("10.0.1.0/24", None),          # unlabelled child -> inherits DC-East
        ("10.0.2.0/24", "DC-East-Rack"),  # labelled child -> most-specific site wins
        ("192.168.0.0/24", None),       # no site anywhere -> host absent
    ])
    h_inherit = _host(db_session, pid, "10.0.1.5")   # in unlabelled /24 + labelled /16
    h_specific = _host(db_session, pid, "10.0.2.5")  # in labelled /24 + labelled /16
    h_none = _host(db_session, pid, "192.168.0.5")   # no site-bearing subnet
    db_session.commit()

    SubnetCorrelationService(db_session).correlate_all_hosts_to_subnets(project_id=pid)

    sites = _resolve_host_sites(db_session, pid)
    assert sites.get(h_inherit.id) == "DC-East"        # inherited from labelled /16
    assert sites.get(h_specific.id) == "DC-East-Rack"  # most-specific site-bearing wins
    assert h_none.id not in sites                       # no site -> absent
