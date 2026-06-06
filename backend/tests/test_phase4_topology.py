"""Phase 4 backend test: network topology graph endpoint."""
from datetime import datetime, timezone

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


def test_topology_graph(client, db_session, test_project):
    pid = test_project.id
    scan = models.Scan(project_id=pid, filename="s.xml", tool_name="nmap", scan_type="nmap_xml")
    db_session.add(scan)
    scope = models.Scope(project_id=pid, name="DMZ")
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr="10.0.0.0/24")
    db_session.add(subnet)
    db_session.flush()

    # Two hosts mapped into the subnet; one carries a critical vuln.
    h1 = models.Host(project_id=pid, ip_address="10.0.0.1", state="up")
    h2 = models.Host(project_id=pid, ip_address="10.0.0.2", state="up")
    unmapped = models.Host(project_id=pid, ip_address="192.168.9.9", state="up")
    db_session.add_all([h1, h2, unmapped])
    db_session.flush()
    db_session.add_all([
        models.HostSubnetMapping(host_id=h1.id, subnet_id=subnet.id),
        models.HostSubnetMapping(host_id=h2.id, subnet_id=subnet.id),
    ])
    db_session.add(Vulnerability(
        host_id=h1.id, scan_id=scan.id, title="EternalBlue", plugin_id="1",
        severity=VulnerabilitySeverity.CRITICAL, source=VulnerabilitySource.NESSUS,
    ))
    db_session.commit()

    body = client.get(f"/api/v1/projects/{pid}/dashboard/topology").json()
    nodes = {n["id"]: n for n in body["nodes"]}
    edges = {(e["source"], e["target"]) for e in body["edges"]}

    assert "project" in nodes
    assert nodes[f"scope-{scope.id}"]["type"] == "scope"
    subnet_node = nodes[f"subnet-{subnet.id}"]
    assert subnet_node["host_count"] == 2
    assert subnet_node["meta"]["critical_hosts"] == 1
    assert subnet_node["meta"]["cidr"] == "10.0.0.0/24"
    assert nodes["unscoped"]["host_count"] == 1

    assert ("project", f"scope-{scope.id}") in edges
    assert (f"scope-{scope.id}", f"subnet-{subnet.id}") in edges
    assert ("project", "unscoped") in edges


def test_topology_excludes_cross_project_hosts(client, db_session, test_project):
    """Project isolation: a host from ANOTHER project mapped into this
    project's subnet (overlapping CIDR) must not inflate host_count or
    critical_hosts."""
    from app.db.models_project import Project

    pid = test_project.id
    scan = models.Scan(project_id=pid, filename="s.xml", tool_name="nmap", scan_type="nmap_xml")
    db_session.add(scan)
    scope = models.Scope(project_id=pid, name="A")
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr="10.0.0.0/24")
    db_session.add(subnet)
    db_session.flush()

    # In-project host (no vuln).
    ha = models.Host(project_id=pid, ip_address="10.0.0.1", state="up")
    db_session.add(ha)
    db_session.flush()
    db_session.add(models.HostSubnetMapping(host_id=ha.id, subnet_id=subnet.id))

    # Another project's host, mapped into THIS subnet, carrying a critical vuln.
    other = Project(name="proj-b", slug="proj-b-iso", description="x", is_default=False)
    db_session.add(other)
    db_session.flush()
    hb = models.Host(project_id=other.id, ip_address="10.0.0.9", state="up")
    db_session.add(hb)
    db_session.flush()
    db_session.add(models.HostSubnetMapping(host_id=hb.id, subnet_id=subnet.id))
    db_session.add(Vulnerability(
        host_id=hb.id, scan_id=scan.id, title="x", plugin_id="1",
        severity=VulnerabilitySeverity.CRITICAL, source=VulnerabilitySource.NESSUS,
    ))

    # An in-project host mapped ONLY to a foreign (other-project) subnet must
    # still surface in THIS project's "unscoped" node — not vanish from both
    # the subnet counts and unscoped.
    other_scope = models.Scope(project_id=other.id, name="B")
    db_session.add(other_scope)
    db_session.flush()
    other_subnet = models.Subnet(scope_id=other_scope.id, cidr="10.99.0.0/24")
    db_session.add(other_subnet)
    db_session.flush()
    hc = models.Host(project_id=pid, ip_address="10.99.0.5", state="up")
    db_session.add(hc)
    db_session.flush()
    db_session.add(models.HostSubnetMapping(host_id=hc.id, subnet_id=other_subnet.id))
    db_session.commit()

    body = client.get(f"/api/v1/projects/{pid}/dashboard/topology").json()
    sn = next(n for n in body["nodes"] if n["id"] == f"subnet-{subnet.id}")
    assert sn["host_count"] == 1               # only the in-project host
    assert sn["meta"]["critical_hosts"] == 0   # cross-project critical excluded
    # hc is in-project but mapped only to a foreign subnet → counted unscoped.
    unscoped = next((n for n in body["nodes"] if n["id"] == "unscoped"), None)
    assert unscoped is not None and unscoped["host_count"] == 1


def test_topology_truncates_at_subnet_cap(client, db_session, test_project):
    """>cap subnets → truncated flag set and exactly cap subnet nodes returned."""
    from app.api.v1.endpoints.dashboard import _TOPO_SUBNET_CAP

    pid = test_project.id
    scope = models.Scope(project_id=pid, name="big")
    db_session.add(scope)
    db_session.flush()
    for i in range(_TOPO_SUBNET_CAP + 1):
        db_session.add(models.Subnet(scope_id=scope.id, cidr=f"10.{i // 256}.{i % 256}.0/24"))
    db_session.commit()

    body = client.get(f"/api/v1/projects/{pid}/dashboard/topology").json()
    assert body["truncated"] is True
    subnet_nodes = [n for n in body["nodes"] if n["type"] == "subnet"]
    assert len(subnet_nodes) == _TOPO_SUBNET_CAP
