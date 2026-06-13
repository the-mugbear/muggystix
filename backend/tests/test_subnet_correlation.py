"""Subnet correlation correctness (audit A1).

Pins the trie-based `_batch_correlate_hosts` rewrite: every containing subnet is
mapped (overlapping /24 + /16 both match), out-of-range hosts get nothing,
invalid IPs/CIDRs are skipped, and — the cross-family fix — an IPv4 host does
NOT match an IPv6 ``::/0`` (the old raw-integer loop did).

No existing coverage for this service; this is net-new.
"""
from app.db import models
from app.services.subnet_correlation import SubnetCorrelationService


def _scope_with_subnets(db, project_id, cidrs):
    scope = models.Scope(project_id=project_id, name="s")
    db.add(scope)
    db.flush()
    subnets = {}
    for cidr in cidrs:
        sub = models.Subnet(scope_id=scope.id, cidr=cidr)
        db.add(sub)
        db.flush()
        subnets[cidr] = sub.id
    return subnets


def _host(db, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _mappings_by_host(db, host_ids, subnet_id_to_cidr):
    """Return {host_id: set(cidr)} from the persisted HostSubnetMapping rows."""
    rows = (
        db.query(models.HostSubnetMapping)
        .filter(models.HostSubnetMapping.host_id.in_(host_ids))
        .all()
    )
    out = {hid: set() for hid in host_ids}
    for r in rows:
        out[r.host_id].add(subnet_id_to_cidr[r.subnet_id])
    return out


def test_correlation_maps_all_containing_subnets(db_session, test_project):
    subnets = _scope_with_subnets(
        db_session,
        test_project.id,
        ["10.0.0.0/24", "10.0.0.0/16", "192.168.1.0/24", "2001:db8::/32", "::/0", "not-a-cidr"],
    )
    id_to_cidr = {sid: cidr for cidr, sid in subnets.items()}

    hosts = {
        "10.0.0.5": _host(db_session, test_project.id, "10.0.0.5"),       # /24 + /16
        "10.0.5.5": _host(db_session, test_project.id, "10.0.5.5"),       # /16 only
        "192.168.1.10": _host(db_session, test_project.id, "192.168.1.10"),  # /24
        "172.16.0.1": _host(db_session, test_project.id, "172.16.0.1"),   # none (NOT ::/0)
        "2001:db8::1": _host(db_session, test_project.id, "2001:db8::1"), # v6 /32 + ::/0
        "bad-ip": _host(db_session, test_project.id, "not-an-ip"),        # skipped
    }
    db_session.commit()

    n = SubnetCorrelationService(db_session).correlate_all_hosts_to_subnets(
        project_id=test_project.id
    )

    host_ids = [h.id for h in hosts.values()]
    got = _mappings_by_host(db_session, host_ids, id_to_cidr)

    assert got[hosts["10.0.0.5"].id] == {"10.0.0.0/24", "10.0.0.0/16"}
    assert got[hosts["10.0.5.5"].id] == {"10.0.0.0/16"}
    assert got[hosts["192.168.1.10"].id] == {"192.168.1.0/24"}
    # The cross-family fix: an IPv4 host must NOT match the IPv6 ::/0.
    assert got[hosts["172.16.0.1"].id] == set()
    assert got[hosts["2001:db8::1"].id] == {"2001:db8::/32", "::/0"}
    assert got[hosts["bad-ip"].id] == set()

    # Return value is the total mapping count.
    assert n == 2 + 1 + 1 + 0 + 2 + 0


def test_correlation_is_idempotent_and_drops_stale_mappings(db_session, test_project):
    subnets = _scope_with_subnets(db_session, test_project.id, ["10.0.0.0/24", "10.0.0.0/16"])
    host = _host(db_session, test_project.id, "10.0.0.9")
    db_session.commit()
    svc = SubnetCorrelationService(db_session)

    svc.correlate_all_hosts_to_subnets(project_id=test_project.id)
    svc.correlate_all_hosts_to_subnets(project_id=test_project.id)  # re-run: no dup rows
    rows = (
        db_session.query(models.HostSubnetMapping)
        .filter(models.HostSubnetMapping.host_id == host.id)
        .all()
    )
    assert len(rows) == 2  # matches both /24 and /16, no duplicates

    # Move the /16 out from under the host → re-correlate (delete + reinsert)
    # must drop the now-stale mapping while keeping the /24.
    db_session.query(models.Subnet).filter(
        models.Subnet.id == subnets["10.0.0.0/16"]
    ).update({"cidr": "172.16.0.0/16"})
    db_session.commit()
    svc.correlate_all_hosts_to_subnets(project_id=test_project.id)

    rows = (
        db_session.query(models.HostSubnetMapping)
        .filter(models.HostSubnetMapping.host_id == host.id)
        .all()
    )
    assert {r.subnet_id for r in rows} == {subnets["10.0.0.0/24"]}


def test_correlate_subnet_adds_only_its_own_mappings(db_session, test_project):
    """correlate_subnet maps project hosts to ONE subnet without disturbing
    mappings to other subnets (the incremental path for a single-subnet add)."""
    subnets = _scope_with_subnets(db_session, test_project.id, ["10.0.0.0/24", "192.168.1.0/24"])
    h_a = _host(db_session, test_project.id, "10.0.0.5")
    h_b = _host(db_session, test_project.id, "192.168.1.10")
    db_session.commit()
    svc = SubnetCorrelationService(db_session)
    svc.correlate_all_hosts_to_subnets(project_id=test_project.id)

    # Add a NEW overlapping /16 and correlate ONLY it.
    new_sub = models.Subnet(
        scope_id=db_session.query(models.Subnet).get(subnets["10.0.0.0/24"]).scope_id,
        cidr="10.0.0.0/16",
    )
    db_session.add(new_sub)
    db_session.flush()
    db_session.commit()

    n = svc.correlate_subnet(new_sub.id)
    assert n == 1  # only h_a falls in 10.0.0.0/16

    db_session.expire_all()
    a_subs = {r.subnet_id for r in db_session.query(models.HostSubnetMapping).filter_by(host_id=h_a.id)}
    b_subs = {r.subnet_id for r in db_session.query(models.HostSubnetMapping).filter_by(host_id=h_b.id)}
    # h_a keeps its /24 AND gains the new /16; h_b's mapping is untouched.
    assert a_subs == {subnets["10.0.0.0/24"], new_sub.id}
    assert b_subs == {subnets["192.168.1.0/24"]}


def test_correlate_subnet_cidr_change_drops_only_its_stale_mappings(db_session, test_project):
    """A CIDR edit recomputes just that subnet's mappings (drops hosts that no
    longer match) and leaves every other subnet's mappings alone."""
    subnets = _scope_with_subnets(db_session, test_project.id, ["10.0.0.0/24", "192.168.1.0/24"])
    h_a = _host(db_session, test_project.id, "10.0.0.5")
    h_b = _host(db_session, test_project.id, "192.168.1.10")
    db_session.commit()
    svc = SubnetCorrelationService(db_session)
    svc.correlate_all_hosts_to_subnets(project_id=test_project.id)

    # Move the 10.0.0.0/24 subnet off h_a, then re-correlate just it.
    db_session.query(models.Subnet).filter_by(id=subnets["10.0.0.0/24"]).update(
        {"cidr": "172.16.9.0/24"}
    )
    db_session.commit()
    svc.correlate_subnet(subnets["10.0.0.0/24"])

    db_session.expire_all()
    a_subs = {r.subnet_id for r in db_session.query(models.HostSubnetMapping).filter_by(host_id=h_a.id)}
    b_subs = {r.subnet_id for r in db_session.query(models.HostSubnetMapping).filter_by(host_id=h_b.id)}
    assert a_subs == set()                              # no longer matches the moved subnet
    assert b_subs == {subnets["192.168.1.0/24"]}        # untouched
