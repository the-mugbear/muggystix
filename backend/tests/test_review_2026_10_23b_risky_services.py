"""One rule for "a risky exposed service" (review 2026-10-23b, FEP-4; owner,
2026-10-23: "one rule everywhere").

The Hosts list's Exposure chips recognised a PROBED service name on any port
(RDP on 3390), while the subnet insight's risky services and the Operations
queue's high-value ports went by port number only — so a host could carry an
"RDP" chip and be outside its subnet's risky count and the queue's tier 4.
All three now read ``ports_of_interest.port_of_interest``; its SQL form,
``port_of_interest_condition``, is pinned to it row by row.
"""
from datetime import datetime, timedelta, timezone

from app.db import models
from app.services.operations_read_service import compute_investigation_queue
from app.services.ports_of_interest import (
    exposure_chips, port_of_interest, port_of_interest_condition, ports_by_number,
)
from app.services.subnet_insight_service import compute_subnet_insights

# (port number, state, service name, method) → the port of interest it exposes.
CASES = [
    (3389, "open", None, None, 3389),                   # the number decides
    (3389, "open", "ms-wbt-server", "table", 3389),     # nmap's table guess: the number
    (3390, "open", "ms-wbt-server", "probed", 3389),    # probed RDP on another port
    (3390, "open", " RDP ", "probed", 3389),            # trimmed, any case
    (3389, "open", "ssh", "probed", 22),                # a probed name wins over the number
    (3389, "open", "http", "probed", None),             # a probed name that is no port of interest
    (3389, "open", "", "probed", 3389),                 # probed but unnamed: the number
    (8080, "open", "http-proxy", "table", None),
    (3390, "open", "ms-wbt-server", None, None),        # unprobed name on another port: nothing
    (3389, "closed", "ms-wbt-server", "probed", None),  # only an OPEN port
]


def _host(db, project_id, ip, ports, *, first_seen=None):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    h.first_seen = first_seen or datetime.now(timezone.utc) - timedelta(days=30)
    db.add(h)
    db.flush()
    for number, state, name, method in ports:
        db.add(models.Port(host_id=h.id, port_number=number, protocol="tcp", state=state,
                           service_name=name, service_method=method))
    scan = models.Scan(project_id=project_id, filename=f"{ip}.xml", tool_name="nmap")
    db.add(scan)
    db.flush()
    db.add(models.HostScanHistory(host_id=h.id, scan_id=scan.id, state_at_scan="up",
                                  discovered_at=datetime.now(timezone.utc)))
    db.flush()
    return h


def test_the_sql_form_is_the_rule_row_by_row(db_session, test_project):
    # One host per case: a host holds a port number / protocol once.
    hosts = [_host(db_session, test_project.id, f"10.77.0.{i}", [case[:4]]) for i, case in enumerate(CASES, 1)]
    db_session.commit()
    host_ids = [h.id for h in hosts]
    matched = {
        pid for (pid,) in db_session.query(models.Port.id)
        .filter(models.Port.host_id.in_(host_ids), port_of_interest_condition())
    }
    for host, (number, state, name, method, expected) in zip(hosts, CASES):
        (port,) = db_session.query(models.Port).filter(models.Port.host_id == host.id).all()
        poi = port_of_interest(port)
        assert (poi.port if poi else None) == expected, (number, state, name, method)
        assert (port.id in matched) == (expected is not None), (number, state, name, method)


def test_rdp_on_another_port_is_risky_on_the_chips_the_subnet_and_operations(db_session, test_project):
    pid = test_project.id
    scope = models.Scope(project_id=pid)
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr="10.78.0.0/24")
    db_session.add(subnet)
    db_session.flush()
    now = datetime.now(timezone.utc)
    rdp = _host(db_session, pid, "10.78.0.1", [(3390, "open", "ms-wbt-server", "probed")],
                first_seen=now - timedelta(days=1))
    ssh = _host(db_session, pid, "10.78.0.2", [(3389, "open", "ssh", "probed")],
                first_seen=now - timedelta(days=1))
    web = _host(db_session, pid, "10.78.0.3", [(3389, "open", "http", "probed")],
                first_seen=now - timedelta(days=1))
    for h in (rdp, ssh, web):
        db_session.add(models.HostSubnetMapping(host_id=h.id, subnet_id=subnet.id))
    db_session.commit()

    # The chips.
    chips = {h.ip_address: [c["label"] for c in exposure_chips(h.ports) if c["weight"]] for h in (rdp, ssh, web)}
    assert chips == {"10.78.0.1": ["RDP"], "10.78.0.2": ["SSH"], "10.78.0.3": []}

    # The subnet insight: the same two hosts, each under its port of interest.
    row = next(r for r in compute_subnet_insights(db_session, pid)["subnets"] if r["subnet_id"] == subnet.id)
    hygiene = row["hygiene"]
    assert hygiene["risky_service_hosts"] == 2                     # was 2, but ssh + web (by number)
    assert {(r["label"], r["host_count"]) for r in hygiene["risky_services"]} == {
        (ports_by_number()[3389].label, 1), (ports_by_number()[22].label, 1),
    }

    # The Operations queue: new, untouched, a high-value port → tier 4, with
    # the chip's word as its reason.
    q = compute_investigation_queue(db_session, test_project, limit=25)
    tier4 = {r.ip_address: r for r in q.items if r.tier == 4}
    assert set(tier4) == {"10.78.0.1", "10.78.0.2"}                # 10.78.0.1 was missing
    assert any("RDP" in reason.text for reason in tier4["10.78.0.1"].reasons)
