"""dnsx zone transfers and `all` (review 2026-10-01 B12).

The shapes are dnsx's own, from retryabledns ``DNSData`` / ``AXFRData``:
``axfr`` is ``{"host": zone, "chain": [DNSData, …]}`` and ``all`` is an array
of resource records as miekg/dns prints them (tab-separated).  The parser read
``axfr`` only as an array of strings and looked for a key named ``any``, so a
successful zone transfer left nothing.
"""
from __future__ import annotations

import json

from app.db import models
from app.parsers.content_detection import looks_like_dnsx
from app.parsers.dnsx_parser import DnsxParser, parse_rr_text

ZONE = "corp.example"
NS = "10.9.0.53:53"


def _rr(owner, rtype, rdata, ttl=300):
    return f"{owner}.\t{ttl}\tIN\t{rtype}\t{rdata}"


def _axfr_row():
    """What `dnsx -axfr -j` writes for a zone whose server allows the transfer
    (the typed arrays of a chain entry carry no owner names)."""
    return {
        "host": ZONE,
        "resolver": ["1.1.1.1:53"],
        "status_code": "NOERROR",
        "timestamp": "2026-09-23T00:47:46.540976857Z",
        "axfr": {
            "host": ZONE,
            "chain": [{
                "host": ZONE,
                "ttl": 300,
                "resolver": [NS],
                "a": ["10.9.0.10", "10.9.0.20", "10.9.0.99"],
                "cname": ["www.corp.example"],
                "mx": ["mail.corp.example"],
                "txt": ["v=spf1 -all"],
                "soa": [{"name": ZONE, "ns": "ns1.corp.example", "mailbox": "admin.corp.example", "serial": 7}],
                "all": [
                    _rr(ZONE, "SOA", "ns1.corp.example. admin.corp.example. 7 3600 600 86400 300"),
                    _rr("www.corp.example", "A", "10.9.0.10"),
                    _rr("vpn.corp.example", "A", "10.9.0.20"),
                    _rr("*.dev.corp.example", "A", "10.9.0.99"),
                    _rr("intranet.corp.example", "CNAME", "www.corp.example."),
                    _rr(ZONE, "MX", "10 mail.corp.example."),
                    _rr(ZONE, "TXT", '"v=spf1 " "-all"'),
                    _rr("db.corp.example", "HINFO", '"x86" "linux"'),
                    _rr(ZONE, "SOA", "ns1.corp.example. admin.corp.example. 7 3600 600 86400 300"),
                ],
                "timestamp": "2026-09-23T00:47:47.1Z",
            }],
        },
    }


def _write(tmp_path, name, records):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _records(db, project_id):
    rows = db.query(models.DNSRecord).filter(models.DNSRecord.project_id == project_id).all()
    return {(r.domain, r.record_type, r.value) for r in rows}, rows


def test_rr_text_is_read_as_owner_ttl_type_value():
    rr = parse_rr_text("servimus.home.\t0\tIN\tA\t192.168.7.222")  # a real dnsx capture line
    assert (rr.owner, rr.ttl, rr.rtype, rr.value) == ("servimus.home", 0, "A", "192.168.7.222")
    assert parse_rr_text(_rr("a.example", "TXT", '"one" "two"')).value == "onetwo"
    assert parse_rr_text(_rr("a.example", "MX", "10 mail.a.example.")).value == "10 mail.a.example"
    assert parse_rr_text("a.example. 60 IN NS ns1.a.example.").value == "ns1.a.example"
    assert parse_rr_text(";.\t0\tCLASS1232\tOPT\t") is None
    assert parse_rr_text("not a record") is None
    assert parse_rr_text(None) is None


def test_a_zone_transfer_is_kept_record_by_record(db_session, test_project, tmp_path):
    pid = test_project.id
    path = _write(tmp_path, "axfr.jsonl", [_axfr_row()])
    parser = DnsxParser(db_session)
    scan = parser.parse_file(str(path), path.name, project_id=pid)
    seen, rows = _records(db_session, pid)

    # Every record under its OWN owner name — not the zone's.
    assert ("www.corp.example", "A", "10.9.0.10") in seen
    assert ("vpn.corp.example", "A", "10.9.0.20") in seen
    assert ("intranet.corp.example", "CNAME", "www.corp.example") in seen
    assert (ZONE, "MX", "10 mail.corp.example") in seen
    assert (ZONE, "TXT", "v=spf1 -all") in seen
    assert ("db.corp.example", "HINFO", '"x86" "linux"') in seen
    # The chain entry's owner-less typed arrays are not pinned on the zone.
    assert (ZONE, "A", "10.9.0.10") not in seen
    assert (ZONE, "CNAME", "www.corp.example") not in seen
    # The transfer itself, attributed to the server that allowed it.
    marker = [r for r in rows if r.record_type == "AXFR"]
    assert [(m.domain, m.value, m.resolver_name) for m in marker] == [
        (ZONE, "zone transfer allowed (9 records)", NS)
    ]
    assert all(r.scan_id == scan.id for r in rows)
    transferred = next(r for r in rows if r.domain == "vpn.corp.example")
    assert transferred.resolver_name == NS and transferred.ttl == 300

    # Addresses in the zone are discovered hosts, named by their owner; a
    # wildcard owner names nothing.
    hosts = {h.ip_address: h.hostname for h in db_session.query(models.Host).filter_by(project_id=pid)}
    assert hosts["10.9.0.10"] == "www.corp.example"
    assert hosts["10.9.0.20"] == "vpn.corp.example"
    assert hosts.get("10.9.0.99") is None

    assert "Zone transfer allowed: corp.example by 10.9.0.53:53 (9 records)" in parser.last_parse_stats["warnings"]


def test_a_row_that_carries_only_a_transfer_is_an_import(db_session, test_project, tmp_path):
    """`dnsx -axfr -j` alone: no typed array at the top, and the resolver's
    own answer may be a refusal."""
    row = _axfr_row()
    row["status_code"] = "REFUSED"
    path = _write(tmp_path, "zones.jsonl", [row])
    assert looks_like_dnsx(path.read_bytes(), "zones.jsonl")
    DnsxParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)
    seen, _ = _records(db_session, test_project.id)
    assert ("vpn.corp.example", "A", "10.9.0.20") in seen


def test_a_reverse_zone_transfer_names_the_hosts(db_session, test_project, tmp_path):
    pid = test_project.id
    row = {
        "host": "0.9.10.in-addr.arpa",
        "axfr": {"host": "0.9.10.in-addr.arpa", "chain": [{
            "resolver": [NS],
            "ptr": ["fs01.corp.example"],
            "all": [_rr("30.0.9.10.in-addr.arpa", "PTR", "fs01.corp.example.")],
        }]},
    }
    path = _write(tmp_path, "rev.jsonl", [row])
    DnsxParser(db_session).parse_file(str(path), path.name, project_id=pid)
    seen, _ = _records(db_session, pid)
    assert ("fs01.corp.example", "PTR", "10.9.0.30") in seen
    host = db_session.query(models.Host).filter_by(project_id=pid, ip_address="10.9.0.30").one()
    assert host.hostname == "fs01.corp.example"


def test_a_chain_entry_without_owner_names_invents_none(db_session, test_project, tmp_path):
    row = {"host": ZONE, "axfr": {"host": ZONE, "chain": [{"resolver": [NS], "a": ["10.9.0.10"]}]}}
    path = _write(tmp_path, "bare.jsonl", [row])
    DnsxParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)
    seen, _ = _records(db_session, test_project.id)
    assert (ZONE, "AXFR", "A 10.9.0.10") in seen
    assert (ZONE, "A", "10.9.0.10") not in seen
    assert db_session.query(models.Host).filter_by(project_id=test_project.id).count() == 0


def test_all_adds_the_types_with_no_array_and_repeats_nothing(db_session, test_project, tmp_path):
    """`all` repeats the typed answers in another spelling; only the types
    dnsx has no array for are read from it."""
    row = {
        "host": "example.com", "resolver": ["1.1.1.1:53"], "status_code": "NOERROR",
        "a": ["93.184.216.34"], "mx": ["mail.example.com"],
        "all": [
            _rr("example.com", "A", "93.184.216.34"),
            _rr("example.com", "MX", "10 mail.example.com."),
            _rr("example.com", "HINFO", '"RFC8482" ""'),
            _rr("_443._tcp.example.com", "TLSA", "3 1 1 ABCDEF"),
        ],
    }
    path = _write(tmp_path, "any.jsonl", [row])
    DnsxParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)
    seen, rows = _records(db_session, test_project.id)
    assert ("example.com", "HINFO", '"RFC8482" ""') in seen
    assert ("_443._tcp.example.com", "TLSA", "3 1 1 ABCDEF") in seen
    assert [r.value for r in rows if r.record_type == "A"] == ["93.184.216.34"]
    assert [r.value for r in rows if r.record_type == "MX"] == ["mail.example.com"]


def test_a_row_with_only_all_is_not_dropped(db_session, test_project, tmp_path):
    row = {"host": "example.com", "status_code": "NOERROR", "all": [_rr("example.com", "DNSKEY", "257 3 13 abc=")]}
    path = _write(tmp_path, "dnsx-keys.jsonl", [row])
    DnsxParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)
    seen, _ = _records(db_session, test_project.id)
    assert ("example.com", "DNSKEY", "257 3 13 abc=") in seen


def test_the_older_array_shapes_still_import(db_session, test_project, tmp_path):
    """Not dnsx's shapes, but read before this change: kept."""
    row = {"host": "example.com", "any": ["some answer"], "axfr": ["www.example.com. 300 IN A 10.0.0.1"]}
    path = _write(tmp_path, "dnsx-old.jsonl", [row])
    DnsxParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)
    seen, _ = _records(db_session, test_project.id)
    assert ("example.com", "ANY", "some answer") in seen
    assert ("example.com", "AXFR", "www.example.com. 300 IN A 10.0.0.1") in seen


# --- the transfer as a weakness of the name server ---------------------------

NS_IP = "10.9.0.53"


def _checks(db, project_id):
    from app.db.models_vulnerability import Vulnerability

    return (
        db.query(Vulnerability).join(models.Host, models.Host.id == Vulnerability.host_id)
        .filter(models.Host.project_id == project_id,
                Vulnerability.check_id == "dns_zone_transfer_allowed")
        .all()
    )


def _name_server(db, project_id, *, with_port=True):
    host = models.Host(project_id=project_id, ip_address=NS_IP, state="up")
    db.add(host)
    db.flush()
    port = None
    if with_port:
        port = models.Port(host_id=host.id, port_number=53, protocol="tcp", state="open")
        db.add(port)
    db.commit()
    return host, port


def test_an_allowed_transfer_is_a_weakness_of_the_name_server(db_session, test_project, tmp_path):
    """It was a line in the import's warnings and an AXFR row on the zone:
    nothing among the server's weaknesses, nothing on Findings."""
    from app.db.models_vulnerability import VulnerabilitySeverity, VulnerabilitySource

    pid = test_project.id
    host, port = _name_server(db_session, pid)
    path = _write(tmp_path, "axfr.jsonl", [_axfr_row()])
    scan = DnsxParser(db_session).parse_file(str(path), path.name, project_id=pid)

    [vuln] = _checks(db_session, pid)
    assert (vuln.host_id, vuln.port_id, vuln.scan_id) == (host.id, port.id, scan.id)
    assert vuln.source == VulnerabilitySource.DNSX
    assert vuln.severity == VulnerabilitySeverity.MEDIUM
    assert vuln.title == "DNS zone transfer allowed"
    assert vuln.plugin_output == "Zone corp.example: transfer allowed by 10.9.0.53:53 (9 records)"


def test_the_check_is_host_level_when_the_server_has_no_dns_port_row(db_session, test_project, tmp_path):
    """The server's host came from this same import (the zone's own A record
    for it); no 53/tcp row exists and none is invented."""
    pid = test_project.id
    row = _axfr_row()
    row["axfr"]["chain"][0]["all"].insert(1, _rr("ns1.corp.example", "A", NS_IP))
    path = _write(tmp_path, "axfr.jsonl", [row])
    DnsxParser(db_session).parse_file(str(path), path.name, project_id=pid)

    [vuln] = _checks(db_session, pid)
    host = db_session.query(models.Host).filter_by(project_id=pid, ip_address=NS_IP).one()
    assert vuln.host_id == host.id and vuln.port_id is None
    assert db_session.query(models.Port).filter_by(host_id=host.id).count() == 0


def test_a_server_that_is_not_a_host_gets_no_observation_and_no_host(db_session, test_project, tmp_path):
    """The resolver is where the operator pointed dnsx; nothing says it is in
    scope.  The fact stays on the zone and in the import's warnings."""
    pid = test_project.id
    path = _write(tmp_path, "axfr.jsonl", [_axfr_row()])
    parser = DnsxParser(db_session)
    parser.parse_file(str(path), path.name, project_id=pid)

    assert _checks(db_session, pid) == []
    assert db_session.query(models.Host).filter_by(project_id=pid, ip_address=NS_IP).count() == 0
    assert "Zone transfer allowed: corp.example by 10.9.0.53:53" in parser.last_parse_stats["warnings"]


def test_importing_the_transfer_again_does_not_repeat_the_observation(db_session, test_project, tmp_path):
    pid = test_project.id
    _name_server(db_session, pid)
    for name in ("first.jsonl", "second.jsonl"):
        path = _write(tmp_path, name, [_axfr_row()])
        DnsxParser(db_session).parse_file(str(path), path.name, project_id=pid)
    assert len(_checks(db_session, pid)) == 1


def test_a_row_that_fails_takes_its_observation_with_it(db_session, test_project, tmp_path, monkeypatch):
    """The check is written inside the row's savepoint: a row skipped for a
    later failure leaves no observation and no claim in the warnings."""
    pid = test_project.id
    _name_server(db_session, pid)
    bad = _axfr_row()
    bad["a"] = ["10.9.0.200"]  # read AFTER the transfer, by the top-level arrays
    good = {"host": "other.example", "resolver": ["1.1.1.1:53"], "a": ["10.9.7.7"]}

    real = DnsxParser._update_host_hostname

    def fail_on_the_bad_row(self, scan_id, ip_address, hostname, *, source):
        if ip_address == "10.9.0.200":
            raise RuntimeError("boom")
        return real(self, scan_id, ip_address, hostname, source=source)

    monkeypatch.setattr(DnsxParser, "_update_host_hostname", fail_on_the_bad_row)
    path = _write(tmp_path, "mixed.jsonl", [bad, good])
    parser = DnsxParser(db_session)
    parser.parse_file(str(path), path.name, project_id=pid)

    assert _checks(db_session, pid) == []
    seen, _ = _records(db_session, pid)
    assert ("other.example", "A", "10.9.7.7") in seen
    assert "Zone transfer allowed" not in (parser.last_parse_stats.get("warnings") or "")


def test_resolver_strings_are_read_as_address_and_port():
    from app.parsers.dnsx_parser import _resolver_endpoint

    assert _resolver_endpoint("10.9.0.53:53") == ("10.9.0.53", 53)
    assert _resolver_endpoint("10.9.0.53") == ("10.9.0.53", 53)
    assert _resolver_endpoint("[2001:db8::53]:5353") == ("2001:db8::53", 5353)
    assert _resolver_endpoint("2001:db8::53") == ("2001:db8::53", 53)
    assert _resolver_endpoint("ns1.corp.example:53") is None
    assert _resolver_endpoint("unknown server") is None
    assert _resolver_endpoint(None) is None
