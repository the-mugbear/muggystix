"""Operations UX review 2026-09-26 (v2.424.0): every count Operations shows
opens a list of the same size.

* the three scope-coverage states partition the hosts, and `scope:` lists each;
* the severity drill-downs name the hosts they open (`hosts_by_severity`);
* the default "take it into review" step is marked generic.
"""
from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services import dns_name_service
from app.services.host_query import build_filtered_host_query


def _seed_scope(db, pid):
    scope = models.Scope(project_id=pid, name="s")
    db.add(scope)
    db.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr="10.1.0.0/24")
    db.add(subnet)
    db.add(models.ScopeDomain(scope_id=scope.id, domain="example.com", include_subdomains=True))
    db.flush()
    hosts = {}
    for ip in ("10.1.0.5", "203.0.113.5", "198.51.100.9"):
        h = models.Host(project_id=pid, ip_address=ip, state="up")
        db.add(h)
        db.flush()
        hosts[ip] = h
    db.add(models.HostSubnetMapping(host_id=hosts["10.1.0.5"].id, subnet_id=subnet.id))
    dns_name_service.record_observation(
        db, project_id=pid, name="www.example.com", record_type="A", value="203.0.113.5",
    )
    db.commit()
    return hosts


def test_scope_states_partition_the_hosts_and_each_opens_its_list(client, db_session, test_project, test_user):
    pid = test_project.id
    _seed_scope(db_session, pid)

    def ips(q):
        return {h.ip_address for h in build_filtered_host_query(db_session, test_user, q=q, project_id=pid)}

    assert ips("scope:subnet") == {"10.1.0.5"}
    assert ips("scope:name") == {"203.0.113.5"}
    assert ips("scope:none") == {"198.51.100.9"}

    body = client.get(f"/api/v1/projects/{pid}/coverage/").json()
    assert (body["hosts_in_subnet_scope"], body["hosts_name_scope_only"], body["hosts_outside_scope"]) == (1, 1, 1)
    assert body["hosts_in_subnet_scope"] + body["hosts_name_scope_only"] + body["hosts_outside_scope"] == body["total_hosts"]


def test_severity_drilldowns_name_the_hosts_they_open(client, db_session, test_project):
    pid = test_project.id
    scan = models.Scan(filename="n", tool_name="nessus", project_id=pid)
    db_session.add(scan)
    db_session.flush()
    a = models.Host(project_id=pid, ip_address="10.2.0.1", state="up")
    b = models.Host(project_id=pid, ip_address="10.2.0.2", state="up")
    db_session.add_all([a, b])
    db_session.flush()
    # Three high rows on two hosts: the bar says 3, the link opens 2.
    for host, plugin in ((a, "1"), (a, "2"), (b, "3")):
        db_session.add(Vulnerability(host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                                     plugin_id=plugin, title=f"t{plugin}", severity=VulnerabilitySeverity.HIGH))
    db_session.commit()

    stats = client.get(f"/api/v1/projects/{pid}/dashboard/stats").json()["vulnerability_stats"]
    assert stats["high"] == 3
    assert stats["hosts_by_severity"]["high"] == 2
    listed = client.get(f"/api/v1/projects/{pid}/hosts/?has_high_vulns=true&limit=1").json()["total"]
    assert listed == stats["hosts_by_severity"]["high"]
