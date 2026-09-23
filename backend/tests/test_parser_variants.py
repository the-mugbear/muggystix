"""Adversarial variants beside the native captures (review 2026-09-23 C6).

Each native capture in ``fixtures/native`` pins ONE ordering of a tool's
output.  These are the realistic variants that capture did not cover and that
corrupted data: a certificate on a non-web port, a failed login before a
successful one, a later module's rows after a share table, an enumeration tool
after nmap -sV, a scan by address, a modern GMP report.  Each test failed on
the code before its fix.
"""
from pathlib import Path

from app.db import models
from app.db.models_confidence import NetexecResult
from app.db.models_vulnerability import Vulnerability

NATIVE = Path(__file__).parent / "fixtures" / "native"


NMAP_TLS = """<?xml version="1.0"?>
<nmaprun scanner="nmap" args="nmap -sC -sV" start="1790143597" version="7.99" xmloutputversion="1.05">
<host><status state="up" reason="syn-ack"/><address addr="10.9.9.5" addrtype="ipv4"/>
<ports>
<port protocol="tcp" portid="3389"><state state="open" reason="syn-ack"/><service name="ms-wbt-server" product="Microsoft Terminal Services" method="probed" conf="10"/>
<script id="ssl-cert" output="Subject: commonName=WIN01"><table key="subject"><elem key="commonName">WIN01</elem></table><table key="issuer"><elem key="commonName">WIN01</elem></table><table key="validity"><elem key="notBefore">2026-01-01T00:00:00</elem><elem key="notAfter">2026-07-01T00:00:00</elem></table></script>
</port>
<port protocol="tcp" portid="5985"><state state="open" reason="syn-ack"/><service name="http" product="Microsoft HTTPAPI httpd" version="2.0" extrainfo="SSDP/UPnP" method="probed" conf="10"/></port>
<port protocol="tcp" portid="5986"><state state="open" reason="syn-ack"/><service name="http" product="Microsoft HTTPAPI httpd" version="2.0" tunnel="ssl" method="probed" conf="10"/>
<script id="ssl-cert" output="Subject: commonName=WIN01"><table key="subject"><elem key="commonName">WIN01</elem></table><table key="issuer"><elem key="commonName">LAB-CA</elem></table><table key="validity"><elem key="notBefore">2026-01-01T00:00:00</elem><elem key="notAfter">2027-07-01T00:00:00</elem></table></script>
</port>
</ports></host>
<runstats><finished time="1790143600"/></runstats>
</nmaprun>
"""


def _nmap(db_session, test_project, tmp_path):
    from app.parsers.nmap_parser import NmapXMLParser
    p = tmp_path / "tls.xml"
    p.write_text(NMAP_TLS)
    return NmapXMLParser(db_session).parse_file(str(p), "tls.xml", project_id=test_project.id)


def _nxc(db_session, test_project, tmp_path, text, name="nxc.txt"):
    from app.parsers.netexec_parser import NetexecParser
    p = tmp_path / name
    p.write_text(text)
    return NetexecParser(db_session).parse_file(str(p), name, project_id=test_project.id)


def test_nmap_ssl_cert_on_rdp_is_not_a_web_interface(db_session, test_project, tmp_path):
    scan = _nmap(db_session, test_project, tmp_path)
    urls = [r.url for r in db_session.query(models.WebInterface).filter_by(scan_id=scan.id)]
    # The HTTPS WinRM listener is a web interface; RDP's certificate is not.
    assert urls == ["https://10.9.9.5:5986"]


def test_netexec_after_nmap_keeps_the_sv_identification(db_session, test_project, tmp_path):
    _nmap(db_session, test_project, tmp_path)
    _nxc(db_session, test_project, tmp_path,
         "WINRM   10.9.9.5   5985   WIN01   [*] Windows 10 / Server 2019 Build 17763 (name:WIN01) (domain:LAB)\n"
         "WINRM   10.9.9.5   5986   WIN01   [+] LAB\\alice:Passw0rd! (Pwn3d!)\n")
    ports = {
        p.port_number: p for p in db_session.query(models.Port).join(models.Host)
        .filter(models.Host.ip_address == "10.9.9.5", models.Host.project_id == test_project.id)
    }
    for number in (5985, 5986):
        assert (ports[number].service_name, ports[number].service_product) == ("http", "Microsoft HTTPAPI httpd")
    assert ports[5986].service_tunnel == "ssl"
    # A tool that reports no reason keeps nmap's.
    assert ports[5985].reason == "syn-ack"


def test_netexec_success_after_a_failure_for_the_same_user_is_kept(db_session, test_project, tmp_path):
    scan = _nxc(db_session, test_project, tmp_path,
                "SMB   10.9.9.6   445   DC01   [*] Windows Server 2019 Build 17763 x64 (name:DC01) (domain:lab.local) (signing:True) (SMBv1:False)\n"
                "SMB   10.9.9.6   445   DC01   [-] lab.local\\alice:Winter2025 STATUS_LOGON_FAILURE\n"
                "SMB   10.9.9.6   445   DC01   [+] lab.local\\alice:Summer2026! (Pwn3d!)\n")
    alice = [r for r in db_session.query(NetexecResult).filter_by(scan_id=scan.id) if r.username]
    assert len(alice) == 1
    assert (alice[0].auth_success, alice[0].local_admin) == (True, True)
    assert "Summer2026" in alice[0].raw_output


def test_netexec_failure_after_a_success_does_not_downgrade(db_session, test_project, tmp_path):
    scan = _nxc(db_session, test_project, tmp_path,
                "SMB   10.9.9.6   445   DC01   [+] lab.local\\alice:Summer2026!\n"
                "SMB   10.9.9.6   445   DC01   [-] lab.local\\alice:Winter2025 STATUS_LOGON_FAILURE\n")
    alice = [r for r in db_session.query(NetexecResult).filter_by(scan_id=scan.id) if r.username]
    assert [r.auth_success for r in alice] == [True]


def test_netexec_share_table_ends_at_the_next_status_line(db_session, test_project, tmp_path):
    scan = _nxc(db_session, test_project, tmp_path,
                "SMB   10.9.9.7   445   DC01   [*] Windows Server 2019 Build 17763 x64 (name:DC01) (domain:lab.local) (signing:True) (SMBv1:False)\n"
                "SMB   10.9.9.7   445   DC01   [+] lab.local\\bob:pw\n"
                "SMB   10.9.9.7   445   DC01   [*] Enumerated shares\n"
                "SMB   10.9.9.7   445   DC01   Share           Permissions     Remark\n"
                "SMB   10.9.9.7   445   DC01   -----           -----------     ------\n"
                "SMB   10.9.9.7   445   DC01   ADMIN$                          Remote Admin\n"
                "SMB   10.9.9.7   445   DC01   IPC$            READ            Remote IPC\n"
                "SMB   10.9.9.7   445   DC01   [+] Brute forcing RIDs\n"
                "SMB   10.9.9.7   445   DC01   500: LAB\\Administrator (SidTypeUser)\n"
                "SMB   10.9.9.7   445   DC01   501: LAB\\Guest (SidTypeUser)\n")
    names = sorted(
        s["name"] for r in db_session.query(NetexecResult).filter_by(scan_id=scan.id)
        for s in (r.shares or []) if isinstance(s, dict)
    )
    assert names == ["ADMIN$", "IPC$"]


def test_nikto_scanned_by_address_names_no_host(db_session, test_project):
    from app.parsers.nikto_parser import NiktoParser
    for name in ("nikto-all.json", "nikto-all.txt"):
        NiktoParser(db_session).parse_file(str(NATIVE / name), name, project_id=test_project.id)
    host = db_session.query(models.Host).filter_by(
        project_id=test_project.id, ip_address="172.30.77.20").one()
    assert host.hostname is None


def test_nikto_25_single_host_json_records_its_findings(db_session, test_project, tmp_path):
    """Review 2026-09-23 R6 (R01 of 09-21): Nikto 2.5 writes ONE host object;
    the findings lost their parent's ip and the job succeeded with none."""
    import json
    from app.parsers.nikto_parser import NiktoParser
    p = tmp_path / "nikto.json"
    p.write_text(json.dumps({
        "host": "10.9.9.8", "ip": "10.9.9.8", "port": "8080", "banner": "",
        "vulnerabilities": [
            {"id": "999100", "method": "GET", "url": "/", "msg": "X-Frame-Options missing"},
            {"id": "999103", "method": "GET", "url": "/", "msg": "X-Content-Type-Options missing"},
        ],
    }))
    scan = NiktoParser(db_session).parse_file(str(p), "nikto.json", project_id=test_project.id)
    vulns = db_session.query(Vulnerability).filter_by(scan_id=scan.id).all()
    assert sorted(v.title for v in vulns) == ["X-Content-Type-Options missing", "X-Frame-Options missing"]
    port = db_session.query(models.Port).filter_by(id=vulns[0].port_id).one()
    assert port.port_number == 8080


OPENVAS_GMP = """<report id="r1"><report id="r1"><results>
<result id="a"><name>OpenSSH Multiple Vulnerabilities</name><host>10.9.9.9</host><port>22/tcp</port>
<nvt oid="1.3.6.1.4.1.25623.1.0.1"><name>x</name><tags>summary=bad|insight=worse</tags>
<refs><ref type="cve" id="CVE-2023-38408"/><ref type="cve" id="CVE-2023-48795"/><ref type="url" id="https://example.test"/></refs></nvt>
<severity>9.8</severity><description>detected</description></result>
</results></report></report>"""


def test_openvas_gmp_first_cve_ref_is_the_cve(db_session, test_project, tmp_path):
    from app.parsers.openvas_parser import OpenVASParser
    p = tmp_path / "gvm.xml"
    p.write_text(OPENVAS_GMP)
    scan = OpenVASParser(db_session).parse_file(str(p), "gvm.xml", project_id=test_project.id)
    v = db_session.query(Vulnerability).filter_by(scan_id=scan.id).one()
    assert v.cve_id == "CVE-2023-38408"
    assert "Also: CVE-2023-48795" in v.references
    assert "CVE-2023-38408" not in v.references


def test_no_parser_can_store_an_address_as_a_host_name(db_session, test_project):
    """The Nikto guard, made central: host creation and every later
    candidate refuse an IP literal; an operator may still type one."""
    from app.services.dns_name_service import apply_hostname_candidate
    from app.services.host_deduplication_service import HostDeduplicationService

    scan = models.Scan(project_id=test_project.id, filename="x.txt", scan_type="nikto", tool_name="nikto")
    db_session.add(scan)
    db_session.flush()
    host = HostDeduplicationService(db_session).find_or_create_host(
        "10.9.9.11", scan.id, {"state": "up", "hostname": "10.9.9.11"}, project_id=test_project.id)
    assert host.hostname is None
    assert apply_hostname_candidate(host, "10.9.9.11", "scanner") is False
    assert apply_hostname_candidate(host, "[2001:db8::1]", "ptr") is False
    assert host.hostname is None
    assert apply_hostname_candidate(host, "web01.lab", "scanner") is True
    assert apply_hostname_candidate(host, "10.9.9.11", "operator") is True


def test_openvas_with_nothing_recordable_fails_instead_of_succeeding_empty(db_session, test_project, tmp_path):
    """Review 2026-09-23 R6 (R13 of 09-21)."""
    import pytest
    from app.parsers.openvas_parser import OpenVASParser
    bad_hosts = tmp_path / "bad.xml"
    bad_hosts.write_text("<report><results><result><name>n</name><host>not-an-ip</host></result>"
                         "</results></report>")
    with pytest.raises(ValueError, match="None of the 1 OpenVAS result"):
        OpenVASParser(db_session).parse_file(str(bad_hosts), "bad.xml", project_id=test_project.id)
    not_openvas = tmp_path / "other.xml"
    not_openvas.write_text("<nmaprun><host/></nmaprun>")
    with pytest.raises(ValueError, match="Not an OpenVAS"):
        OpenVASParser(db_session).parse_file(str(not_openvas), "other.xml", project_id=test_project.id)


def test_openvas_clean_report_and_partial_results_are_said(db_session, test_project, tmp_path):
    from app.parsers.openvas_parser import OpenVASParser
    clean = tmp_path / "clean.xml"
    clean.write_text("<report id='r'><results></results></report>")
    OpenVASParser(db_session).parse_file(str(clean), "clean.xml", project_id=test_project.id)

    mixed = tmp_path / "mixed.xml"
    mixed.write_text(OPENVAS_GMP.replace(
        "</results>", "<result><name>n</name><host>not-an-ip</host></result></results>"))
    parser = OpenVASParser(db_session)
    parser.parse_file(str(mixed), "mixed.xml", project_id=test_project.id)
    assert parser.last_parse_stats["skipped"] == 1
    assert "1 of 2 OpenVAS result(s) not recorded" in parser.last_parse_stats["warnings"]


def test_whatweb_with_no_usable_record_leaves_no_scan(db_session, test_project, tmp_path):
    import pytest
    from app.parsers.whatweb_parser import WhatwebParser
    p = tmp_path / "ww.json"
    p.write_text('[{"target": "", "plugins": {}}]')
    before = db_session.query(models.Scan).count()
    with pytest.raises(ValueError, match="0 usable records"):
        WhatwebParser(db_session).parse_file(str(p), "ww.json", project_id=test_project.id)
    db_session.rollback()
    assert db_session.query(models.Scan).count() == before


def test_eyewitness_with_every_row_unusable_fails(db_session, test_project, tmp_path):
    import pytest
    from app.parsers.eyewitness_parser import EyewitnessParser
    p = tmp_path / "Requests.csv"
    p.write_text("Protocol,Port,Domain,Request Status,Screenshot Path, Source Path\n"
                 ",,,,,\n,,,,,\n")
    with pytest.raises(ValueError, match="all 2 row"):
        EyewitnessParser(db_session).parse_file(str(p), "Requests.csv", project_id=test_project.id)


def test_directory_buster_json_reads_each_records_own_fields(db_session, test_project, tmp_path):
    """Review 2026-09-23 R5: the column names came from the first record, so
    feroxbuster (configuration line first) and dirsearch (`contentLength`)
    lost every size."""
    import json
    from app.parsers.dirbuster_parser import DirBusterParser
    p = tmp_path / "ferox.json"
    p.write_text("\n".join(json.dumps(r) for r in (
        {"type": "configuration", "target_url": "http://10.9.9.20/", "threads": 50},
        {"type": "response", "url": "http://10.9.9.20/admin", "status": 301, "content_length": 178},
        {"type": "response", "url": "http://10.9.9.20/login", "status": 200, "content_length": 5120},
    )))
    DirBusterParser(db_session).parse_file(str(p), "ferox.json", project_id=test_project.id)
    rows = {w.path: (w.status_code, w.size) for w in db_session.query(models.WebPath)
            .join(models.Host).filter(models.Host.ip_address == "10.9.9.20")}
    assert rows == {"/admin": (301, 178), "/login": (200, 5120)}


def test_a_reobservation_without_a_score_keeps_the_stored_one(db_session, test_project):
    from datetime import datetime, timezone

    from app.db.models_vulnerability import VulnerabilitySeverity, VulnerabilitySource
    from app.parsers.parser_utils import upsert_vulnerability
    scan = models.Scan(project_id=test_project.id, filename="o.xml", scan_type="openvas", tool_name="openvas")
    host = models.Host(project_id=test_project.id, ip_address="10.9.9.10", state="up",
                       first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc))
    db_session.add_all([scan, host])
    db_session.flush()
    common = dict(db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.OPENVAS,
                  title="Weak cipher", severity=VulnerabilitySeverity.MEDIUM, plugin_id="1.2.3")
    upsert_vulnerability(**common, cvss_score=5.3)
    v = upsert_vulnerability(**common, cvss_score=None)
    assert v.cvss_score == 5.3


def test_a_revisited_new_host_updates_its_script(db_session, test_project, tmp_path):
    """Remediation review 2026-09-23 finding 2: a host created in this parse,
    left for another host and met again (merged XML: A → B → A) was assumed
    to have nothing in the DB, so its script INSERT collided on
    uq_port_script and the later element was skipped."""
    from app.parsers.nmap_parser import NmapXMLParser

    def host(ip, output):
        return (f'<host><status state="up"/><address addr="{ip}" addrtype="ipv4"/><ports>'
                f'<port protocol="tcp" portid="443"><state state="open"/><service name="https"/>'
                f'<script id="http-title" output="{output}"/></port></ports></host>')

    path = tmp_path / "merged.xml"
    path.write_text('<nmaprun scanner="nmap" start="1790143597">'
                    + host("10.91.0.1", "first") + host("10.91.0.2", "middle")
                    + host("10.91.0.1", "latest") + '</nmaprun>')
    NmapXMLParser(db_session).parse_file(str(path), path.name, project_id=test_project.id)
    script = (db_session.query(models.Script).join(models.Port).join(models.Host)
              .filter(models.Host.project_id == test_project.id,
                      models.Host.ip_address == "10.91.0.1").one())
    assert script.output == "latest"
