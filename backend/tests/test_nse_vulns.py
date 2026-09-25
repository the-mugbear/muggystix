"""NSE vulnerability results and TLS on any service (v2.413.0).

The XML below follows nmap's own output shapes (master, 2026-09-25):
``nselib/vulns.lua`` stores each vulnerability under its CVE id with
``title`` / ``state`` / ``ids`` / ``scores`` / ``description`` / ``refs`` and
leaves NOT VULNERABLE out unless asked; ``scripts/vulners.nse`` writes one
table per CPE of ``{id, cvss, type, is_exploit}``.
"""
from __future__ import annotations

from lxml import etree

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySource
from app.parsers import nse_vulns
from app.parsers.nmap_parser import NmapXMLParser

MS17 = """<script id="smb-vuln-ms17-010" output="&#xa;  VULNERABLE:&#xa;  Remote Code Execution vulnerability in Microsoft SMBv1 servers (ms17-010)&#xa;    State: VULNERABLE&#xa;    IDs:  CVE:CVE-2017-0143&#xa;    Risk factor: HIGH&#xa;">
<table key="CVE-2017-0143">
<elem key="title">Remote Code Execution vulnerability in Microsoft SMBv1 servers (ms17-010)</elem>
<elem key="state">VULNERABLE</elem>
<table key="ids"><elem>CVE:CVE-2017-0143</elem></table>
<table key="description"><elem>A critical remote code execution vulnerability exists in Microsoft SMBv1.</elem></table>
<table key="dates"><table key="disclosure"><elem key="year">2017</elem><elem key="month">03</elem><elem key="day">14</elem></table></table>
<elem key="disclosure">2017-03-14</elem>
<table key="refs"><elem>https://technet.microsoft.com/en-us/library/security/ms17-010.aspx</elem></table>
</table>
</script>"""

NOT_VULN = """<script id="ssl-heartbleed" output="">
<table key="CVE-2014-0160">
<elem key="title">The Heartbleed Bug</elem>
<elem key="state">NOT VULNERABLE</elem>
<table key="ids"><elem>CVE:CVE-2014-0160</elem></table>
</table>
</script>"""

VULNERS = """<script id="vulners" output="cpe:/a:openbsd:openssh:7.4: ...">
<table key="cpe:/a:openbsd:openssh:7.4">
<table><elem key="id">CVE-2023-38408</elem><elem key="cvss">9.8</elem><elem key="type">cve</elem><elem key="is_exploit">false</elem></table>
<table><elem key="id">EDB-ID:12345</elem><elem key="cvss">7.5</elem><elem key="type">exploitdb</elem><elem key="is_exploit">true</elem></table>
</table>
</script>"""

RDP_TLS = """<script id="ssl-cert" output="Subject: commonName=WS01">
<table key="subject"><elem key="commonName">WS01</elem></table>
<table key="issuer"><elem key="commonName">WS01</elem></table>
<table key="validity"><elem key="notBefore">2020-01-01T00:00:00</elem><elem key="notAfter">2021-01-01T00:00:00</elem></table>
</script>
<script id="ssl-enum-ciphers" output="TLSv1.0 ...">
<table key="TLSv1.0"><table key="ciphers"></table></table>
<table key="TLSv1.2"><table key="ciphers"></table></table>
</script>"""


def _parse(db, project, tmp_path, *, ssh="", smb_host="", rdp=""):
    xml = f"""<?xml version="1.0"?>
<nmaprun scanner="nmap" args="nmap -sV --script vuln" start="1758800000" version="7.94">
<host><status state="up" reason="syn-ack"/><address addr="10.8.3.5" addrtype="ipv4"/>
<ports>
<port protocol="tcp" portid="22"><state state="open" reason="syn-ack"/><service name="ssh" product="OpenSSH" version="7.4"/>{ssh}</port>
<port protocol="tcp" portid="445"><state state="open" reason="syn-ack"/><service name="microsoft-ds"/></port>
<port protocol="tcp" portid="3389"><state state="open" reason="syn-ack"/><service name="ms-wbt-server"/>{rdp}</port>
</ports>
<hostscript>{smb_host}</hostscript>
</host>
<runstats><finished time="1758800100"/></runstats>
</nmaprun>"""
    path = tmp_path / "vuln.xml"
    path.write_text(xml)
    scan = NmapXMLParser(db).parse_file(str(path), "vuln.xml", project_id=project.id)
    return {v.title: v for v in db.query(Vulnerability).filter(Vulnerability.scan_id == scan.id)}


def test_vulns_lib_shape():
    (result,) = nse_vulns.vulns_lib_results(etree.fromstring(MS17))
    assert result["state"] == "VULNERABLE"
    assert result["cves"] == ["CVE-2017-0143"]
    assert result["risk"] == "high"
    assert result["disclosure"] == "2017-03-14"
    assert nse_vulns.vulns_lib_results(etree.fromstring(NOT_VULN)) == []


def test_vulners_keeps_cve_entries_only():
    results = nse_vulns.vulners_results(etree.fromstring(VULNERS))
    assert [(r["id"], r["cvss"]) for r in results] == [("CVE-2023-38408", 9.8)]
    assert nse_vulns.cpe_product(results[0]["cpe"]) == "openssh 7.4"


def test_vuln_script_becomes_an_observation_on_the_smb_port(db_session, test_project, tmp_path):
    vulns = _parse(db_session, test_project, tmp_path, smb_host=MS17 + NOT_VULN)
    ms17 = vulns["Remote Code Execution vulnerability in Microsoft SMBv1 servers (ms17-010)"]
    assert ms17.source == VulnerabilitySource.NMAP
    assert ms17.severity.value == "high"
    assert ms17.cve_id == "CVE-2017-0143"
    assert ms17.port.port_number == 445
    assert "State: VULNERABLE" in ms17.plugin_output
    assert "The Heartbleed Bug" not in vulns


def test_vulners_cve_is_a_version_match_observation(db_session, test_project, tmp_path):
    vulns = _parse(db_session, test_project, tmp_path, ssh=VULNERS)
    row = vulns["CVE-2023-38408 in openssh 7.4"]
    assert row.severity.value == "critical"
    assert row.cve_id == "CVE-2023-38408"
    assert row.port.port_number == 22
    assert "not a test" in row.description


def test_tls_weaknesses_on_rdp_without_a_web_interface(db_session, test_project, tmp_path):
    """TLS 1.0 and an expired certificate on RDP are observations; the
    certificate being self-signed is not, and no web interface is made
    (review 2026-09-23 C6a)."""
    vulns = _parse(db_session, test_project, tmp_path, rdp=RDP_TLS)
    assert {"Deprecated TLS/SSL protocol offered", "TLS certificate expired"} <= set(vulns)
    assert vulns["Deprecated TLS/SSL protocol offered"].port.port_number == 3389
    assert "TLSv1.0" in vulns["Deprecated TLS/SSL protocol offered"].plugin_output
    assert db_session.query(models.WebInterface).filter(models.WebInterface.port == 3389).count() == 0


def test_certificate_valid_at_scan_time_is_not_expired(db_session, test_project, tmp_path):
    valid = RDP_TLS.replace("2021-01-01T00:00:00", "2030-01-01T00:00:00")
    vulns = _parse(db_session, test_project, tmp_path, rdp=valid)
    assert "TLS certificate expired" not in vulns
