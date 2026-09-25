"""The misconfiguration catalog (v2.412.0): each check is one scanner
observation, named the same whichever tool saw it, so an analyst filters on
it and promotes it like any scanner row.

Input comes from the repository's captures (the Samba lab NetExec and SMBMap
output, the nxc wiki VNC samples) and a small nmap XML built here.
"""
from __future__ import annotations

import os

from app.db import models
from app.db.models_auth import User
from app.db.models_confidence import NetexecResult
from app.db.models_vulnerability import Vulnerability, VulnerabilitySource
from app.parsers.netexec_parser import NetexecParser, writable_share
from app.parsers.nmap_parser import NmapXMLParser
from app.parsers.smbmap_parser import SMBMapParser
from app.services import host_query_dsl as D
from app.services.misconfig_checks import CHECKS

NATIVE = os.path.join(os.path.dirname(__file__), "fixtures", "native")


def _observations(db, scan_id):
    return db.query(Vulnerability).filter(Vulnerability.scan_id == scan_id).all()


def _checks(db, scan_id, ip=None):
    rows = _observations(db, scan_id)
    if ip:
        rows = [v for v in rows if db.get(models.Host, v.host_id).ip_address == ip]
    return {v.plugin_id for v in rows}


def _nmap_xml(tmp_path, *, port_scripts="", host_scripts=""):
    xml = f"""<?xml version="1.0"?>
<nmaprun scanner="nmap" args="nmap -sV -sC" start="1758800000" version="7.94">
<host><status state="up" reason="syn-ack"/><address addr="10.8.1.5" addrtype="ipv4"/>
<ports>
<port protocol="tcp" portid="445"><state state="open" reason="syn-ack"/><service name="microsoft-ds"/></port>
<port protocol="tcp" portid="5900"><state state="open" reason="syn-ack"/><service name="vnc"/>{port_scripts}</port>
</ports>
<hostscript>{host_scripts}</hostscript>
</host>
<runstats><finished time="1758800100"/></runstats>
</nmaprun>"""
    path = tmp_path / "scan.xml"
    path.write_text(xml)
    return path


def test_catalog_ids_and_titles_are_unique():
    assert len({c.title for c in CHECKS.values()}) == len(CHECKS)
    # Kept from the v2.390.0 NetExec observation, so old rows stay one issue.
    assert CHECKS["smbv1_enabled"].title == "SMBv1 enabled"


def test_netexec_samba_capture(db_session, test_project):
    scan = NetexecParser(db_session).parse_file(
        os.path.join(NATIVE, "netexec-samba.txt"), "netexec-samba.txt", project_id=test_project.id)
    found = _checks(db_session, scan.id, "172.30.77.10")
    assert {"smb_signing_not_required", "smb_null_session"} <= found
    vulns = [v for v in _observations(db_session, scan.id) if v.plugin_id == "smb_null_session"]
    # The banner flag and the guest session are one observation on 445.
    assert len(vulns) == 1
    assert vulns[0].source == VulnerabilitySource.NETEXEC
    assert vulns[0].port is not None and vulns[0].port.port_number == 445


def test_netexec_vnc_no_auth_from_the_wiki(db_session, test_project):
    scan = NetexecParser(db_session).parse_file(
        os.path.join(NATIVE, "netexec-wiki-vnc-ftp-ssh.txt"), "wiki.txt", project_id=test_project.id)
    vnc = [v for v in _observations(db_session, scan.id) if v.plugin_id == "vnc_no_auth"]
    assert {(db_session.get(models.Host, v.host_id).ip_address, v.port.port_number) for v in vnc} == {
        ("192.168.56.22", 5900), ("192.168.56.22", 5901),
    }
    # A banner without the flag is not a finding.
    assert "vnc_no_auth" not in _checks(db_session, scan.id, "192.168.56.23")
    assert vnc[0].severity.value == "high"
    assert "(No Auth:True)" in vnc[0].plugin_output


def test_smbmap_null_session_and_writable_flag(db_session, test_project):
    scan = SMBMapParser(db_session).parse_file(
        os.path.join(NATIVE, "smbmap-samba.txt"), "smbmap-samba.txt", project_id=test_project.id)
    (vuln,) = [v for v in _observations(db_session, scan.id) if v.plugin_id == "smb_null_session"]
    assert vuln.source == VulnerabilitySource.SMBMAP
    row = db_session.query(NetexecResult).filter_by(scan_id=scan.id).one()
    assert row.writable_share is not None


def test_writable_share_reads_only_share_tables():
    assert writable_share([{"name": "data", "permissions": "READ, WRITE"}]) is True
    assert writable_share([{"name": "public", "permissions": "READ ONLY"}]) is False
    assert writable_share({"public": {"a.txt": {}}}) is None
    assert writable_share(None) is None


def test_nmap_scripts(db_session, test_project, tmp_path):
    path = _nmap_xml(
        tmp_path,
        port_scripts='<script id="vnc-info" output="&#xa;  Protocol version: 3.8&#xa;  Security types: &#xa;    None (1)"/>',
        host_scripts=(
            '<script id="smb2-security-mode" output="&#xa;  3:1:1: &#xa;    Message signing enabled but not required"/>'
            '<script id="smb-protocols" output="&#xa;  dialects: &#xa;    NT LM 0.12 (SMBv1) [dangerous, but default]&#xa;    2:0:2"/>'
        ),
    )
    scan = NmapXMLParser(db_session).parse_file(str(path), "scan.xml", project_id=test_project.id)
    assert _checks(db_session, scan.id) == {"vnc_no_auth", "smb_signing_not_required", "smbv1_enabled"}
    by_check = {v.plugin_id: v for v in _observations(db_session, scan.id)}
    assert by_check["vnc_no_auth"].port.port_number == 5900
    assert by_check["smb_signing_not_required"].port.port_number == 445
    assert all(v.source == VulnerabilitySource.NMAP for v in by_check.values())


def test_nmap_vnc_with_authentication_is_not_flagged(db_session, test_project, tmp_path):
    path = _nmap_xml(
        tmp_path,
        port_scripts='<script id="vnc-info" output="&#xa;  Protocol version: 3.8&#xa;  Security types: &#xa;    VNC Authentication (2)"/>',
        host_scripts='<script id="smb2-security-mode" output="&#xa;  3:1:1: &#xa;    Message signing enabled and required"/>',
    )
    scan = NmapXMLParser(db_session).parse_file(str(path), "scan.xml", project_id=test_project.id)
    assert _checks(db_session, scan.id) == set()


def test_one_issue_whichever_tool_saw_it(db_session, test_project, tmp_path):
    """nmap and NetExec report SMB signing on the same host: two scanner rows,
    one issue key — so Scanner observations shows one issue and promotion
    makes one finding."""
    nmap = NmapXMLParser(db_session).parse_file(
        str(_nmap_xml(tmp_path, host_scripts='<script id="smb2-security-mode" output="Message signing enabled but not required"/>')),
        "scan.xml", project_id=test_project.id)
    nxc = NetexecParser(db_session).parse_file(
        os.path.join(NATIVE, "netexec-samba.txt"), "netexec-samba.txt", project_id=test_project.id)
    rows = [v for v in _observations(db_session, nmap.id) + _observations(db_session, nxc.id)
            if v.plugin_id == "smb_signing_not_required"]
    assert {v.source for v in rows} == {VulnerabilitySource.NMAP, VulnerabilitySource.NETEXEC}
    assert len({v.issue_key for v in rows}) == 1


def _hosts(db, project, q):
    user = db.query(User).first()
    pred = D.evaluate(D.parse_query(q), D.BuildCtx(db=db, current_user=user, project_id=project.id))
    return {h.ip_address for h in db.query(models.Host).filter(models.Host.project_id == project.id, pred)}


def test_access_filters(db_session, test_project, test_user):
    scan = models.Scan(filename="t.txt", tool_name="netexec", project_id=test_project.id)
    db_session.add(scan)
    db_session.flush()
    hosts = {}
    for ip in ("10.8.2.1", "10.8.2.2", "10.8.2.3"):
        hosts[ip] = models.Host(project_id=test_project.id, ip_address=ip, state="up")
        db_session.add(hosts[ip])
    db_session.flush()
    db_session.add_all([
        NetexecResult(scan_id=scan.id, host_id=hosts["10.8.2.1"].id, protocol="smb", port=445, local_admin=True),
        NetexecResult(scan_id=scan.id, host_id=hosts["10.8.2.2"].id, protocol="smb", port=445, writable_share=True),
        NetexecResult(scan_id=scan.id, host_id=hosts["10.8.2.3"].id, protocol="smb", port=445, writable_share=False),
    ])
    db_session.flush()
    assert _hosts(db_session, test_project, "has:local_admin") == {"10.8.2.1"}
    assert _hosts(db_session, test_project, "has:writable_share") == {"10.8.2.2"}
    assert _hosts(db_session, test_project, "NOT has:writable_share") == {"10.8.2.1", "10.8.2.3"}


def _wiki(db, project, name):
    path = os.path.join(NATIVE, f"netexec-wiki-ftp-{name}.txt")
    return NetexecParser(db).parse_file(path, os.path.basename(path), project_id=project.id)


def test_netexec_anonymous_ftp_from_the_wiki(db_session, test_project):
    """nxc prints an anonymous login as "[+] : - Anonymous Login!"."""
    scan = _wiki(db_session, test_project, "authentication--block-01")
    assert _checks(db_session, scan.id) == {"ftp_anonymous"}


def test_netexec_action_lines_are_not_logins(db_session, test_project):
    """ftp --put / --get report "[+] Uploaded: …" / "[+] Downloaded: …"
    after the login; they were stored as logins by a user named "Uploaded"."""
    scan = _wiki(db_session, test_project, "get-and-put-files--block-04")
    rows = db_session.query(NetexecResult).filter_by(scan_id=scan.id).all()
    logins = [r for r in rows if r.auth_success]
    assert [r.username for r in logins] == ["frank"]
    assert _checks(db_session, scan.id) == set()


def test_nxc_only_host_is_up(db_session, test_project):
    """nxc prints a line only for a host that answered (v2.413.0)."""
    NetexecParser(db_session).parse_file(
        os.path.join(NATIVE, "netexec-wiki-vnc-ftp-ssh.txt"), "wiki.txt", project_id=test_project.id)
    host = db_session.query(models.Host).filter_by(project_id=test_project.id, ip_address="192.168.56.22").one()
    assert host.state == "up"


def test_netexec_action_results_from_the_nxc_source():
    """ssh.py's other success lines after a login (NetExec main, 2026-09-25)."""
    from app.parsers.netexec_parser import _ACTION_RESULTS
    for detail in ('Executed command', 'Uploaded: a.txt to b.txt', 'Downloaded: flag.txt',
                   'Created file "a.txt" on "/tmp/a.txt"', 'File "/etc/hosts" was downloaded to "hosts"'):
        assert detail.lower().startswith(_ACTION_RESULTS), detail


def test_netexec_spray_keeps_each_account(db_session, test_project):
    scan = _wiki(db_session, test_project, "password-spraying--block-05")
    rows = db_session.query(NetexecResult).filter_by(scan_id=scan.id, auth_success=True).all()
    assert {(db_session.get(models.Host, r.host_id).ip_address, r.username) for r in rows} == {
        ("127.31.0.1", "root"), ("127.31.0.0", "marshall"),
    }
    # A named account is not anonymous access.
    assert _checks(db_session, scan.id) == set()


def test_nxc_line_layout_is_recognised_without_tokens():
    from app.parsers import content_detection as cd
    with open(os.path.join(NATIVE, "netexec-wiki-vnc-ftp-ssh.txt"), "rb") as fh:
        vnc_only = b"".join(line for line in fh if line.startswith(b"VNC"))
    assert cd.looks_like_netexec(vnc_only, "capture.txt")
    assert not cd.looks_like_netexec(b"Open 10.0.0.1:5900\n", "capture.txt")


def test_web_header_wording_from_the_tools():
    """Nikto's messages (nikto_headers.plugin, and the 2.6.1 capture) and
    Nuclei's matcher names map onto one check each (v2.414.0)."""
    from app.services.misconfig_checks import nikto_header_check, nuclei_header_check
    assert nikto_header_check("/: Suggested security header missing: strict-transport-security.") == "http_missing_hsts"
    assert nikto_header_check("/: The X-Content-Type-Options header is not set. This could allow") == "http_missing_xcto"
    assert nikto_header_check("/: Retrieved x-powered-by header: PHP/7.4.3.") == "http_version_disclosure"
    assert nikto_header_check("/: Suggested security header missing: referrer-policy.") is None
    assert nikto_header_check("/: Retrieved access-control-allow-origin header: *.") is None
    assert nuclei_header_check("http-missing-security-headers", "x-frame-options") == "http_missing_frame_protection"
    assert nuclei_header_check("http-missing-security-headers", "referrer-policy") is None
    assert nuclei_header_check("tech-detect", "strict-transport-security") is None


def test_testssl_rated_checks_share_the_catalog(db_session, test_project, tmp_path):
    import json
    from app.parsers.testssl_parser import TestsslParser
    where = "web.example.com/10.8.4.1"
    records = [
        {"id": "SSLv3", "ip": where, "port": "443", "severity": "HIGH", "finding": "offered (NOT ok)"},
        {"id": "TLS1", "ip": where, "port": "443", "severity": "LOW", "finding": "offered (deprecated)"},
        {"id": "HSTS", "ip": where, "port": "443", "severity": "LOW", "finding": "not offered"},
        {"id": "cert_expirationStatus", "ip": where, "port": "443", "severity": "CRITICAL", "finding": "expired"},
        {"id": "heartbleed", "ip": where, "port": "443", "severity": "OK", "finding": "not vulnerable"},
    ]
    path = tmp_path / "testssl.json"
    path.write_text(json.dumps(records))
    scan = TestsslParser(db_session).parse_file(str(path), "testssl.json", project_id=test_project.id)
    rows = {v.plugin_id: v for v in _observations(db_session, scan.id)}
    assert set(rows) == {"tls_deprecated_protocol", "http_missing_hsts", "tls_cert_expired"}
    assert "SSLv3" in rows["tls_deprecated_protocol"].plugin_output
    assert "TLS 1.0" in rows["tls_deprecated_protocol"].plugin_output
    assert rows["http_missing_hsts"].name_id is not None


def test_vuln_filter_finds_the_catalog_title(db_session, test_project):
    NetexecParser(db_session).parse_file(
        os.path.join(NATIVE, "netexec-wiki-vnc-ftp-ssh.txt"), "wiki.txt", project_id=test_project.id)
    assert _hosts(db_session, test_project, 'vuln:"VNC server does not require authentication"') == {"192.168.56.22"}
