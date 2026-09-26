"""Staff parser-architecture review, 2026-09-25 (v2.419.0).

Each test reproduces a finding against the code as it was and pins the fix:

H1 — a savepoint rollback left the dedup history cache pointing at a
     discarded row: a later element for the same host lost its scan
     membership.
H2 — script rows moved their (cascading) scan_id to the newest scan, so
     deleting a failed import's scan deleted an earlier scan's evidence.
H3 — one web record's database error failed the whole httpx import.
H4 — undecodable JSON lines were dropped before any parser could count them.
H6 — a job cancelled after its parser wrote a scan left that scan unlinked.
H7 — an equally confident rescan of the same service could not refresh its
     version.
H8 — a new closed port was "active" until its first repeat observation.
H9 — IPv6 in a field holding only an address was "no address".
"""
import json
from unittest.mock import MagicMock

from app.db import models
from app.parsers.httpx_parser import HttpxParser
from app.parsers.nmap_parser import NmapXMLParser
from app.parsers.parser_utils import extract_first_ip, parse_host_port_token
from app.parsers.streaming_json import begin_rejection_tally, end_rejection_tally, iter_json_records
from app.services.host_deduplication_service import (
    HostDeduplicationService, port_state_is_active, should_refresh_service_details,
)
from app.services.ingestion_service import ingestion_service


def _nmap_xml(hosts: str) -> str:
    return (
        '<?xml version="1.0"?><nmaprun scanner="nmap" args="nmap -sV" start="1758800000" version="7.94">'
        f'{hosts}<runstats><finished time="1758800100"/></runstats></nmaprun>'
    )


def _host(ip: str, ports: str, hostscript: str = "") -> str:
    return (
        f'<host><status state="up"/><address addr="{ip}" addrtype="ipv4"/>'
        f'<ports>{ports}</ports>{hostscript}</host>'
    )


def _port(portid: str, name: str = "http", product: str = "nginx", version: str = "1.18.0",
          script: str = "") -> str:
    return (
        f'<port protocol="tcp" portid="{portid}"><state state="open" reason="syn-ack"/>'
        f'<service name="{name}" product="{product}" version="{version}" method="probed" conf="10"/>'
        f'{script}</port>'
    )


def _import_nmap(db, project, tmp_path, xml: str, name: str):
    path = tmp_path / name
    path.write_text(xml)
    return NmapXMLParser(db).parse_file(str(path), name, project_id=project.id)


# --------------------------------------------------------------------- H2

def test_deleting_a_failed_rescan_keeps_the_earlier_scans_scripts(db_session, test_project, tmp_path):
    script = '<script id="http-title" output="{out}"/>'
    hostscript = '<hostscript><script id="smb-os-discovery" output="{out}"/></hostscript>'
    pid = test_project.id
    s1 = _import_nmap(db_session, test_project, tmp_path, _nmap_xml(_host(
        "10.61.0.5", _port("80", script=script.format(out="first")), hostscript.format(out="first"))), "s1.xml")
    s1_id = s1.id
    s2 = _import_nmap(db_session, test_project, tmp_path, _nmap_xml(_host(
        "10.61.0.5", _port("80", script=script.format(out="second")), hostscript.format(out="second"))), "s2.xml")
    # What the dispatcher's cleanup does to a failed attempt's scan.
    db_session.query(models.Scan).filter(models.Scan.id == s2.id).delete(synchronize_session=False)
    db_session.commit()

    host = db_session.query(models.Host).filter(
        models.Host.project_id == pid, models.Host.ip_address == "10.61.0.5").one()
    port_script = db_session.query(models.Script).join(models.Port).filter(models.Port.host_id == host.id).one()
    host_script = db_session.query(models.HostScript).filter(models.HostScript.host_id == host.id).one()
    # The rows survive, still credited to the scan that first recorded them.
    assert port_script.scan_id == s1_id and host_script.scan_id == s1_id


# --------------------------------------------------------------------- H1

def test_a_rolled_back_element_does_not_cost_the_host_its_scan_membership(db_session, test_project, tmp_path):
    host = models.Host(project_id=test_project.id, ip_address="10.61.0.9", state="up")
    db_session.add(host)
    db_session.commit()
    host_id = host.id
    # The same host twice (merged output): the first element has a port the
    # parser cannot read and is rolled back; the second is valid.
    xml = _nmap_xml(_host("10.61.0.9", _port("bad")) + _host("10.61.0.9", _port("443", name="https")))
    scan = _import_nmap(db_session, test_project, tmp_path, xml, "merged.xml")
    history = db_session.query(models.HostScanHistory).filter(
        models.HostScanHistory.host_id == host_id, models.HostScanHistory.scan_id == scan.id).all()
    assert len(history) == 1
    assert db_session.query(models.Port).filter(
        models.Port.host_id == host_id, models.Port.port_number == 443).count() == 1


# --------------------------------------------------------------------- H7 / H8

def test_an_equally_confident_rescan_refreshes_the_version(db_session, test_project):
    host = models.Host(project_id=test_project.id, ip_address="10.61.0.12", state="up")
    scan = models.Scan(filename="x", tool_name="nmap", project_id=test_project.id)
    db_session.add_all([host, scan])
    db_session.flush()
    dedup = HostDeduplicationService(db_session)
    base = {"port_number": 80, "protocol": "tcp", "state": "open", "service_name": "http",
            "service_product": "nginx", "service_conf": 10, "service_method": "probed"}
    dedup.find_or_create_port(host.id, scan.id, {**base, "service_version": "1.18.0"})
    port = dedup.find_or_create_port(host.id, scan.id, {**base, "service_version": "1.26.0"})
    assert port.service_version == "1.26.0"
    # A name-only observation (no confidence) refreshes nothing and erases nothing.
    port = dedup.find_or_create_port(host.id, scan.id, {"port_number": 80, "protocol": "tcp",
                                                        "state": "open", "service_name": "http"})
    assert (port.service_product, port.service_version) == ("nginx", "1.26.0")
    # A same-service observation that omits the version does not blank it.
    port = dedup.find_or_create_port(host.id, scan.id, {**base, "service_version": None})
    assert port.service_version == "1.26.0"


def test_service_refresh_rule():
    assert should_refresh_service_details("http", 10, "HTTP", 10)
    assert not should_refresh_service_details("http", 10, "http", 5)
    assert not should_refresh_service_details("http", 10, "http", None)
    assert not should_refresh_service_details("http", 10, "https", 10)


def test_port_activity_is_one_rule_for_create_and_update(db_session, test_project):
    for i, state in enumerate(("open", "closed", "filtered", "open|filtered", "closed|filtered", "unfiltered", None)):
        host = models.Host(project_id=test_project.id, ip_address=f"10.61.1.{i + 1}", state="up")
        scan = models.Scan(filename="x", tool_name="nmap", project_id=test_project.id)
        db_session.add_all([host, scan])
        db_session.flush()
        dedup = HostDeduplicationService(db_session)
        data = {"port_number": 161, "protocol": "udp", "state": state}
        created = dedup.find_or_create_port(host.id, scan.id, data).is_active
        repeated = dedup.find_or_create_port(host.id, scan.id, data).is_active
        assert created == repeated == port_state_is_active(state), state
    assert port_state_is_active("closed") is False and port_state_is_active("open|filtered") is True


# --------------------------------------------------------------------- H3

def test_one_bad_httpx_record_does_not_fail_the_import(db_session, test_project, tmp_path):
    rec = lambda ip, length: json.dumps({  # noqa: E731
        "url": f"https://{ip}/", "host": ip, "port": "443", "scheme": "https",
        "status_code": 200, "webserver": "nginx", "tech": ["Nginx"], "content_length": length,
    })
    p = tmp_path / "httpx.jsonl"
    # Past BIGINT: a database error on the middle record's flush.
    p.write_text("\n".join([rec("10.61.2.1", 10), rec("10.61.2.2", 10 ** 20), rec("10.61.2.3", 3_000_000_000)]))
    parser = HttpxParser(db_session)
    scan = parser.parse_file(str(p), p.name, project_id=test_project.id)
    rows = {w.ip_address: w for w in db_session.query(models.WebInterface).filter(models.WebInterface.scan_id == scan.id)}
    assert set(rows) == {"10.61.2.1", "10.61.2.3"}
    # A body past 2 GiB is stored, not rejected (content_length is BIGINT).
    assert rows["10.61.2.3"].content_length == 3_000_000_000
    assert parser.last_parse_stats["skipped"] == 1
    # The scan's host list holds only hosts that exist.
    hist = {h.host_id for h in db_session.query(models.HostScanHistory).filter(models.HostScanHistory.scan_id == scan.id)}
    assert hist == {rows["10.61.2.1"].host_id, rows["10.61.2.3"].host_id}


# --------------------------------------------------------------------- H4

def test_undecodable_json_lines_are_counted(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n{"b": 2, "trunc\n{"c": 3}\n')
    token = begin_rejection_tally()
    records = list(iter_json_records(str(p)))
    assert records == [{"a": 1}, {"c": 3}]
    assert end_rejection_tally(token) == 1


def test_an_import_with_undecodable_lines_is_partial(db_session, test_project, tmp_path):
    good = lambda ip: json.dumps({"url": f"https://{ip}/", "host": ip, "port": "443", "scheme": "https",  # noqa: E731
                                  "status_code": 200, "webserver": "nginx", "tech": ["Nginx"]})
    p = tmp_path / "httpx.jsonl"
    p.write_text(good("10.61.3.1") + '\n{"url": "https://10.61.3.2/", "tech": ["Ng\n' + good("10.61.3.3") + "\n")
    job = MagicMock()
    job.id, job.options, job.storage_path = 9101, {}, str(p)
    job.original_filename, job.project_id, job.source_tool = "httpx.jsonl", test_project.id, None
    result = ingestion_service._execute_parser(db=db_session, job=job, parser_class=HttpxParser, description="httpx")
    assert result["partial"] is True and result["skipped_count"] == 1
    assert "not valid JSON" in result["parser_warnings"]


# --------------------------------------------------------------------- H9

def test_ipv6_in_a_field_that_holds_only_an_address():
    assert extract_first_ip("2001:db8::1") == "2001:db8::1"
    assert extract_first_ip(" 10.0.0.5 ") == "10.0.0.5"
    assert extract_first_ip("host 10.0.0.6 (name)") == "10.0.0.6"
    assert parse_host_port_token("[2001:db8::1]:443") == ("2001:db8::1", 443, None)
    assert parse_host_port_token("10.0.0.7:22") == ("10.0.0.7", 22, None)


def test_amass_keeps_ipv6_addresses(db_session, test_project, tmp_path):
    from app.parsers.amass_parser import AmassParser
    p = tmp_path / "amass.json"
    p.write_text(json.dumps({"name": "v6.example.com", "domain": "example.com",
                             "addresses": [{"ip": "2001:db8::20", "cidr": "2001:db8::/32", "asn": 64500}]}) + "\n")
    AmassParser(db_session).parse_file(str(p), p.name, project_id=test_project.id)
    values = {r.value for r in db_session.query(models.DNSRecord).filter(
        models.DNSRecord.project_id == test_project.id, models.DNSRecord.record_type == "AAAA")}
    assert "2001:db8::20" in values


def test_naabu_imports_ipv6_results(db_session, test_project, tmp_path):
    from app.parsers.naabu_parser import NaabuParser
    p = tmp_path / "naabu.json"
    p.write_text(json.dumps({"ip": "2001:db8::10", "port": 443}) + "\n" + json.dumps({"ip": "10.61.4.1", "port": 22}) + "\n")
    NaabuParser(db_session).parse_file(str(p), p.name, project_id=test_project.id)
    ips = {h.ip_address for h in db_session.query(models.Host).filter(models.Host.project_id == test_project.id)}
    assert {"2001:db8::10", "10.61.4.1"} <= ips
