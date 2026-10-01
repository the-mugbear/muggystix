"""Nessus: whether the scan authenticated to each host (review 2026-10-01 B12).

The host's ``Credentialed_Scan`` tag answers it; a file without the tag is
read from plugin 19506's ``Credentialed checks : yes|no`` line.  It lands on
``host_scan_history.credentialed`` — per host, per scan, NULL when the file
does not say — and the import's message counts both.
"""
from __future__ import annotations

import textwrap

from app.db import models
from app.parsers.nessus_parser import NessusVulnerability, credentialed_status
from app.services.nessus_integration_service import NessusIntegrationService


def _host_xml(ip, *, tag=None, scan_info=None, findings=True):
    props = f'<tag name="host-ip">{ip}</tag>'
    if tag is not None:
        props += f'<tag name="Credentialed_Scan">{tag}</tag>'
    items = ""
    if scan_info is not None:
        items += (
            '<ReportItem port="0" svc_name="general" protocol="tcp" severity="0" pluginID="19506" '
            'pluginName="Nessus Scan Information"><description>Scan info.</description>'
            f"<plugin_output>{scan_info}</plugin_output></ReportItem>"
        )
    if findings:
        items += (
            '<ReportItem port="22" svc_name="ssh" protocol="tcp" severity="3" pluginID="90317" '
            'pluginName="SSH Weak Algorithms Supported"><description>Weak.</description>'
            "<risk_factor>High</risk_factor></ReportItem>"
        )
    return f'<ReportHost name="{ip}"><HostProperties>{props}</HostProperties>{items}</ReportHost>'


SCAN_INFO_YES = textwrap.dedent("""\
    Information about this scan :

    Nessus version : 10.7.2
    Scanner IP : 10.0.0.2
    Port scanner(s) : nessus_syn_scanner
    Credentialed checks : yes, as 'svc-nessus' via ssh
    Patch management checks : None
""")
SCAN_INFO_NO = SCAN_INFO_YES.replace("yes, as 'svc-nessus' via ssh", "no")


def _import(db, project_id, tmp_path, hosts, **kwargs):
    path = tmp_path / "cred.nessus"
    path.write_text(
        '<?xml version="1.0" ?><NessusClientData_v2><Report name="cred">'
        + "".join(hosts) + "</Report></NessusClientData_v2>"
    )
    result = NessusIntegrationService(db).process_nessus_file(str(path), project_id=project_id, **kwargs)
    assert result["success"], result
    return result


def _credentialed(db, project_id, scan_id):
    rows = (
        db.query(models.Host.ip_address, models.HostScanHistory.credentialed)
        .join(models.HostScanHistory, models.HostScanHistory.host_id == models.Host.id)
        .filter(models.Host.project_id == project_id, models.HostScanHistory.scan_id == scan_id)
        .all()
    )
    return dict(rows)


def _vuln(plugin_id, output):
    return NessusVulnerability(
        plugin_id=plugin_id, plugin_name="n", severity=0, risk_factor="None", cvss_base_score=None,
        cvss_vector=None, cvss3_base_score=None, cvss3_vector=None, cve_list=[], description="",
        solution="", synopsis="", plugin_output=output, port=0, protocol="tcp", service_name=None,
        exploitable=False, patch_publication_date=None, vuln_publication_date=None,
    )


def test_the_tag_answers_and_the_plugin_line_is_the_fallback():
    assert credentialed_status({"Credentialed_Scan": "true"}, []) is True
    assert credentialed_status({"Credentialed_Scan": "false"}, []) is False
    # The host's own tag wins over the plugin text.
    assert credentialed_status({"Credentialed_Scan": "false"}, [_vuln("19506", SCAN_INFO_YES)]) is False
    assert credentialed_status({}, [_vuln("19506", SCAN_INFO_YES)]) is True
    assert credentialed_status({}, [_vuln("19506", SCAN_INFO_NO)]) is False
    # Another plugin's text is not the scan's statement, and silence is None.
    assert credentialed_status({}, [_vuln("12345", SCAN_INFO_YES)]) is None
    assert credentialed_status({}, [_vuln("19506", "Nessus version : 10.7.2")]) is None
    assert credentialed_status({}, []) is None


def test_each_host_records_whether_the_scan_authenticated(db_session, test_project, tmp_path):
    pid = test_project.id
    result = _import(db_session, pid, tmp_path, [
        _host_xml("10.30.0.1", tag="true"),
        _host_xml("10.30.0.2", tag="false"),
        _host_xml("10.30.0.3", scan_info=SCAN_INFO_YES),
        _host_xml("10.30.0.4", scan_info=SCAN_INFO_NO),
        _host_xml("10.30.0.5"),
    ])
    assert _credentialed(db_session, pid, result["scan_id"]) == {
        "10.30.0.1": True,
        "10.30.0.2": False,
        "10.30.0.3": True,
        "10.30.0.4": False,
        "10.30.0.5": None,
    }
    assert result["hosts_credentialed"] == 2
    assert result["hosts_uncredentialed"] == 2
    assert "credentialed checks ran on 2 host(s), not on 2" in result["message"]


def test_it_is_recorded_when_informational_items_are_skipped(db_session, test_project, tmp_path):
    """Plugin 19506 is severity 0: the switch drops its row, not what it says."""
    pid = test_project.id
    result = _import(db_session, pid, tmp_path, [
        _host_xml("10.30.1.1", scan_info=SCAN_INFO_NO, findings=False),
    ], skip_informational=True)
    assert result["vulnerabilities_found"] == 0
    assert _credentialed(db_session, pid, result["scan_id"]) == {"10.30.1.1": False}


def test_a_file_that_does_not_say_claims_nothing(db_session, test_project, tmp_path):
    pid = test_project.id
    result = _import(db_session, pid, tmp_path, [_host_xml("10.30.2.1")])
    assert _credentialed(db_session, pid, result["scan_id"]) == {"10.30.2.1": None}
    assert "credentialed" not in result["message"]


def test_the_status_is_the_scans_not_the_hosts(db_session, test_project, tmp_path):
    """An unauthenticated scan after an authenticated one: each row keeps its own."""
    pid = test_project.id
    first = _import(db_session, pid, tmp_path, [_host_xml("10.30.3.1", tag="true")])
    second = _import(db_session, pid, tmp_path, [_host_xml("10.30.3.1", tag="false")])
    assert _credentialed(db_session, pid, first["scan_id"]) == {"10.30.3.1": True}
    assert _credentialed(db_session, pid, second["scan_id"]) == {"10.30.3.1": False}
