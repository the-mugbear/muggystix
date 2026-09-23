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
