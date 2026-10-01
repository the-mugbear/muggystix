"""masscan's port reason, RustScan's IPv6 lines and nmap's scanned-port list
(review 2026-10-01 B12)."""
from __future__ import annotations

from pathlib import Path

from app.db import models
from app.parsers.content_detection import looks_like_rustscan
from app.parsers.masscan_parser import MasscanParser
from app.parsers.nmap_parser import NmapXMLParser
from app.parsers.rustscan_parser import RustScanParser

NATIVE = Path(__file__).parent / "fixtures" / "native"


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return path


def _ports(db, project_id):
    rows = (
        db.query(models.Host.ip_address, models.Port.port_number, models.Port.reason, models.Port.state)
        .join(models.Port, models.Port.host_id == models.Host.id)
        .filter(models.Host.project_id == project_id)
        .all()
    )
    return {(ip, port): (reason, state) for ip, port, reason, state in rows}


# ---------------------------------------------------------------------------
# masscan — Port.reason
# ---------------------------------------------------------------------------

MASSCAN_XML = """<?xml version="1.0"?>
<nmaprun scanner="masscan" start="1790124757" version="1.0-BETA"  xmloutputversion="1.03">
<scaninfo type="syn" protocol="tcp" />
<host endtime="1790124714"><address addr="10.90.0.1" addrtype="ipv4"/><ports><port protocol="tcp" portid="443"><state state="open" reason="syn-ack" reason_ttl="63"/></port></ports></host>
<host endtime="1790124714"><address addr="10.90.0.1" addrtype="ipv4"/><ports><port protocol="tcp" portid="22"><state state="open" reason="syn-ack" reason_ttl="64"/></port></ports></host>
</nmaprun>
"""
MASSCAN_JSON = """[
{   "ip": "10.90.1.1",   "timestamp": "1790124714", "ports": [ {"port": 443, "proto": "tcp", "status": "open", "reason": "syn-ack", "ttl": 63} ] }
,
{   "ip": "10.90.1.1",   "timestamp": "1790124714", "ports": [ {"port": 80, "proto": "tcp", "status": "open", "reason": "syn-ack", "ttl": 63} ] }
]
"""


def test_masscan_xml_and_json_record_the_port_reason(db_session, test_project, tmp_path):
    pid = test_project.id
    MasscanParser(db_session).parse_file(str(_write(tmp_path, "m.xml", MASSCAN_XML)), "m.xml", project_id=pid)
    MasscanParser(db_session).parse_file(str(_write(tmp_path, "m.json", MASSCAN_JSON)), "m.json", project_id=pid)
    assert _ports(db_session, pid) == {
        ("10.90.0.1", 443): ("syn-ack", "open"),
        ("10.90.0.1", 22): ("syn-ack", "open"),
        ("10.90.1.1", 443): ("syn-ack", "open"),
        ("10.90.1.1", 80): ("syn-ack", "open"),
    }


def test_a_banner_records_response_is_not_the_ports_reason(db_session, test_project, tmp_path):
    """A real `--banners` capture: the port appears once with `syn-ack` and
    once per banner with reason `response`.  Whichever comes first, the
    port's reason is the SYN-ACK."""
    pid = test_project.id
    for name in ("masscan-banners.xml", "masscan-banners.json"):
        MasscanParser(db_session).parse_file(str(NATIVE / name), name, project_id=pid)
        assert _ports(db_session, pid) == {("172.30.77.20", 8080): ("syn-ack", "open")}

    reordered = """<?xml version="1.0"?>
<nmaprun scanner="masscan" start="1790141251" version="1.0-BETA">
<host endtime="1"><address addr="10.90.2.1" addrtype="ipv4"/><ports><port protocol="tcp" portid="8080"><state state="open" reason="response" reason_ttl="64" /><service name="title" banner="x"></service></port></ports></host>
<host endtime="1"><address addr="10.90.2.1" addrtype="ipv4"/><ports><port protocol="tcp" portid="8080"><state state="open" reason="syn-ack" reason_ttl="64"/></port></ports></host>
</nmaprun>
"""
    path = _write(tmp_path, "reordered.xml", reordered)
    MasscanParser(db_session).parse_file(str(path), "reordered.xml", project_id=pid)
    assert _ports(db_session, pid)[("10.90.2.1", 8080)] == ("syn-ack", "open")


def test_a_list_import_keeps_a_stored_reason(db_session, test_project, tmp_path):
    """-oL carries no reason: re-observing an open port keeps the one on file."""
    pid = test_project.id
    MasscanParser(db_session).parse_file(str(_write(tmp_path, "m.xml", MASSCAN_XML)), "m.xml", project_id=pid)
    listing = "#masscan\nopen tcp 443 10.90.0.1 1790124714\n# end\n"
    MasscanParser(db_session).parse_file(str(_write(tmp_path, "m.txt", listing)), "m.txt", project_id=pid)
    assert _ports(db_session, pid)[("10.90.0.1", 443)] == ("syn-ack", "open")


def test_a_reason_for_another_state_is_dropped(db_session, test_project, tmp_path):
    """The reason belongs to the observation that set the state (the dedup
    service's rule, mirrored in the bulk SQL)."""
    pid = test_project.id
    host = models.Host(project_id=pid, ip_address="10.90.3.1", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(models.Port(host_id=host.id, port_number=80, protocol="tcp", state="closed", reason="reset"))
    db_session.flush()
    listing = "#masscan\nopen tcp 80 10.90.3.1 1790124714\n# end\n"
    MasscanParser(db_session).parse_file(str(_write(tmp_path, "m.txt", listing)), "m.txt", project_id=pid)
    db_session.expire_all()
    assert _ports(db_session, pid)[("10.90.3.1", 80)] == (None, "open")


# ---------------------------------------------------------------------------
# RustScan — IPv6
# ---------------------------------------------------------------------------

RUSTSCAN_V6 = (
    "Open [2001:db8::10]:22\n"
    "Open [2001:db8::10]:443\n"
    "Open 10.91.0.1:80\n"
    "2001:db8::10 -> [22,443]\n"
    "2001:db8::20 -> [8443]\n"
    "10.91.0.1 -> [80]\n"
)


def test_rustscan_ipv6_results_are_imported(db_session, test_project, tmp_path):
    pid = test_project.id
    path = _write(tmp_path, "scan.txt", RUSTSCAN_V6)
    assert looks_like_rustscan(b"Open [2001:db8::10]:22\n", "scan.txt")
    assert looks_like_rustscan(b"2001:db8::20 -> [8443]\n", "scan.txt")
    parser = RustScanParser(db_session)
    parser.parse_file(str(path), "scan.txt", project_id=pid)
    assert sorted(_ports(db_session, pid)) == [
        ("10.91.0.1", 80), ("2001:db8::10", 22), ("2001:db8::10", 443), ("2001:db8::20", 8443),
    ]
    assert parser.last_parse_stats["summary"].endswith("on 3 hosts")


def test_rustscan_does_not_read_an_address_out_of_other_text(db_session, test_project, tmp_path):
    """A clock, a MAC address or `::` in prose is not an IPv6 result."""
    pid = test_project.id
    text = (
        "Open 10.91.1.1:22\n"
        "Started at 10:30:15 -> [not ports]\n"
        "MAC 00:11:22:33:44:55 -> [1]\n"
        "std::io::Error -> [2]\n"
    )
    RustScanParser(db_session).parse_file(str(_write(tmp_path, "r.txt", text)), "r.txt", project_id=pid)
    assert sorted(_ports(db_session, pid)) == [("10.91.1.1", 22)]


# ---------------------------------------------------------------------------
# nmap — what was scanned, one row per <scaninfo>
# ---------------------------------------------------------------------------

NMAP_TCP_UDP = """<?xml version="1.0"?>
<nmaprun scanner="nmap" args="nmap -sS -sU -p T:22,80,U:53,161 10.92.0.1" start="1790124757" version="7.94" xmloutputversion="1.05">
<scaninfo type="syn" protocol="tcp" numservices="2" services="22,80"/>
<scaninfo type="udp" protocol="udp" numservices="2" services="53,161"/>
<host><status state="up" reason="echo-reply"/><address addr="10.92.0.1" addrtype="ipv4"/>
<ports><port protocol="tcp" portid="22"><state state="open" reason="syn-ack"/></port></ports></host>
<runstats><finished time="1790124800"/></runstats>
</nmaprun>
"""


def test_nmap_keeps_one_scan_info_row_per_protocol(db_session, test_project, tmp_path):
    """TCP + UDP in one run: two <scaninfo> elements, two rows — the port
    list is what separates "no open ports" from "not scanned"."""
    path = _write(tmp_path, "both.xml", NMAP_TCP_UDP)
    scan = NmapXMLParser(db_session).parse_file(str(path), "both.xml", project_id=test_project.id)
    db_session.flush()
    rows = db_session.query(models.ScanInfo).filter_by(scan_id=scan.id).order_by(models.ScanInfo.id).all()
    assert [(r.type, r.protocol, r.numservices, r.services) for r in rows] == [
        ("syn", "tcp", 2, "22,80"),
        ("udp", "udp", 2, "53,161"),
    ]
