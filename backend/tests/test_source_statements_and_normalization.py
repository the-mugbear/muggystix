"""Normalization across tools (owner, 2026-11-06: "normalization is paramount").

A scanner observation's fields are the TOOL's statements.  A tool that cannot
make one leaves the column empty — never a default that reads as a statement.

* ``source_statements`` is the one declaration of what each source can state;
  every ``VulnerabilitySource`` has one; the Tool coverage payload carries it;
  a coverage claim of ``Vulnerability.exploitable`` / ``cvss_score`` is made
  only by a tool declared able to state it.
* The writers refuse an undeclared statement (``refuse_undeclared`` in
  ``upsert_vulnerability``).
* Nikto rates nothing: no parser writes a literal fallback severity.
* ``cvss_version`` is derived from the vector in one place, on insert and on
  refresh.
* Every CVE a tool names is kept (``vulnerability_cves``); the first stays
  ``cve_id``; the ``cve:`` filter, the observations list's search and the
  Query page find a row by a CVE it did not lead with.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db import models
from app.db.models import Host
from app.db.models_vulnerability import (
    Vulnerability, VulnerabilityCve, VulnerabilitySeverity, VulnerabilitySource,
)
from app.parsers.parser_utils import upsert_vulnerability
from app.services import host_query_predicates as P
from app.services import scanner_observation_service as svc
from app.services.cvss_service import cvss_version_of
from app.services.parser_coverage import coverage_payload, load_coverage
from app.services.source_statements import (
    STATEMENTS, UndeclaredStatement, refuse_undeclared, statements_for_tool_id, statements_of,
)
from tests.conftest import USING_POSTGRES

PARSERS = Path(__file__).resolve().parents[1] / "app" / "parsers"


# --- the declaration -------------------------------------------------------

def test_every_source_has_a_declaration():
    assert set(STATEMENTS) == set(VulnerabilitySource)
    for source in VulnerabilitySource:
        assert statements_of(source) is STATEMENTS[source]


def test_the_coverage_page_carries_the_declaration_for_tools_that_write_observations():
    payload = coverage_payload()
    by_id = {t["id"]: t for t in payload["tools"]}
    assert by_id["nikto"]["statements"] == {"severity": True, "cvss": False, "cve": True, "exploitability": False}
    assert by_id["nessus"]["statements"] == {"severity": True, "cvss": True, "cve": True, "exploitability": True}
    without = [t for t in payload["tools"] if statements_for_tool_id(t["id"]) is None]
    assert without, "some tool writes no observation rows (masscan, amass…) and says so with null"
    assert all(t["statements"] is None for t in without)


def test_no_tool_claims_a_statement_its_source_cannot_make():
    """The coverage catalogue (what each parser WRITES) never names a column
    the source is declared unable to state."""
    for tool in load_coverage():
        declared = statements_for_tool_id(tool.id)
        if declared is None:
            continue
        stored = {col for signal in tool.signals for col in signal.stored_as}
        if "Vulnerability.exploitable" in stored:
            assert declared["exploitability"], f"{tool.id} claims exploitability it cannot state"
        if "Vulnerability.cvss_score" in stored or "Vulnerability.cvss_vector" in stored:
            assert declared["cvss"], f"{tool.id} claims CVSS it cannot state"
        if "Vulnerability.cve_id" in stored:
            assert declared["cve"], f"{tool.id} claims CVEs it cannot state"


def test_an_undeclared_statement_is_refused_and_a_declared_one_passes():
    with pytest.raises(UndeclaredStatement):
        refuse_undeclared(VulnerabilitySource.NIKTO, exploitable=True)
    with pytest.raises(UndeclaredStatement):
        refuse_undeclared(VulnerabilitySource.NIKTO, exploitable=False)
    with pytest.raises(UndeclaredStatement):
        refuse_undeclared(VulnerabilitySource.NETEXEC, severity=VulnerabilitySeverity.HIGH)
    with pytest.raises(UndeclaredStatement):
        refuse_undeclared(VulnerabilitySource.NIKTO, cvss_score=7.5)
    # UNKNOWN is the absence of a statement, not one.
    refuse_undeclared(VulnerabilitySource.NETEXEC, severity=VulnerabilitySeverity.UNKNOWN)
    refuse_undeclared(VulnerabilitySource.NIKTO, severity=VulnerabilitySeverity.LOW, cve_id="CVE-2020-1")
    refuse_undeclared(VulnerabilitySource.NESSUS, severity=VulnerabilitySeverity.HIGH, cvss_score=9.8,
                      cvss_vector="CVSS:3.1/AV:N", cve_id="CVE-2023-1", exploitable=True)


def test_no_parser_writes_a_literal_fallback_severity():
    """Nikto's rows were LOW because the parser said so, not the tool.  A
    parser that has no rating writes UNKNOWN."""
    offenders = []
    for path in PARSERS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if 'map_text_severity("low")' in text or "map_text_severity('low')" in text:
            offenders.append(path.name)
    assert offenders == []


# --- writers ------------------------------------------------------------------

def _host(db, project_id, ip):
    h = Host(project_id=project_id, ip_address=ip, state="up",
             first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc))
    db.add(h)
    db.flush()
    return h


def _scan(db, project_id, name="a.xml", tool="nessus"):
    scan = models.Scan(project_id=project_id, filename=name, scan_type=tool, tool_name=tool)
    db.add(scan)
    db.flush()
    return scan


def test_the_writer_refuses_an_undeclared_statement(db_session, test_project):
    host = _host(db_session, test_project.id, "10.78.0.1")
    scan = _scan(db_session, test_project.id, tool="nikto")
    with pytest.raises(UndeclaredStatement):
        upsert_vulnerability(db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NIKTO,
                             title="x", severity=VulnerabilitySeverity.UNKNOWN, exploitable=True)


def test_cvss_version_is_derived_from_the_vector_once():
    assert cvss_version_of("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == "3.1"
    assert cvss_version_of("CVSS:3.0/AV:N/AC:L") == "3.0"
    assert cvss_version_of("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N") == "4.0"
    assert cvss_version_of("AV:N/AC:L/Au:N/C:P/I:P/A:P") == "2.0"
    assert cvss_version_of("CVSS2#AV:N/AC:L/Au:N/C:P/I:P/A:P") == "2.0"
    assert cvss_version_of("(AV:N/AC:L/Au:N/C:P/I:P/A:P)") == "2.0"
    assert cvss_version_of("") is None
    assert cvss_version_of(None) is None
    assert cvss_version_of("not a vector") is None


def test_the_writer_stores_the_version_on_insert_and_on_refresh(db_session, test_project):
    host = _host(db_session, test_project.id, "10.78.0.2")
    first, second = _scan(db_session, test_project.id, "1.xml"), _scan(db_session, test_project.id, "2.xml")
    row = upsert_vulnerability(db=db_session, host_id=host.id, scan_id=first.id, source=VulnerabilitySource.NESSUS,
                               title="A hole", severity=VulnerabilitySeverity.HIGH, plugin_id="1",
                               cvss_score=5.0, cvss_vector="AV:N/AC:L/Au:N/C:P/I:P/A:P")
    assert row.cvss_version == "2.0"
    again = upsert_vulnerability(db=db_session, host_id=host.id, scan_id=second.id, source=VulnerabilitySource.NESSUS,
                                 title="A hole", severity=VulnerabilitySeverity.HIGH, plugin_id="1",
                                 cvss_score=9.8, cvss_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
    assert again is row and row.cvss_version == "3.1"
    # No vector this time: score and version stay.
    upsert_vulnerability(db=db_session, host_id=host.id, scan_id=second.id, source=VulnerabilitySource.NESSUS,
                         title="A hole", severity=VulnerabilitySeverity.HIGH, plugin_id="1")
    assert (row.cvss_score, row.cvss_version) == (9.8, "3.1")


def _cves_of(db, row):
    return sorted(c for (c,) in db.query(VulnerabilityCve.cve_id).filter(VulnerabilityCve.vulnerability_id == row.id))


def test_every_cve_a_tool_names_is_kept_and_the_first_stays_the_identity(db_session, test_project):
    host = _host(db_session, test_project.id, "10.78.0.3")
    scan = _scan(db_session, test_project.id)
    row = upsert_vulnerability(db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                               title="OpenSSH", severity=VulnerabilitySeverity.HIGH, plugin_id="2",
                               cve_id="CVE-2023-0001", cve_ids=["cve-2023-0001", "CVE-2023-0002", "CVE-2023-0002"])
    assert row.cve_id == "CVE-2023-0001"
    assert _cves_of(db_session, row) == ["CVE-2023-0001", "CVE-2023-0002"]
    # A later report naming fewer never removes one; a new one is added.
    upsert_vulnerability(db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                         title="OpenSSH", severity=VulnerabilitySeverity.HIGH, plugin_id="2",
                         cve_id="CVE-2023-0001", cve_ids=["CVE-2023-0003"])
    assert _cves_of(db_session, row) == ["CVE-2023-0001", "CVE-2023-0002", "CVE-2023-0003"]
    # No list given: the first CVE alone is recorded.
    other = upsert_vulnerability(db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                                 title="Another", severity=VulnerabilitySeverity.LOW, plugin_id="3",
                                 cve_id="CVE-2021-9")
    assert _cves_of(db_session, other) == ["CVE-2021-9"]
    # A row with no CVE has no child rows.
    none = upsert_vulnerability(db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                                title="No CVE", severity=VulnerabilitySeverity.LOW, plugin_id="4")
    assert _cves_of(db_session, none) == []


def test_the_nessus_path_keeps_every_cve_too(db_session, test_project):
    from app.parsers.nessus_parser import NessusVulnerability
    from app.services.vulnerability_service import VulnerabilityService

    host = _host(db_session, test_project.id, "10.78.0.4")
    scan = _scan(db_session, test_project.id, "n.nessus")
    item = NessusVulnerability(
        plugin_id="77777", plugin_name="Many CVEs", severity=3, risk_factor="High",
        cvss_base_score=7.5, cvss_vector="AV:N/AC:L/Au:N/C:P/I:P/A:P", cvss3_base_score=9.8,
        cvss3_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", cve_list=["CVE-2024-1", "CVE-2024-2"],
        description="d", solution="", synopsis="s", plugin_output=None, port=22, protocol="tcp",
        service_name="ssh", exploitable=None, patch_publication_date=None, vuln_publication_date=None,
    )
    row = VulnerabilityService(db_session)._create_vulnerability_from_nessus(item, host, scan, manage_savepoint=False)
    db_session.flush()
    assert (row.cve_id, row.cvss_version, row.cvss_score) == ("CVE-2024-1", "3.1", 9.8)
    assert _cves_of(db_session, row) == ["CVE-2024-1", "CVE-2024-2"]


# --- readers ------------------------------------------------------------------

@pytest.fixture
def two_cves(db_session, test_project):
    host = _host(db_session, test_project.id, "10.78.1.1")
    scan = _scan(db_session, test_project.id)
    row = upsert_vulnerability(db=db_session, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
                               title="Leads with one, names two", severity=VulnerabilitySeverity.HIGH, plugin_id="9",
                               cve_id="CVE-2023-38408", cve_ids=["CVE-2023-38408", "CVE-2023-51385"])
    db_session.commit()
    return {"host": host, "row": row}


def test_the_cve_filter_finds_a_host_by_a_cve_the_row_did_not_lead_with(db_session, two_cves):
    ids = {i for (i,) in db_session.query(Host.id).filter(P.cve_predicate(["CVE-2023-51385"]))}
    assert ids == {two_cves["host"].id}
    assert {i for (i,) in db_session.query(Host.id).filter(P.cve_predicate(["CVE-2023-38408"]))} == ids
    assert {i for (i,) in db_session.query(Host.id).filter(P.cve_predicate(["CVE-1999-0001"]))} == set()


def test_the_observations_search_finds_an_issue_by_any_of_its_cves(db_session, test_project, two_cves):
    found = svc.list_issues(db_session, test_project.id, judged="any", search="CVE-2023-51385").items
    assert [r.title for r in found] == ["Leads with one, names two"]
    assert svc.list_issues(db_session, test_project.id, judged="any", search="CVE-1999-0001").items == []


@pytest.mark.skipif(not USING_POSTGRES, reason="the Query page compiles for Postgres")
def test_the_query_page_lists_every_cve_and_the_version(client, test_project, two_cves):
    body = client.get(f"/api/v1/projects/{test_project.id}/query",
                      params={"q": 'observations | where like(cves, "%CVE-2023-51385%") | fields cve, cves, cvss_version'}).json()
    assert "rows" in body, body
    assert body["rows"] == [["CVE-2023-38408", "CVE-2023-38408 CVE-2023-51385", None]]
