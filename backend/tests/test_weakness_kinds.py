"""Scanners' own checks join the misconfiguration catalog, and every
weakness has a kind (v2.415.0)."""
from __future__ import annotations

import json
import os

from app.db import models
from app.db.models_auth import User
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.parsers.nuclei_parser import NucleiParser
from app.parsers.parser_utils import upsert_vulnerability
from app.services import host_query_dsl as D
from app.services import scanner_observation_service as obs
from app.services.misconfig_checks import CHECKS, NESSUS_PLUGIN_CHECKS, record_misconfig, vuln_kind
from app.services.vuln_identity import issue_key

NATIVE = os.path.join(os.path.dirname(__file__), "fixtures", "native")


def test_a_check_is_the_issue_ahead_of_a_cve():
    assert issue_key(cve_id="CVE-1999-0497", title="Anonymous FTP Enabled", check_id="ftp_anonymous") == "check:ftp_anonymous"
    assert issue_key(cve_id="CVE-1999-0497", title="Anonymous FTP Enabled") == "cve:CVE-1999-0497"


def test_every_mapped_plugin_names_a_catalog_check():
    assert set(NESSUS_PLUGIN_CHECKS.values()) <= set(CHECKS)


def test_kind():
    assert vuln_kind("smbv1_enabled", "info") == "misconfiguration"
    assert vuln_kind(None, VulnerabilitySeverity.INFO) == "informational"
    assert vuln_kind(None, VulnerabilitySeverity.HIGH) == "vulnerability"


def _host(db, project, ip):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    port = models.Port(host_id=host.id, port_number=21, protocol="tcp", state="open")
    db.add(port)
    db.flush()
    return host


def _seed(db, project):
    scan = models.Scan(filename="s", tool_name="nessus", project_id=project.id)
    db.add(scan)
    db.flush()
    ftp_nmap = _host(db, project, "10.9.0.1")
    ftp_nessus = _host(db, project, "10.9.0.2")
    vuln_host = _host(db, project, "10.9.0.3")
    record_misconfig(db, check_id="ftp_anonymous", host_id=ftp_nmap.id, scan_id=scan.id,
                     source=VulnerabilitySource.NMAP, port_number=21)
    # A Nessus row the way VulnerabilityService writes a mapped plugin.
    db.add(Vulnerability(host_id=ftp_nessus.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                         plugin_id="10079", title=CHECKS["ftp_anonymous"].title, check_id="ftp_anonymous",
                         cve_id="CVE-1999-0497", severity=VulnerabilitySeverity.MEDIUM))
    upsert_vulnerability(db=db, host_id=vuln_host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                         title="Apache Tomcat < 9.0.86", severity=VulnerabilitySeverity.HIGH, plugin_id="999")
    upsert_vulnerability(db=db, host_id=vuln_host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                         title="OS Identification", severity=VulnerabilitySeverity.INFO, plugin_id="11936")
    db.flush()


def _hosts(db, project, q):
    user = db.query(User).first()
    pred = D.evaluate(D.parse_query(q), D.BuildCtx(db=db, current_user=user, project_id=project.id))
    return {h.ip_address for h in db.query(models.Host).filter(models.Host.project_id == project.id, pred)}


def test_one_issue_across_nmap_and_nessus(db_session, test_project):
    _seed(db_session, test_project)
    page = obs.list_issues(db_session, test_project.id, kind="misconfiguration")
    (issue,) = page.items
    assert issue.issue_key == "check:ftp_anonymous"
    assert issue.host_count == 2
    assert issue.sources == ["nessus", "nmap"]
    assert issue.kind == "misconfiguration"


def test_kind_filters(db_session, test_project):
    _seed(db_session, test_project)
    kinds = {i.title: i.kind for i in obs.list_issues(db_session, test_project.id).items}
    assert kinds["Apache Tomcat < 9.0.86"] == "vulnerability"
    assert kinds["OS Identification"] == "informational"
    assert [i.title for i in obs.list_issues(db_session, test_project.id, kind="vulnerability").items] == [
        "Apache Tomcat < 9.0.86"]
    assert _hosts(db_session, test_project, "kind:misconfiguration") == {"10.9.0.1", "10.9.0.2"}
    assert _hosts(db_session, test_project, "kind:vulnerability") == {"10.9.0.3"}
    assert _hosts(db_session, test_project, "check:ftp_anonymous") == {"10.9.0.1", "10.9.0.2"}


NESSUS_XML = """<?xml version="1.0" ?>
<NessusClientData_v2>
<Report name="kinds">
<ReportHost name="10.9.1.5">
  <HostProperties><tag name="host-ip">10.9.1.5</tag></HostProperties>
  <ReportItem port="445" svc_name="cifs" protocol="tcp" severity="2" pluginID="57608" pluginName="SMB Signing not required">
    <description>Signing is not required on the remote SMB server.</description>
    <solution>Enforce message signing.</solution>
  </ReportItem>
  <ReportItem port="21" svc_name="ftp" protocol="tcp" severity="2" pluginID="10079" pluginName="Anonymous FTP Enabled">
    <description>Nessus has detected that the FTP server allows anonymous logins.</description>
    <cve>CVE-1999-0497</cve>
  </ReportItem>
  <ReportItem port="443" svc_name="www" protocol="tcp" severity="3" pluginID="90317" pluginName="SSH Weak Algorithms Supported">
    <description>Not a catalog check.</description>
  </ReportItem>
</ReportHost>
</Report>
</NessusClientData_v2>
"""


def test_nessus_plugins_take_the_catalog_check(db_session, test_project, tmp_path):
    from app.services.nessus_integration_service import NessusIntegrationService
    path = tmp_path / "kinds.nessus"
    path.write_text(NESSUS_XML)
    result = NessusIntegrationService(db_session).process_nessus_file(str(path), project_id=test_project.id)
    assert result["success"], result
    rows = {v.plugin_id: v for v in db_session.query(Vulnerability).filter(Vulnerability.source == VulnerabilitySource.NESSUS)}
    smb = rows["57608"]
    assert (smb.check_id, smb.title, smb.issue_key) == (
        "smb_signing_not_required", "SMB signing not required", "check:smb_signing_not_required")
    assert smb.description.startswith("Signing is not required")  # Nessus's write-up stays
    ftp = rows["10079"]
    assert (ftp.check_id, ftp.issue_key, ftp.cve_id) == ("ftp_anonymous", "check:ftp_anonymous", "CVE-1999-0497")
    assert rows["90317"].check_id is None and rows["90317"].title == "SSH Weak Algorithms Supported"


def test_attention_counts_issues_not_rows(db_session, test_project):
    """One VNC issue on two ports is one high issue; medium is counted."""
    from app.api.v1.endpoints.hosts import _issue_counts
    scan = models.Scan(filename="s", tool_name="netexec", project_id=test_project.id)
    db_session.add(scan)
    db_session.flush()
    host = _host(db_session, test_project, "10.9.2.1")
    for port in (5900, 5901):
        db_session.add(models.Port(host_id=host.id, port_number=port, protocol="tcp", state="open"))
    db_session.flush()
    for port in (5900, 5901):
        record_misconfig(db_session, check_id="vnc_no_auth", host_id=host.id, scan_id=scan.id,
                         source=VulnerabilitySource.NETEXEC, port_number=port)
    record_misconfig(db_session, check_id="ftp_anonymous", host_id=host.id, scan_id=scan.id,
                     source=VulnerabilitySource.NETEXEC, port_number=21)
    db_session.flush()
    counts = _issue_counts(db_session, [host.id])[host.id]
    assert (counts["high"], counts["medium"], counts["misconfiguration"]) == (1, 1, 2)


def test_misconfiguration_is_not_a_vulnerability_assessment(db_session, test_project):
    from app.services.host_assessment_service import host_assessment
    scan = models.Scan(filename="s", tool_name="netexec", project_id=test_project.id)
    db_session.add(scan)
    db_session.flush()
    host = _host(db_session, test_project, "10.9.3.1")
    record_misconfig(db_session, check_id="ftp_anonymous", host_id=host.id, scan_id=scan.id,
                     source=VulnerabilitySource.NETEXEC, port_number=21)
    db_session.flush()
    assert host_assessment(db_session, host)["vuln_assessed"] is False
    upsert_vulnerability(db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NIKTO,
                         title="Outdated server", severity=VulnerabilitySeverity.LOW)
    db_session.flush()
    assert host_assessment(db_session, host)["vuln_assessed"] is True


def test_nuclei_templates_map_to_checks(db_session, test_project, tmp_path):
    base = json.load(open(os.path.join(NATIVE, "nuclei-je.json")))[3]  # the tcp (vnc-service-detect) record
    record = dict(base, **{"template-id": "ftp-anonymous-login", "port": "21", "host": "10.20.0.9:21",
                           "matched-at": "10.20.0.9:21"})
    record["info"] = {"name": "FTP Anonymous Login", "severity": "medium"}
    path = tmp_path / "n.json"
    path.write_text(json.dumps([record]))
    scan = NucleiParser(db_session).parse_file(str(path), "n.json", project_id=test_project.id)
    (row,) = db_session.query(Vulnerability).filter(Vulnerability.scan_id == scan.id).all()
    assert (row.check_id, row.title, row.issue_key) == (
        "ftp_anonymous", "Anonymous FTP login allowed", "check:ftp_anonymous")
