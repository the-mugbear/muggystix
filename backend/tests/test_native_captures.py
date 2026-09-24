"""Parsers against REAL tool output (v2.387.0).

The fixtures under ``fixtures/native/`` are unmodified captures from the
Parser Lab (a Kali container run against a home LAN and a Samba/web lab
network, 2026-09-22/23): the addresses are private test addresses.  The older
``artifacts/manual`` samples were written to fit the parsers, so each defect
below passed its tests and failed on the real file.  Every test here was run
against the pre-fix parser and failed there.
"""
from pathlib import Path

import pytest

from app.db import models
from app.db.models_vulnerability import Vulnerability

NATIVE = Path(__file__).parent / "fixtures" / "native"


def _vulns(db, scan):
    return (
        db.query(Vulnerability)
        .filter(Vulnerability.scan_id == scan.id)
        .order_by(Vulnerability.id)
        .all()
    )


# --- Nikto -------------------------------------------------------------------

class TestNikto:
    def test_native_json_records_each_finding_under_its_host(self, db_session, test_project):
        """Nikto 2.6 ``-Format json``: [{host, ip, port, vulnerabilities: [...]}].
        The host object was stored as ONE empty "Nikto finding"."""
        from app.parsers.nikto_parser import NiktoParser

        scan = NiktoParser(db_session).parse_file(
            str(NATIVE / "nikto-all.json"), "nikto-all.json", project_id=test_project.id)
        vulns = _vulns(db_session, scan)
        assert len(vulns) == 11
        assert "Nikto finding" not in {v.title for v in vulns}
        robots = next(v for v in vulns if v.plugin_id == "999997")
        assert robots.title.startswith("/robots.txt: Entry '/public/'")
        assert "portswigger.net" in robots.description
        host = db_session.get(models.Host, robots.host_id)
        assert host.ip_address == "172.30.77.20"
        assert db_session.get(models.Port, robots.port_id).port_number == 8080
        # A message that only makes sense with its path carries the path, as
        # the text output prints it; "/" adds nothing (browser walkthrough
        # 2026-09-24: "This might be interesting." named no URL).
        titles = {v.plugin_id: v.title for v in vulns}
        assert titles["999996"] == "/robots.txt: contains 1 entry which should be manually viewed."
        assert titles["001811"] == "/public/: This might be interesting."
        assert titles["600720"].startswith("SimpleHTTP/0.6 appears")

    def test_native_text_ids_long_messages_and_metadata(self, db_session, test_project):
        """A message over 200 characters raised StringDataRightTruncation and
        failed the file; "Server:", "Platform:" and the request tally were
        stored as findings; the five headers sharing id 013587 collapsed."""
        from app.parsers.nikto_parser import NiktoParser

        scan = NiktoParser(db_session).parse_file(
            str(NATIVE / "nikto-all.txt"), "nikto-all.txt", project_id=test_project.id)
        vulns = _vulns(db_session, scan)
        titles = [v.title for v in vulns]
        assert len(vulns) == 11
        assert not any(t.startswith(("Server:", "Platform:", "No CGI")) or "requests:" in t for t in titles)
        headers = [v for v in vulns if v.plugin_id == "013587"]
        assert len(headers) == 5
        assert {t.rsplit(": ", 1)[1].rstrip(".") for t in (v.title for v in headers)} == {
            "strict-transport-security", "referrer-policy", "permissions-policy",
            "content-security-policy", "x-content-type-options",
        }
        long_one = next(v for v in vulns if v.plugin_id == "007352")
        assert len(long_one.source_plugin_name) <= 200
        assert "See: https://www.netsparker.com" in long_one.description
        assert "See:" not in long_one.title

    def test_native_csv_has_no_header_row(self, db_session, test_project):
        """Nikto's ``-Format csv``: a banner, no header, positional columns."""
        from app.parsers.nikto_parser import NiktoParser

        scan = NiktoParser(db_session).parse_file(
            str(NATIVE / "nikto.csv"), "nikto.csv", project_id=test_project.id)
        vulns = _vulns(db_session, scan)
        assert len(vulns) == 5  # the empty target row is not a finding
        assert all(v.title.startswith("/: Suggested security header missing:") for v in vulns)
        assert db_session.get(models.Host, vulns[0].host_id).ip_address == "192.168.7.222"


# --- detection ---------------------------------------------------------------

def _detected(name: str) -> list:
    from types import SimpleNamespace
    from app.services.ingestion_service import IngestionService

    sample = (NATIVE / name).read_bytes()[:65536]
    job = SimpleNamespace(original_filename=name, options={}, format_override=None)
    return [ft for ft, _cls, _desc in IngestionService()._build_parsing_attempts(job, sample)]


@pytest.mark.parametrize("renamed", [False, True])
def test_masscan_list_is_recognised_by_its_rows(renamed, tmp_path):
    """``-oL`` starts ``#masscan`` (no space) — it was "not recognised"."""
    from types import SimpleNamespace
    from app.services.ingestion_service import IngestionService

    sample = (NATIVE / "masscan-list.txt").read_bytes()
    name = "ports.txt" if renamed else "masscan-list.txt"
    job = SimpleNamespace(original_filename=name, options={}, format_override=None)
    types = [ft for ft, _c, _d in IngestionService()._build_parsing_attempts(job, sample)]
    assert types[:1] == ["masscan_list"]


def test_testssl_json_is_not_also_naabu():
    """testssl's flat records carry ip + port; the review asked for a choice."""
    assert "naabu_json" not in _detected("testssl-full.json")
    assert _detected("testssl-full.json")[:1] == ["testssl_json"]


def _detected_bytes(sample: bytes, name: str) -> list:
    from types import SimpleNamespace
    from app.services.ingestion_service import IngestionService

    job = SimpleNamespace(original_filename=name, options={}, format_override=None)
    return [ft for ft, _cls, _desc in IngestionService()._build_parsing_attempts(job, sample)]


class TestNiktoJsonDetection:
    """v2.404.0 — Nikto's native JSON is recognised by its shape, not only
    by a "nikto" filename, and that shape claims no other tool's JSON."""

    def test_native_json_is_nikto_under_a_neutral_name(self):
        from app.parsers.content_detection import looks_like_nikto

        sample = (NATIVE / "nikto-all.json").read_bytes()
        assert looks_like_nikto(sample, "upload.json")
        assert _detected("nikto-all.json") == ["nikto_json"]
        assert _detected_bytes(sample, "upload.json") == ["nikto_json"]

    @pytest.mark.parametrize("sample", [
        # One host object (Nikto 2.5, single target).
        b'{"host": "10.0.0.5", "ip": "10.0.0.5", "port": "80", "banner": "",'
        b' "vulnerabilities": [{"id": "999100", "method": "GET", "url": "/", "msg": "x"}]}',
        # Older Nikto: OSVDB instead of references.
        b'[{"host": "a.example", "ip": "10.0.0.5", "port": "443", "banner": "Apache",'
        b' "vulnerabilities": [{"id": "000726", "OSVDB": "0", "method": "GET", "url": "/", "msg": "y"}]}]',
        # A wrapper carrying findings.
        b'{"ip": "10.0.0.5", "port": 80, "findings": [{"id": "1", "msg": "z"}]}',
        # Flat records (artifacts/manual/nikto_sample.json).
        b'[{"ip": "10.0.0.5", "port": 80, "id": "nikto-000726", "msg": "m", "severity": "low"}]',
        # A target Nikto reported nothing on.
        b'[{"host": "10.0.0.5", "ip": "10.0.0.5", "port": 80, "server_banner": null, "vulnerabilities": []}]',
    ])
    def test_other_nikto_json_shapes_stay_recognised(self, sample):
        from app.parsers.content_detection import looks_like_nikto

        assert looks_like_nikto(sample, "upload.json")

    def test_a_host_object_larger_than_the_sample_is_still_recognised(self):
        from app.parsers.content_detection import looks_like_nikto

        finding = b'{"id": "999100", "method": "GET", "url": "/a", "msg": "' + b"x" * 200 + b'"}'
        big = b'[{"host": "10.0.0.5", "ip": "10.0.0.5", "port": 80, "vulnerabilities": [' + \
            b",".join([finding] * 400) + b"]}]"
        assert len(big) > 65536
        assert looks_like_nikto(big[:65536], "upload.json")

    @pytest.mark.parametrize("name", [
        "masscan-banners.json", "testssl-full.json", "testssl-pretty.json",
        "netexec-spider-172.30.77.10.json", "rdap-native.json", "rdap-compact.json",
    ])
    def test_other_native_json_is_not_nikto(self, name):
        from app.parsers.content_detection import looks_like_nikto

        sample = (NATIVE / name).read_bytes()[:65536]
        assert not looks_like_nikto(sample, "upload.json")
        assert "nikto_json" not in _detected_bytes(sample, "upload.json")

    @pytest.mark.parametrize("sample", [
        # httpx
        b'{"url": "https://10.0.0.5", "host": "10.0.0.5", "port": "443", "title": "t",'
        b' "webserver": "nginx", "tech": ["Nginx"], "status_code": 200}',
        # naabu
        b'{"host": "10.0.0.5", "ip": "10.0.0.5", "port": 22, "protocol": "tcp"}',
        # smbmap
        b'[{"ip": "10.0.0.5", "port": 445, "shares": [{"name": "C$", "permissions": "NO ACCESS"}]}]',
        # EyeWitness
        b'[{"url": "http://10.0.0.5", "page_title": "t", "screenshot_path": "s.png"}]',
        # ffuf
        b'{"results": [{"url": "http://10.0.0.5/admin", "status": 200, "length": 12}]}',
        # dnsx
        b'{"host": "a.example", "a": ["10.0.0.5"], "status_code": "NOERROR", "resolver": ["1.1.1.1:53"]}',
        # BloodHound
        b'{"data": [{"Properties": {"name": "HOST.CORP.LOCAL"}}], "meta": {"type": "computers", "count": 1}}',
        # amass
        b'{"name": "a.example", "domain": "example", "addresses": [{"ip": "10.0.0.5"}]}',
        # A scanner that also says "vulnerabilities", with no Nikto finding in it.
        b'{"ip": "10.0.0.5", "port": 80, "vulnerabilities": [{"cve": "CVE-2021-1", "severity": "high"}]}',
    ])
    def test_other_tools_json_is_not_nikto(self, sample):
        from app.parsers.content_detection import looks_like_nikto

        assert not looks_like_nikto(sample, "upload.json")
        assert "nikto_json" not in _detected_bytes(sample, "upload.json")


def test_testssl_pretty_is_recognised_by_its_content():
    from app.parsers.testssl_parser import looks_like_testssl

    assert looks_like_testssl((NATIVE / "testssl-pretty.json").read_bytes()[:65536], "report.json")


# --- SMBMap / NetExec ----------------------------------------------------------

def test_smbmap_1_10_host_line(db_session, test_project):
    """"[+] IP: 172.30.77.10:445	Name: …" — every 1.10 report failed "0 hosts"."""
    from app.parsers.smbmap_parser import SMBMapParser

    scan = SMBMapParser(db_session).parse_file(
        str(NATIVE / "smbmap-samba.txt"), "smbmap-samba.txt", project_id=test_project.id)
    history = db_session.query(models.HostScanHistory).filter_by(scan_id=scan.id).all()
    hosts = [db_session.get(models.Host, h.host_id).ip_address for h in history]
    assert hosts == ["172.30.77.10"]

    # v2.390.0 — the share table and the session, which were dropped.
    from app.db.models_confidence import NetexecResult

    result = db_session.query(NetexecResult).filter_by(scan_id=scan.id).one()
    assert (result.tool, result.protocol, result.port) == ("smbmap", "smb", 445)
    assert result.shares == [
        {"name": "public", "permissions": "READ ONLY", "remark": "Parser lab read-only public share"},
        {"name": "restricted", "permissions": "NO ACCESS", "remark": "Parser lab authenticated-only share"},
        {"name": "IPC$", "permissions": "NO ACCESS", "remark": "IPC Service (Parser Lab Samba)"},
    ]
    # "Status: NULL Session" — a login with a blank identity, which weak-auth flags.
    assert (result.auth_success, result.username) == (True, "")
    from app.services.host_condition_sets import weak_auth_host_ids

    assert result.host_id in weak_auth_host_ids(db_session, test_project.id)


def test_netexec_local_admin_and_smbv1(db_session, test_project, tmp_path):
    """v2.390.0 — "(Pwn3d!)" and "(SMBv1:True)" were in the line and nowhere else."""
    from app.db.models_confidence import NetexecResult
    from app.db.models_vulnerability import VulnerabilitySource
    from app.parsers.netexec_parser import NetexecParser

    f = tmp_path / "nxc.txt"
    f.write_text(
        "SMB   10.9.8.7   445   FILE01   [*] Windows Server 2016 Build 14393 x64 (name:FILE01) "
        "(domain:corp.local) (signing:False) (SMBv1:True)\n"
        "SMB   10.9.8.7   445   FILE01   [+] corp.local\\admin:Passw0rd! (Pwn3d!)\n"
    )
    scan = NetexecParser(db_session).parse_file(str(f), "nxc.txt", project_id=test_project.id)
    rows = db_session.query(NetexecResult).filter_by(scan_id=scan.id).all()
    banner = next(r for r in rows if r.auth_success is None)
    login = next(r for r in rows if r.auth_success)
    assert (banner.smbv1, banner.local_admin) == (True, None)
    assert (login.username, login.local_admin) == ("admin", True)
    [vuln] = _vulns(db_session, scan)
    assert (vuln.title, vuln.source, vuln.severity.value) == ("SMBv1 enabled", VulnerabilitySource.NETEXEC, "medium")
    assert vuln.port_id is not None


@pytest.mark.parametrize("name", ["netexec-samba.txt", "netexec-samba-log.txt"])
def test_netexec_share_table_is_kept(name, db_session, test_project):
    """The rows under "[*] Enumerated shares" matched no pattern: shares were null."""
    from app.db.models_confidence import NetexecResult
    from app.parsers.netexec_parser import NetexecParser

    scan = NetexecParser(db_session).parse_file(str(NATIVE / name), name, project_id=test_project.id)
    results = db_session.query(NetexecResult).filter_by(scan_id=scan.id).all()
    with_shares = [r for r in results if r.shares]
    assert len(with_shares) == 1
    assert with_shares[0].shares == [
        {"name": "public", "permissions": "READ", "remark": "Parser lab read-only public share"},
        {"name": "restricted", "permissions": None, "remark": "Parser lab authenticated-only share"},
        {"name": "IPC$", "permissions": None, "remark": "IPC Service (Parser Lab Samba)"},
    ]


def test_netexec_banner_is_not_a_failed_login_and_guest_counts_as_weak_auth(db_session, test_project, tmp_path):
    """v2.388.1 — every row defaulted to auth_success=False: the SMB banner
    read "Auth failed" beside the real guest login, and the weak-auth
    condition (latest row per host/port) could pick the banner over it."""
    from app.db.models_confidence import NetexecResult
    from app.parsers.netexec_parser import NetexecParser
    from app.services.host_condition_sets import weak_auth_host_ids

    NetexecParser(db_session).parse_file(
        str(NATIVE / "netexec-samba.txt"), "netexec-samba.txt", project_id=test_project.id)
    rows = db_session.query(NetexecResult).all()
    assert sorted(((r.username, r.auth_success) for r in rows), key=repr) == [("guest", True), (None, None)]
    host = db_session.query(models.Host).filter_by(ip_address="172.30.77.10").one()
    assert weak_auth_host_ids(db_session, test_project.id) == {host.id}

    # A later "[-]" line is a failed login: the guest success no longer counts.
    failed = tmp_path / "later.txt"
    failed.write_text(
        "SMB   172.30.77.10   445   LABSMB   [-] LABSMB\\guest: STATUS_LOGON_FAILURE\n")
    NetexecParser(db_session).parse_file(str(failed), "later.txt", project_id=test_project.id)
    latest = db_session.query(NetexecResult).order_by(NetexecResult.id.desc()).first()
    assert (latest.username, latest.auth_success) == ("guest", False)


def test_netexec_spider_plus_takes_the_address_from_the_file_name(db_session, test_project):
    """spider_plus writes <ip>.json holding {share: {path: {...}}}; no IP inside."""
    from app.db.models_confidence import NetexecResult
    from app.parsers.netexec_parser import NetexecParser

    name = "netexec-spider-172.30.77.10.json"
    scan = NetexecParser(db_session).parse_file(str(NATIVE / name), name, project_id=test_project.id)
    result = db_session.query(NetexecResult).filter_by(scan_id=scan.id).one()
    assert db_session.get(models.Host, result.host_id).ip_address == "172.30.77.10"
    assert (result.protocol, result.port) == ("smb", 445)
    assert list(result.shares["public"]) == ["README.txt"]


def test_smb_signing_agrees_across_tools_and_counts_as_relayable(db_session, test_project):
    """The lab's Samba host: NetExec says (signing:False), nmap's
    smb2-security-mode says "enabled but not required".  NetExec stored
    "disabled", nmap "enabled" — the last import won, and after nmap the relay
    condition (``disabled`` only) no longer flagged the host."""
    from app.parsers.netexec_parser import NetexecParser
    from app.services.host_condition_sets import smb_unsigned_host_ids

    NetexecParser(db_session).parse_file(
        str(NATIVE / "netexec-samba.txt"), "netexec-samba.txt", project_id=test_project.id)
    host = db_session.query(models.Host).filter_by(project_id=test_project.id, ip_address="172.30.77.10").one()
    assert host.smb_signing == "not_required"
    assert smb_unsigned_host_ids(db_session, test_project.id) == {host.id}

    from lxml import etree
    from app.parsers.nmap_parser import NmapXMLParser

    hostscript = etree.fromstring(
        '<hostscript><script id="smb2-security-mode" output="&#xa;  3:1:1: &#xa;    '
        'Message signing enabled but not required"/></hostscript>'
    )
    assert NmapXMLParser._detect_smb_signing(hostscript) == host.smb_signing


# --- identity and banners ----------------------------------------------------------

@pytest.mark.parametrize("name", ["masscan-banners.json", "masscan-banners.xml"])
def test_masscan_banners_become_script_output(name, db_session, test_project):
    """v2.390.0 — `--banners` output was read past and dropped."""
    from app.parsers.masscan_parser import MasscanParser

    scan = MasscanParser(db_session).parse_file(str(NATIVE / name), name, project_id=test_project.id)
    port = (
        db_session.query(models.Port).join(models.Host)
        .filter(models.Host.ip_address == "172.30.77.20", models.Port.port_number == 8080).one()
    )
    scripts = {s.script_id: s.output for s in db_session.query(models.Script).filter_by(port_id=port.id)}
    assert scripts["masscan-http.server"] == "SimpleHTTP/0.6 Python/3.14.7"
    assert scripts["masscan-title"] == "Parser Lab HTTP Target"
    assert scripts["masscan-http"].startswith("HTTP/1.0 200 OK\nServer: SimpleHTTP/0.6")
    assert "\\x0d" not in scripts["masscan-http"] and "\r" not in scripts["masscan-http"]
    # The pseudo-service names never became the port's service name.
    assert port.service_name in (None, "")


def test_nmap_mac_address_and_nessus_netbios(db_session, test_project, tmp_path):
    """v2.390.0 — nmap's MAC + vendor and Nessus's NetBIOS name were dropped."""
    from app.parsers.nmap_parser import NmapXMLParser

    xml = tmp_path / "arp.xml"
    xml.write_text(
        '<?xml version="1.0"?><nmaprun scanner="nmap" args="nmap -sn" start="1790000000">'
        '<host><status state="up"/><address addr="10.9.9.9" addrtype="ipv4"/>'
        '<address addr="AA:BB:CC:00:11:22" addrtype="mac" vendor="Example Networks"/>'
        '<ports><port protocol="tcp" portid="22"><state state="open"/></port></ports></host>'
        '<runstats><finished time="1790000100"/></runstats></nmaprun>'
    )
    NmapXMLParser(db_session).parse_file(str(xml), "arp.xml", project_id=test_project.id)
    host = db_session.query(models.Host).filter_by(ip_address="10.9.9.9").one()
    assert (host.mac_address, host.mac_vendor) == ("AA:BB:CC:00:11:22", "Example Networks")


def test_nessus_keeps_netbios_mac_see_also_and_exploit_frameworks(db_session, test_project, tmp_path):
    """v2.390.0 — read by the parser, dropped before the database."""
    import json
    from app.services.nessus_integration_service import NessusIntegrationService

    f = tmp_path / "n.nessus"
    f.write_text("""<?xml version="1.0" ?>
<NessusClientData_v2><Report name="r" xmlns:cm="http://www.nessus.org/cm">
<ReportHost name="10.9.6.1"><HostProperties>
  <tag name="host-ip">10.9.6.1</tag><tag name="netbios-name">FILE01</tag>
  <tag name="mac-address">00:11:22:33:44:55
00:11:22:33:44:66</tag>
</HostProperties>
<ReportItem port="445" svc_name="cifs" protocol="tcp" severity="3" pluginID="97833" pluginName="MS17-010" pluginFamily="Windows">
  <risk_factor>High</risk_factor><description>d</description><solution>s</solution><synopsis>syn</synopsis>
  <see_also>https://example.test/ms17-010
https://example.test/eternalblue</see_also>
  <metasploit_name>MS17-010 EternalBlue SMB Remote Windows Kernel Pool Corruption</metasploit_name>
  <plugin_output>SMBv1 accepted; MS17-010 missing</plugin_output>
</ReportItem>
</ReportHost></Report></NessusClientData_v2>""")
    NessusIntegrationService(db_session).process_nessus_file(str(f), project_id=test_project.id)
    host = db_session.query(models.Host).filter_by(ip_address="10.9.6.1").one()
    assert (host.netbios_name, host.mac_address) == ("FILE01", "00:11:22:33:44:55")
    from app.db.models_vulnerability import Vulnerability as V

    vuln = db_session.query(V).filter_by(host_id=host.id).one()
    refs = json.loads(vuln.references)
    assert "https://example.test/eternalblue" in refs
    assert "Metasploit: MS17-010 EternalBlue SMB Remote Windows Kernel Pool Corruption" in refs
    assert vuln.plugin_output == "SMBv1 accepted; MS17-010 missing"


def test_nessus_keeps_product_and_versions(db_session, test_project, tmp_path):
    """v2.406.0 — the product (CPE) and the versions the output names, so the
    inspector can fold one outdated product's advisory plugins together."""
    from app.services.host_serialization import serialize_vulnerability
    from app.services.nessus_integration_service import NessusIntegrationService

    f = tmp_path / "t.nessus"
    f.write_text("""<?xml version="1.0" ?>
<NessusClientData_v2><Report name="r">
<ReportHost name="10.9.7.1"><HostProperties><tag name="host-ip">10.9.7.1</tag></HostProperties>
<ReportItem port="8080" svc_name="www" protocol="tcp" severity="4" pluginID="1001" pluginName="Apache Tomcat 9.0.13 &lt; 9.0.120 multiple vulnerabilities" pluginFamily="Web Servers">
  <risk_factor>Critical</risk_factor><description>d</description><solution>s</solution><synopsis>syn</synopsis>
  <cpe>cpe:/a:apache:tomcat
x-cpe:/a:apache:tomcat:9.0.13</cpe>
  <cve>CVE-2026-1</cve><cve>CVE-2026-2</cve>
  <plugin_output>
  URL               : http://10.9.7.1:8080/
  Installed version : 9.0.13
  Fixed version     : 9.0.120
</plugin_output>
</ReportItem>
<ReportItem port="22" svc_name="ssh" protocol="tcp" severity="2" pluginID="1002" pluginName="SSH weak MACs" pluginFamily="Misc.">
  <risk_factor>Medium</risk_factor><description>d</description><solution>s</solution><synopsis>syn</synopsis>
</ReportItem>
</ReportHost></Report></NessusClientData_v2>""")
    NessusIntegrationService(db_session).process_nessus_file(str(f), project_id=test_project.id)
    host = db_session.query(models.Host).filter_by(ip_address="10.9.7.1").one()
    rows = {v.plugin_id: v for v in db_session.query(Vulnerability).filter_by(host_id=host.id)}
    tomcat, ssh = rows["1001"], rows["1002"]
    assert (tomcat.cpe, tomcat.installed_version, tomcat.fixed_version) == (
        "a:apache:tomcat", "9.0.13", "9.0.120",
    )
    assert (ssh.cpe, ssh.installed_version, ssh.fixed_version) == (None, None, None)
    served = serialize_vulnerability(tomcat)
    assert (served["cpe"], served["fixed_version"]) == ("a:apache:tomcat", "9.0.120")
    # Every <cve> element, not only the first; the first stays primary.
    assert tomcat.cve_id == "CVE-2026-1"
    assert served["cve_count"] == 2


def test_normalize_cpe_forms():
    from app.parsers.nessus_parser import extract_versions, normalize_cpe

    assert normalize_cpe("cpe:/a:apache:tomcat:9.0.13") == "a:apache:tomcat"
    assert normalize_cpe("cpe:2.3:o:canonical:ubuntu_linux:18.04:*:*") == "o:canonical:ubuntu_linux"
    assert normalize_cpe("x-cpe:/a:Vendor:Product") == "a:vendor:product"
    assert normalize_cpe("not a cpe") is None
    assert normalize_cpe(None) is None
    assert extract_versions("Installed version : 1.2\nFixed version : 1.3 / 2.0") == ("1.2", "1.3 / 2.0")
    assert extract_versions("nothing") == (None, None)


def test_openvas_writeup_evidence_and_refs(db_session, test_project, tmp_path):
    """v2.390.0 — OpenVAS tags (the write-up), refs and QoD were dropped, and
    the per-host detection output stood in for the description."""
    import json
    from app.parsers.openvas_parser import OpenVASParser

    f = tmp_path / "o.xml"
    f.write_text("""<?xml version="1.0"?>
<report id="r"><report><results>
<result><host>10.9.6.2</host><port>443/tcp</port><name>Weak cipher suites</name>
  <nvt oid="1.3.6.1.4.1.25623.1.0.103440"><cvss_base>5.0</cvss_base>
    <tags>summary=Weak ciphers are offered.|insight=RC4 is broken.|impact=Traffic may be decrypted.</tags>
    <refs><ref type="cve" id="CVE-2013-2566"/><ref type="cve" id="CVE-2015-2808"/><ref type="url" id="https://example.test/rc4"/></refs>
  </nvt>
  <threat>Medium</threat><severity>5.0</severity><qod><value>98</value></qod>
  <description>Offered: TLS_RSA_WITH_RC4_128_SHA</description>
  <cve>CVE-2013-2566, CVE-2015-2808</cve>
</result>
</results></report></report>""")
    OpenVASParser(db_session).parse_file(str(f), "o.xml", project_id=test_project.id)
    from app.db.models_vulnerability import Vulnerability as V

    vuln = db_session.query(V).one()
    assert vuln.description.startswith("Summary: Weak ciphers are offered.")
    assert "Impact: Traffic may be decrypted." in vuln.description
    assert vuln.plugin_output == "Offered: TLS_RSA_WITH_RC4_128_SHA\nQuality of detection: 98%"
    refs = json.loads(vuln.references)
    assert refs[0] == "Also: CVE-2015-2808" and "https://example.test/rc4" in refs


# --- nmap TLS scripts -----------------------------------------------------------

def test_nmap_ssl_scripts_feed_the_cert_and_tls_conditions(db_session, test_project):
    """v2.390.0 — nmap's ssl-cert / ssl-enum-ciphers were stored as display
    text only; has:cert_issue / has:weak_tls read web_interfaces, which only
    httpx / testssl filled, so a self-signed certificate nmap saw was missed.
    The capture: a lab service, self-signed, valid one week, TLS 1.2 + 1.3."""
    from datetime import datetime, timezone
    from app.parsers.nmap_parser import NmapXMLParser
    from app.services.host_condition_sets import cert_issue_host_ids, weak_tls_host_ids

    scan = NmapXMLParser(db_session).parse_file(
        str(NATIVE / "nmap-tls-verbose.xml"), "nmap-tls-verbose.xml", project_id=test_project.id)
    [wi] = db_session.query(models.WebInterface).filter_by(scan_id=scan.id, source="nmap").all()
    assert (wi.url, wi.port) == ("https://172.30.80.20:8443", 8443)
    assert wi.cert_not_after == datetime(2026, 9, 30, 6, 6, 36, tzinfo=timezone.utc)
    assert wi.cert_self_signed is True
    assert (wi.cert_subject_org, wi.cert_issuer_org) == ("Disposable Parser Lab", "Disposable Parser Lab")
    assert wi.tls_weak_protocol is False
    assert wi.host_id in cert_issue_host_ids(db_session, test_project.id)
    assert wi.host_id not in weak_tls_host_ids(db_session, test_project.id)


# --- content discovery --------------------------------------------------------

def test_content_discovery_keeps_nmaps_service_and_stores_paths(db_session, test_project, tmp_path):
    """v2.390.0 — ffuf's "https" beat nmap's "http" under the longer-name rule
    and NULLed nmap's product/version; the paths lived in service_extrainfo."""
    import json
    from app.parsers.dirbuster_parser import DirBusterParser

    host = models.Host(project_id=test_project.id, ip_address="10.9.7.1", state="up")
    db_session.add(host)
    db_session.flush()
    db_session.add(models.Port(host_id=host.id, port_number=443, protocol="tcp", state="open",
                               service_name="http", service_product="nginx", service_version="1.24.0",
                               service_conf=10))
    db_session.commit()

    f = tmp_path / "ffuf.json"
    f.write_text(json.dumps({"results": [
        {"url": "https://10.9.7.1/admin", "status": 401, "length": 512},
        {"url": "https://10.9.7.1/backup.zip", "status": 200, "length": 90210},
    ]}))
    scan = DirBusterParser(db_session).parse_file(str(f), "ffuf.json", project_id=test_project.id)

    port = db_session.query(models.Port).filter_by(host_id=host.id, port_number=443).one()
    assert (port.service_name, port.service_product, port.service_version) == ("http", "nginx", "1.24.0")
    rows = db_session.query(models.WebPath).filter_by(scan_id=scan.id).order_by(models.WebPath.path).all()
    assert [(r.path, r.status_code, r.size, r.url) for r in rows] == [
        ("/admin", 401, 512, "https://10.9.7.1/admin"),
        ("/backup.zip", 200, 90210, "https://10.9.7.1/backup.zip"),
    ]
    assert all(r.port_id == port.id for r in rows)


def test_web_paths_endpoint_and_detail_count(client, db_session, test_project, tmp_path):
    import json
    from app.parsers.dirbuster_parser import DirBusterParser

    f = tmp_path / "ffuf.json"
    f.write_text(json.dumps({"results": [{"url": "http://10.9.7.2/admin", "status": 403, "length": 10}]}))
    DirBusterParser(db_session).parse_file(str(f), "ffuf.json", project_id=test_project.id)
    DirBusterParser(db_session).parse_file(str(f), "ffuf-again.json", project_id=test_project.id)
    db_session.commit()
    host = db_session.query(models.Host).filter_by(ip_address="10.9.7.2").one()

    base = f"/api/v1/projects/{test_project.id}/hosts/{host.id}"
    assert client.get(base).json()["web_path_count"] == 1
    [row] = client.get(f"{base}/web-paths").json()
    assert (row["path"], row["status_code"], row["port"], row["scans"], row["source"]) == ("/admin", 403, 80, 2, "ffuf")


# --- RDAP ----------------------------------------------------------------------

@pytest.mark.parametrize("name", ["rdap-native.json", "rdap-compact.json"])
def test_rdap_response_saved_as_a_document(name, db_session, test_project):
    """A pretty-printed response was read line by line: 189 "invalid JSON"
    lines, nothing attributed, reported as a success."""
    from app.db.models_attribution import NetworkAttribution
    from app.parsers.rdap_parser import RdapParser

    parser = RdapParser(db_session)
    parser.parse_file(str(NATIVE / name), name, project_id=test_project.id)
    cidrs = [a.cidr for a in db_session.query(NetworkAttribution).filter_by(project_id=test_project.id)]
    assert cidrs == ["174.16.0.0/12"]
    assert parser.last_parse_stats["skipped"] == 0


def test_rdap_file_with_nothing_usable_fails(db_session, test_project, tmp_path):
    from app.parsers.rdap_parser import RdapParser

    bad = tmp_path / "rdap.json"
    bad.write_text("not json\nstill not json\n")
    with pytest.raises(ValueError, match="no usable network range"):
        RdapParser(db_session).parse_file(str(bad), "rdap.json", project_id=test_project.id)


# --- EyeWitness ----------------------------------------------------------------

def _web(db, scan):
    return db.query(models.WebInterface).filter_by(scan_id=scan.id).all()


def test_eyewitness_requests_csv(db_session, test_project):
    """EyeWitness's own Requests.csv has no URL column: every row was skipped."""
    from app.parsers.eyewitness_parser import EyewitnessParser

    scan = EyewitnessParser(db_session).parse_file(
        str(NATIVE / "eyewitness.csv"), "eyewitness.csv", project_id=test_project.id)
    [wi] = _web(db_session, scan)
    assert (wi.url, wi.protocol, wi.port, wi.ip_address) == ("https://192.168.7.245", "https", 443, "192.168.7.245")


def test_eyewitness_report_directory_zipped(db_session, test_project, tmp_path, monkeypatch):
    """The zip of an EyeWitness report directory (Requests.csv, screens/,
    source/) failed: the parser wanted a JSON report EyeWitness never writes."""
    import zipfile
    from app.core.config import settings
    from app.parsers.eyewitness_parser import EyewitnessParser

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    bundle = tmp_path / "eyewitness.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.write(NATIVE / "eyewitness.csv", "Requests.csv")
        zf.writestr("screens/https.192.168.7.245.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        zf.writestr("source/https.192.168.7.245.txt", "<html></html>")
        zf.writestr("report.html", "<html></html>")
    scan = EyewitnessParser(db_session).parse_file(str(bundle), "eyewitness.zip", project_id=test_project.id)
    [wi] = _web(db_session, scan)
    assert wi.url == "https://192.168.7.245"
    assert wi.screenshot_path == f"{scan.id}/https.192.168.7.245.png"


# --- testssl ---------------------------------------------------------------------

def test_testssl_rated_checks_are_scanner_observations(db_session, test_project):
    """v2.390.0 — only three TLS facts were promoted; every rated check
    (Heartbleed, ROBOT, missing HSTS, a self-signed chain …) stayed in
    web_interfaces.raw, which nothing reads."""
    import json
    from app.db.models_vulnerability import VulnerabilitySource
    from app.parsers.testssl_parser import TestsslParser

    records = json.loads((NATIVE / "testssl-full.json").read_text())
    scan = TestsslParser(db_session).parse_file(
        str(NATIVE / "testssl-full.json"), "testssl-full.json", project_id=test_project.id)
    vulns = _vulns(db_session, scan)
    # The weaknesses; not the letter grade, and not one row per cipher suite
    # (the cipherlist_* families already say which kinds are offered).
    assert {v.plugin_id for v in vulns} == {
        "cipherlist_OBSOLETED", "cert_trust", "cert_chain_of_trust", "cert_revocation", "LUCKY13",
    }
    by_id = {v.plugin_id: v for v in vulns}
    assert by_id["cert_chain_of_trust"].severity.value == "critical"
    assert by_id["cert_chain_of_trust"].title == "Certificate chain not trusted"
    assert all(v.source == VulnerabilitySource.TESTSSL for v in vulns)
    # An OK / INFO check is a fact, not an observation.
    ok_ids = {r["id"] for r in records if str(r.get("severity", "")).upper() in {"OK", "INFO"}}
    assert not ok_ids & {v.plugin_id for v in vulns}


def test_testssl_pretty_json_records_its_target(db_session, test_project):
    """``--jsonfile-pretty`` nests findings under scanResult: "0 TLS targets"."""
    from app.parsers.testssl_parser import TestsslParser

    scan = TestsslParser(db_session).parse_file(
        str(NATIVE / "testssl-pretty.json"), "testssl-pretty.json", project_id=test_project.id)
    [wi] = _web(db_session, scan)
    assert wi.ip_address == "192.168.7.245"
    assert wi.port == 443
