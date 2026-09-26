"""The backfill records, from evidence stored before the catalog existed,
what the parsers would record at import now (v2.414.0)."""
from __future__ import annotations

import os

from app.db import models
from app.db.models_confidence import NetexecResult
from app.db.models_vulnerability import Vulnerability, VulnerabilitySource
from app.services.misconfig_backfill import backfill_misconfigs

NATIVE = os.path.join(os.path.dirname(__file__), "fixtures", "native")


def _samba_lines():
    """The Samba lab capture's banner and guest-login lines, as stored."""
    with open(os.path.join(NATIVE, "netexec-samba.txt")) as fh:
        lines = [line.strip() for line in fh if "172.30.77.10" in line]
    banner = next(line for line in lines if "(signing:" in line)
    guest = next(line for line in lines if "(Guest)" in line)
    return banner, guest


def _seed(db, project):
    scan = models.Scan(filename="old.txt", tool_name="netexec", project_id=project.id)
    db.add(scan)
    db.flush()
    host = models.Host(project_id=project.id, ip_address="172.30.77.10", state="up",
                       smb_signing="not_required")
    other = models.Host(project_id=project.id, ip_address="10.8.5.9", state="up")
    db.add_all([host, other])
    db.flush()
    smb = models.Port(host_id=host.id, port_number=445, protocol="tcp", state="open")
    vnc = models.Port(host_id=other.id, port_number=5900, protocol="tcp", state="open")
    db.add_all([smb, vnc])
    db.flush()
    banner, guest = _samba_lines()
    db.add_all([
        NetexecResult(scan_id=scan.id, host_id=host.id, protocol="smb", port=445, raw_output=banner),
        NetexecResult(scan_id=scan.id, host_id=host.id, protocol="smb", port=445,
                      auth_success=True, username="guest", raw_output=guest),
        NetexecResult(scan_id=scan.id, host_id=host.id, protocol="smb", port=445, tool="smbmap",
                      auth_success=True, username="", raw_output="SMBMap 172.30.77.10:445 Status: NULL Session"),
        models.Script(port_id=vnc.id, scan_id=scan.id, script_id="vnc-info",
                      output="\n  Protocol version: 3.8\n  Security types: \n    None (1)"),
        models.HostScript(host_id=host.id, scan_id=scan.id, script_id="smb2-security-mode",
                          output="\n  3:1:1: \n    Message signing enabled but not required"),
    ])
    db.flush()
    return host, other


def _rows(db, project):
    return (db.query(Vulnerability).join(models.Host, Vulnerability.host_id == models.Host.id)
            .filter(models.Host.project_id == project.id).all())


def test_backfill_records_what_the_parsers_would(db_session, test_project):
    host, other = _seed(db_session, test_project)
    backfill_misconfigs(db_session, project_id=test_project.id)
    rows = _rows(db_session, test_project)
    got = {(db_session.get(models.Host, v.host_id).ip_address, v.plugin_id, v.source) for v in rows}
    assert got == {
        ("172.30.77.10", "smb_signing_not_required", VulnerabilitySource.NETEXEC),
        ("172.30.77.10", "smb_signing_not_required", VulnerabilitySource.NMAP),
        ("172.30.77.10", "smb_null_session", VulnerabilitySource.NETEXEC),
        ("172.30.77.10", "smb_null_session", VulnerabilitySource.SMBMAP),
        ("10.8.5.9", "vnc_no_auth", VulnerabilitySource.NMAP),
    }
    assert all(v.port is not None for v in rows)


def test_backfill_adopts_rows_stored_under_a_tools_title(db_session, test_project):
    """A Nikto header row and a testssl HSTS row imported before v2.414.0
    become the catalog check; a finding promoted from one takes its key."""
    from app.db.models_findings import Finding
    from app.parsers.parser_utils import upsert_vulnerability
    from app.db.models_vulnerability import VulnerabilitySeverity
    scan = models.Scan(filename="old", tool_name="nikto", project_id=test_project.id)
    db_session.add(scan)
    db_session.flush()
    host = models.Host(project_id=test_project.id, ip_address="10.8.6.1", state="up")
    db_session.add(host)
    db_session.flush()
    nikto = upsert_vulnerability(db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NIKTO,
                                 title="/: Suggested security header missing: strict-transport-security",
                                 severity=VulnerabilitySeverity.LOW, plugin_id="013587", key_on_title=True)
    testssl = upsert_vulnerability(db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.TESTSSL,
                                   title="HSTS not set", severity=VulnerabilitySeverity.LOW, plugin_id="HSTS")
    other = upsert_vulnerability(db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NIKTO,
                                 title="/admin/: Directory indexing found.", severity=VulnerabilitySeverity.LOW)
    finding = Finding(project_id=test_project.id, title="HSTS", severity="low", status="confirmed",
                      source="scanner", vuln_id=nikto.id, dedup_key=nikto.issue_key)
    db_session.add(finding)
    db_session.flush()

    backfill_misconfigs(db_session, project_id=test_project.id)
    db_session.flush()
    assert (nikto.check_id, testssl.check_id) == ("http_missing_hsts", "http_missing_hsts")
    assert nikto.issue_key == testssl.issue_key == "check:http_missing_hsts"
    assert nikto.title == "HTTP Strict-Transport-Security header missing"
    assert other.check_id is None
    assert finding.dedup_key == "check:http_missing_hsts"


def test_backfill_marks_nxc_only_hosts_up(db_session, test_project):
    host, _other = _seed(db_session, test_project)
    host.state = "unknown"
    db_session.flush()
    counts = backfill_misconfigs(db_session, project_id=test_project.id)
    db_session.refresh(host)
    assert host.state == "up"
    assert counts["hosts marked up (NetExec answered)"] == 1


def test_backfill_is_idempotent_and_project_scoped(db_session, test_project):
    _seed(db_session, test_project)
    backfill_misconfigs(db_session, project_id=test_project.id)
    first = len(_rows(db_session, test_project))
    backfill_misconfigs(db_session, project_id=test_project.id)
    assert len(_rows(db_session, test_project)) == first
    # Another project's run touches nothing here.
    assert backfill_misconfigs(db_session, project_id=test_project.id + 999) == {}
