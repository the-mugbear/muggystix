"""Review 2026-10-23b, ingestion: masscan on the one address rule (ING-1), its
bulk writes as the dedup service's set-based form of the per-row rules
(ING-7), and the web parsers' port memo keyed on the protocol (ING-10).

ING-7's proof is the equivalence test: the same observations written through
``upsert_hosts_bulk`` / ``upsert_ports_bulk`` and through
``find_or_create_host`` / ``find_or_create_port`` leave the same rows — every
held service name and confidence against every new name, a held state the
scan agrees or disagrees with, ports the scan creates.
"""
from __future__ import annotations

import json

import pytest

from app.db import models
from app.db.models_confidence import ConflictHistory
from app.db.models_project import Project
from app.parsers.masscan_parser import MasscanParser
from app.parsers.parser_utils import resolve_port_cached
from app.services.host_deduplication_service import HostDeduplicationService
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(not USING_POSTGRES, reason="the set-based form is PostgreSQL's")

TS = "1711938600"

# ---------------------------------------------------------------------------
# ING-7 — the bulk form is the per-row form
# ---------------------------------------------------------------------------

_HELD_NAMES = ("absent", None, "", "http", "http-proxy")
_HELD_CONFS = (None, 0, 10)
_NEW_NAMES = (None, "", "http", "https-alt")
_HOST_STATES = (None, "up", "down", "unknown", "absent")


def _cases():
    """``(port_number, held port or None, the observation)``: each held
    service against each new name, alternating the held state and reason."""
    cases, number = [], 1000
    for held_name in _HELD_NAMES:
        for held_conf in _HELD_CONFS if held_name != "absent" else (None,):
            for new_name in _NEW_NAMES:
                number += 1
                held = None if held_name == "absent" else {
                    "service_name": held_name, "service_conf": held_conf,
                    "service_product": "nginx", "service_version": "1.2", "service_tunnel": "ssl",
                    "state": ("open", "closed")[number % 2], "reason": ("syn-ack", None)[number % 3 == 0],
                }
                seen = {"port_number": number, "protocol": "tcp", "state": "open"}
                if number % 4:
                    seen["reason"] = ("syn-ack", "reset", None)[number % 3]
                if new_name is not None:
                    seen["service_name"] = new_name
                cases.append((number, held, seen))
    return cases


def _project(db, name):
    project = Project(name=name, slug=name, description="test")
    db.add(project)
    db.flush()
    held_scan = models.Scan(filename="held.xml", tool_name="nmap", scan_type="nmap_xml", project_id=project.id)
    scan = models.Scan(filename="new.txt", tool_name="masscan", scan_type="port_scan", project_id=project.id)
    db.add_all([held_scan, scan])
    db.flush()
    hosts = {}
    for idx, state in enumerate(_HOST_STATES):
        address = f"10.61.0.{idx + 1}"
        if state != "absent":
            host = models.Host(ip_address=address, state=state, project_id=project.id,
                               last_updated_scan_id=held_scan.id)
            db.add(host)
            db.flush()
            hosts[address] = host.id
    first = next(iter(hosts.values()))
    for number, held, _seen in _cases():
        if held is not None:
            db.add(models.Port(host_id=first, port_number=number, protocol="tcp",
                               last_updated_scan_id=held_scan.id, **held))
    db.flush()
    return project, held_scan, scan


def _rows(db, project, held_scan, scan):
    label = {held_scan.id: "held", scan.id: "new", None: None}
    hosts = {h.id: h for h in db.query(models.Host).filter_by(project_id=project.id)}
    ports = {p.id: p for p in db.query(models.Port).filter(models.Port.host_id.in_(hosts))}

    def port_key(port_id):
        p = ports[port_id]
        return (hosts[p.host_id].ip_address, p.port_number, p.protocol)

    return {
        "hosts": sorted(
            (h.ip_address, h.state, h.state_reason, label[h.last_updated_scan_id]) for h in hosts.values()
        ),
        "ports": sorted(
            (*port_key(p.id), p.state, p.reason, p.service_name, p.service_conf, p.service_product,
             p.service_version, p.service_tunnel, p.is_active, label[p.created_scan_id],
             label[p.last_updated_scan_id])
            for p in ports.values()
        ),
        "host_history": sorted(
            (hosts[r.host_id].ip_address, label[r.scan_id], r.state_at_scan, r.host_created, r.hostname_changed)
            for r in db.query(models.HostScanHistory).filter(models.HostScanHistory.host_id.in_(hosts))
        ),
        "port_history": sorted(
            (*port_key(r.port_id), label[r.scan_id], r.state_at_scan, r.service_name,
             json.loads(r.service_info) if r.service_info else None, r.port_created)
            for r in db.query(models.PortScanHistory).filter(models.PortScanHistory.port_id.in_(ports))
        ),
        "conflicts": sorted(
            (hosts[c.host_id].ip_address, c.field_name, c.previous_value, c.new_value,
             label[c.previous_scan_id], label[c.new_scan_id])
            for c in db.query(ConflictHistory).filter(ConflictHistory.host_id.in_(hosts))
        ),
    }


@pytest.mark.parametrize("twice", [False, True])
def test_the_bulk_form_writes_what_the_per_row_form_writes(db_session, twice):
    addresses = [f"10.61.0.{idx + 1}" for idx in range(len(_HOST_STATES))]

    bulk_project, bulk_held, bulk_scan = _project(db_session, "bulk")
    bulk = HostDeduplicationService(db_session)

    def bulk_write():
        host_ids = bulk.upsert_hosts_bulk(bulk_project.id, bulk_scan.id, {a: () for a in addresses})
        bulk.upsert_ports_bulk(bulk_scan.id, [(host_ids[addresses[0]], dict(seen)) for _n, _h, seen in _cases()])

    row_project, row_held, row_scan = _project(db_session, "per-row")
    per_row = HostDeduplicationService(db_session)

    def per_row_write():
        first = None
        for address in addresses:
            host = per_row.find_or_create_host(address, row_scan.id, {"state": "up"}, project_id=row_project.id)
            first = first or host
        db_session.flush()
        for _n, _h, seen in _cases():
            per_row.find_or_create_port(first.id, row_scan.id, dict(seen))
        db_session.flush()

    # Twice: the scan observed again (a repeat later in a large file, after
    # a flush) must find what it wrote the first time.
    for _ in range(2 if twice else 1):
        bulk_write()
        per_row_write()
    db_session.flush()
    db_session.expire_all()

    assert _rows(db_session, bulk_project, bulk_held, bulk_scan) == _rows(
        db_session, row_project, row_held, row_scan)


def test_the_bulk_form_takes_no_confidence(db_session):
    with pytest.raises(ValueError, match="find_or_create_port"):
        HostDeduplicationService(db_session).upsert_ports_bulk(
            1, [(1, {"port_number": 80, "protocol": "tcp", "state": "open", "service_conf": 10})])


# ---------------------------------------------------------------------------
# ING-1 — an address that is not one is a record not read, in every format
# ---------------------------------------------------------------------------

def _hosts(db, project_id):
    return sorted(ip for (ip,) in db.query(models.Host.ip_address).filter_by(project_id=project_id))


def _import(db, project_id, tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body)
    parser = MasscanParser(db)
    parser.parse_file(str(path), name, project_id=project_id)
    db.commit()
    return parser


def test_a_list_line_naming_no_address_is_counted_and_the_rest_imports(client, db_session, test_project, tmp_path):
    parser = _import(db_session, test_project.id, tmp_path, "m.txt", (
        "#masscan\n"
        f"open tcp 80 10.62.0.1 {TS}\n"
        f"open tcp 80 web01 {TS}\n"
        f"banner tcp 80 web01 {TS} http Server: x\n"
        f"Timestamp: {TS}\tHost: also-not-an-address ()\tPorts: 80/open/tcp//http//\n"
    ))
    assert _hosts(db_session, test_project.id) == ["10.62.0.1"]
    assert parser.last_parse_stats["skipped"] == 3
    assert parser.last_parse_stats["partial"] is True
    assert "3 lines" in parser.last_parse_stats["warnings"]
    assert client.get(f"/api/v1/projects/{test_project.id}/hosts/").status_code == 200


def test_a_json_record_naming_no_address_is_counted(db_session, test_project, tmp_path):
    parser = _import(db_session, test_project.id, tmp_path, "m.json", json.dumps([
        {"ip": "10.62.1.1", "timestamp": TS, "ports": [{"port": 22, "proto": "tcp", "status": "open"}]},
        {"ip": "web01", "timestamp": TS, "ports": [{"port": 22, "proto": "tcp", "status": "open"}]},
    ]))
    assert _hosts(db_session, test_project.id) == ["10.62.1.1"]
    assert parser.last_parse_stats["skipped"] == 1
    assert "1 record of the file named no IP address" in parser.last_parse_stats["warnings"]


def test_an_xml_host_naming_no_address_is_counted(db_session, test_project, tmp_path):
    def host(addr):
        return (f'<host><address addr="{addr}" addrtype="ipv4"/><ports><port protocol="tcp" portid="443">'
                '<state state="open" reason="syn-ack"/></port></ports></host>')

    parser = _import(db_session, test_project.id, tmp_path, "m.xml", (
        '<?xml version="1.0"?><nmaprun scanner="masscan">' + host("10.62.2.1") + host("web01") + "</nmaprun>"
    ))
    assert _hosts(db_session, test_project.id) == ["10.62.2.1"]
    assert parser.last_parse_stats["skipped"] == 1


def test_a_file_naming_no_address_at_all_imports_nothing(db_session, test_project, tmp_path):
    with pytest.raises(ValueError, match="0 hosts"):
        _import(db_session, test_project.id, tmp_path, "m.txt", f"#masscan\nopen tcp 80 web01 {TS}\n")


def test_one_ipv6_address_written_two_ways_is_one_host(db_session, test_project, tmp_path):
    pid = test_project.id
    db_session.add(models.Host(ip_address="2001:db8::1", state="up", project_id=pid))
    # Stored before addresses were normalised: still found by its spelling.
    db_session.add(models.Host(ip_address="2001:DB8::5", state="up", project_id=pid))
    db_session.commit()
    _import(db_session, pid, tmp_path, "v6.txt", (
        "#masscan\n"
        f"open tcp 80 2001:DB8:0::1 {TS}\n"
        f"open tcp 443 2001:db8:0:0::1 {TS}\n"
        f"open tcp 22 2001:DB8::5 {TS}\n"
        f"banner tcp 22 2001:DB8::5 {TS} ssh SSH-2.0-x\n"
        f"open tcp 25 2001:DB8::9 {TS}\n"
    ))
    assert _hosts(db_session, pid) == ["2001:DB8::5", "2001:db8::1", "2001:db8::9"]
    ports = dict(
        db_session.query(models.Port.port_number, models.Host.ip_address)
        .join(models.Host, models.Host.id == models.Port.host_id).filter(models.Host.project_id == pid)
    )
    assert ports == {80: "2001:db8::1", 443: "2001:db8::1", 22: "2001:DB8::5", 25: "2001:db8::9"}
    banner = (
        db_session.query(models.Script.output).join(models.Port, models.Port.id == models.Script.port_id)
        .filter(models.Port.port_number == 22, models.Script.script_id == "masscan-ssh").scalar()
    )
    assert banner == "SSH-2.0-x"


def test_a_host_brought_back_up_records_the_disagreement(db_session, test_project, tmp_path):
    """ING-7's drift: the bulk path wrote no conflict row for down → up."""
    pid = test_project.id
    db_session.add(models.Host(ip_address="10.62.3.1", state="down", project_id=pid))
    db_session.commit()
    _import(db_session, pid, tmp_path, "up.txt", f"#masscan\nopen tcp 80 10.62.3.1 {TS}\n")
    _import(db_session, pid, tmp_path, "up2.txt", f"#masscan\nopen tcp 81 10.62.3.1 {TS}\n")
    rows = db_session.query(ConflictHistory.field_name, ConflictHistory.previous_value, ConflictHistory.new_value).all()
    assert rows == [("state", "down", "up")]  # once, however often it is seen


def test_a_later_line_naming_the_service_is_not_dropped(db_session, test_project, tmp_path):
    _import(db_session, test_project.id, tmp_path, "svc.txt", (
        "#masscan\n"
        f"open tcp 8080 10.62.4.1 {TS}\n"
        f"Timestamp: {TS}\tHost: 10.62.4.1 ()\tPorts: 8080/open/tcp//http-alt//\n"
    ))
    assert db_session.query(models.Port.service_name).filter(models.Port.port_number == 8080).scalar() == "http-alt"


# ---------------------------------------------------------------------------
# ING-5 — every import's hosts are correlated, by the dispatcher, once
# ---------------------------------------------------------------------------

def _every_import_case():
    from tests.test_every_import_records_the_hosts_it_touches import CASES
    return sorted(CASES)


@pytest.mark.parametrize("file_type", _every_import_case())
def test_every_import_correlates_the_hosts_it_recorded(db_session, test_project, tmp_path, monkeypatch, file_type):
    """The rule the parsers each kept (about 20 copies, some only when they
    created a host, one never until 2.342.0) is now the dispatcher's: every
    host an import recorded is mapped to the scope subnets that contain it."""
    from app.core.config import settings
    from tests.test_every_import_records_the_hosts_it_touches import CASES, _import

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    pid = test_project.id
    scope = models.Scope(project_id=pid)
    db_session.add(scope)
    db_session.flush()
    db_session.add_all([models.Subnet(scope_id=scope.id, cidr="0.0.0.0/0"),
                        models.Subnet(scope_id=scope.id, cidr="::/0")])
    db_session.commit()

    scan_id = _import(db_session, pid, tmp_path, file_type, CASES[file_type])
    recorded = {
        host_id for (host_id,) in
        db_session.query(models.HostScanHistory.host_id).filter(models.HostScanHistory.scan_id == scan_id)
    }
    mapped = {
        host_id for (host_id,) in
        db_session.query(models.HostSubnetMapping.host_id).filter(models.HostSubnetMapping.host_id.in_(recorded))
    }
    assert recorded and mapped == recorded


def test_a_failed_correlation_never_fails_the_import(db_session, test_project, tmp_path, monkeypatch):
    from app.services.subnet_correlation import SubnetCorrelationService
    from tests.ingestion_job_harness import run_file

    def broken(self, scan_id):
        raise RuntimeError("correlation is down")

    monkeypatch.setattr(SubnetCorrelationService, "batch_correlate_scan_hosts_to_subnets", broken)
    path = tmp_path / "r.txt"
    path.write_text("Open 10.63.0.1:22\n")
    job = run_file(db_session, test_project.id, path)
    assert job.status == "completed", job.error_message
    assert _hosts(db_session, test_project.id) == ["10.63.0.1"]


# ---------------------------------------------------------------------------
# ING-8 — the Nessus writes isolate through the one record savepoint
# ---------------------------------------------------------------------------

def test_an_informational_port_that_fails_loses_no_finding(db_session, test_project, tmp_path, monkeypatch):
    """The informational items' ports were written in a loop that also chose
    which findings to keep: a failure part-way dropped every finding after
    it, silently (not counted as a write failure)."""
    from app.db.models_vulnerability import Vulnerability
    from app.services.nessus_integration_service import NessusIntegrationService
    from app.services.vulnerability_service import VulnerabilityService
    from tests.test_nessus_skip_informational import NESSUS_XML

    real = VulnerabilityService._get_or_create_port
    calls = []

    def first_port_fails(self, *args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("the first informational port cannot be written")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(VulnerabilityService, "_get_or_create_port", first_port_fails)
    path = tmp_path / "info.nessus"
    path.write_text(NESSUS_XML)
    result = NessusIntegrationService(db_session).process_nessus_file(
        str(path), project_id=test_project.id, skip_informational=True,
    )
    assert result["informational_skipped"] == 3
    assert result["vuln_write_failures"] == 0
    plugins = sorted(p for (p,) in db_session.query(Vulnerability.plugin_id))
    assert plugins == ["42873", "90317"]


# ---------------------------------------------------------------------------
# ING-10 — the web parsers' port memo is keyed on the protocol too
# ---------------------------------------------------------------------------

def test_the_port_memo_tells_tcp_from_udp(db_session, test_project):
    host = models.Host(ip_address="10.62.5.1", state="up", project_id=test_project.id)
    db_session.add(host)
    db_session.flush()
    tcp = models.Port(host_id=host.id, port_number=53, protocol="tcp", state="open")
    udp = models.Port(host_id=host.id, port_number=53, protocol="udp", state="open")
    db_session.add_all([tcp, udp])
    db_session.flush()
    cache: dict = {}
    assert resolve_port_cached(db_session, host, 53, cache) is tcp
    assert resolve_port_cached(db_session, host, 53, cache, protocol="udp") is udp
