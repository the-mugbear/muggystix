"""An evidence gap, host by host (plan A8, 2026-10-10).

The Evidence page answers per PROJECT: for each kind of evidence, the hosts
it applies to and the hosts that carry it (``eligible_host_ids`` /
``assessed_host_ids``).  A list of hosts needs the same rule per host — the
Hosts query's ``gap:<domain>`` and the gaps a host row names.  Those are
``evidence_service.domain_eligible_condition`` / ``domain_assessed_condition``.

There is one definition of each only if the two agree, so this file pins the
predicate to the set, domain by domain, over an estate that has every case
on both sides and a second project that must never leak in.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db import models
from app.db.models_confidence import NetexecResult
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import Project
from app.db.models_proposals import EvidenceRecord
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.evidence_service import (
    EVIDENCE_DOMAINS, assessed_host_ids, domain_assessed_condition, domain_eligible_condition,
    eligible_host_ids, evidence_gap_condition, evidence_gap_hosts, host_evidence_gaps,
)

DOMAINS = [d["key"] for d in EVIDENCE_DOMAINS]


def _host(db, project_id, ip, ports=(), **fields):
    h = models.Host(project_id=project_id, ip_address=ip, state="up", **fields)
    h.last_seen = datetime.now(timezone.utc)
    db.add(h)
    db.flush()
    for number, service in ports:
        db.add(models.Port(host_id=h.id, port_number=number, protocol="tcp", state="open", service_name=service))
    db.flush()
    return h


def _estate(db, project_id):
    """Every case of every domain.  Returns the hosts by what they are."""
    nessus = models.Scan(project_id=project_id, filename="v.nessus", tool_name="Nessus")
    nmap = models.Scan(project_id=project_id, filename="p.xml", tool_name="nmap")
    db.add_all([nessus, nmap])
    db.flush()
    hosts = {
        # Listed only: no port at all.
        "bare": _host(db, project_id, "10.7.0.1"),
        # Ports nobody identified; no OS.
        "ports_unidentified": _host(db, project_id, "10.7.0.2", ports=[(9999, None)]),
        # Identified service and an OS; nothing else.
        "identified": _host(db, project_id, "10.7.0.3", ports=[(22, "ssh")], os_name="Debian 12"),
        # An empty OS name is no OS.
        "empty_os": _host(db, project_id, "10.7.0.4", ports=[(22, "ssh")], os_name=""),
        # Web by port number, and by service name on another port: neither fingerprinted.
        "web_port": _host(db, project_id, "10.7.0.5", ports=[(8443, None)]),
        "web_named": _host(db, project_id, "10.7.0.6", ports=[(7001, "http-alt")]),
        # Web, fingerprinted.
        "web_seen": _host(db, project_id, "10.7.0.7", ports=[(443, "https")]),
        # SMB exposed: not enumerated / signing recorded / NetExec ran.
        "smb_gap": _host(db, project_id, "10.7.0.8", ports=[(445, "microsoft-ds")]),
        "smb_signing": _host(db, project_id, "10.7.0.9", ports=[(445, "microsoft-ds")], smb_signing="disabled"),
        "smb_netexec": _host(db, project_id, "10.7.0.10", ports=[(389, "ldap")]),
        # Vulnerability-assessed by a clean scanner run, and by a row only.
        "scanned_clean": _host(db, project_id, "10.7.0.11", ports=[(22, "ssh")]),
        "vuln_row": _host(db, project_id, "10.7.0.12", ports=[(22, "ssh")]),
        # A misconfiguration check is not a vulnerability assessment.
        "check_row": _host(db, project_id, "10.7.0.13", ports=[(22, "ssh")]),
        # Carries a finding: untested / tested / only a failed attempt.
        "finding_untested": _host(db, project_id, "10.7.0.14", ports=[(22, "ssh")]),
        "finding_tested": _host(db, project_id, "10.7.0.15", ports=[(22, "ssh")]),
        "finding_failed_attempt": _host(db, project_id, "10.7.0.16", ports=[(22, "ssh")]),
    }
    db.add(models.WebInterface(project_id=project_id, host_id=hosts["web_seen"].id, scan_id=nmap.id,
                               url="https://10.7.0.7/", source="httpx"))
    db.add(NetexecResult(scan_id=nmap.id, host_id=hosts["smb_netexec"].id, protocol="ldap", port=389))
    db.add(models.HostScanHistory(host_id=hosts["scanned_clean"].id, scan_id=nessus.id))
    db.add(models.HostScanHistory(host_id=hosts["identified"].id, scan_id=nmap.id))
    db.add(Vulnerability(host_id=hosts["vuln_row"].id, scan_id=nmap.id, title="v",
                         severity=VulnerabilitySeverity.LOW, source=VulnerabilitySource.NESSUS))
    db.add(Vulnerability(host_id=hosts["check_row"].id, scan_id=nmap.id, title="smb signing",
                         severity=VulnerabilitySeverity.LOW, source=VulnerabilitySource.NESSUS,
                         check_id="smb-signing-not-required"))
    finding = Finding(project_id=project_id, title="f", severity="high", status="open", source="manual")
    db.add(finding)
    db.flush()
    for key in ("finding_untested", "finding_tested", "finding_failed_attempt"):
        db.add(FindingHost(finding_id=finding.id, host_id=hosts[key].id, host_status="open"))
    for key, outcome in (("finding_tested", "no_finding"), ("finding_failed_attempt", "failed")):
        db.add(EvidenceRecord(project_id=project_id, host_id=hosts[key].id, tool="curl",
                              outcome=outcome, summary="s", executed_at=datetime.now(timezone.utc)))
    db.commit()
    return hosts


@pytest.fixture()
def estate(db_session, test_project):
    hosts = _estate(db_session, test_project.id)
    # The same estate in another project: nothing of it may count here.
    other = Project(name="other-evidence", slug="other-evidence", description="x")
    db_session.add(other)
    db_session.commit()
    _estate(db_session, other.id)
    return hosts


def _matching(db, project_id, condition):
    return set(db.execute(
        select(models.Host.id).where(models.Host.project_id == project_id, condition)
    ).scalars())


@pytest.mark.parametrize("domain", DOMAINS)
def test_the_host_predicates_are_the_evidence_pages_sets(db_session, test_project, estate, domain):
    pid = test_project.id
    eligible = eligible_host_ids(db_session, pid, [domain])[domain]
    assessed = assessed_host_ids(db_session, pid, [domain])[domain]
    assert eligible and (eligible - assessed) and (eligible & assessed), "the estate must have both sides"

    assert _matching(db_session, pid, domain_eligible_condition(domain)) == eligible
    assert _matching(db_session, pid, domain_assessed_condition(domain)) == assessed
    assert _matching(db_session, pid, evidence_gap_condition(domain)) == eligible - assessed


@pytest.mark.parametrize("domain", DOMAINS)
def test_gap_in_the_hosts_query_opens_exactly_the_evidence_gap(client, db_session, test_project, estate, domain):
    gap = evidence_gap_hosts(db_session, test_project.id, domain)
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": f"gap:{domain}", "limit": 100})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == gap["total"] > 0
    assert {h["id"] for h in body["items"]} == {h["host_id"] for h in gap["items"]}
    # ...and its negation is every other host of the project, none dropped.
    rest = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": f"NOT gap:{domain}", "limit": 100})
    assert rest.json()["total"] == len(estate) - gap["total"]


def test_an_unknown_gap_is_refused_naming_the_accepted_ones(client, test_project):
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": "gap:everything"})
    assert r.status_code == 400
    assert "web_tls" in r.text


def test_a_page_of_hosts_names_each_hosts_gaps_in_one_statement(db_session, test_project, estate):
    from sqlalchemy import event

    ids = [h.id for h in estate.values()]  # read first: an expired host reloads itself
    statements = []
    listen = lambda conn, cursor, statement, *a: statements.append(statement)  # noqa: E731
    event.listen(db_session.bind, "before_cursor_execute", listen)
    try:
        gaps = host_evidence_gaps(db_session, ids)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", listen)
    assert len(statements) == 1

    pid = test_project.id
    for domain in DOMAINS:
        expected = eligible_host_ids(db_session, pid, [domain])[domain] - assessed_host_ids(db_session, pid, [domain])[domain]
        assert {hid for hid, keys in gaps.items() if domain in keys} == expected
    # A host with nothing missing is not in the answer at all.
    assert gaps[estate["bare"].id] == ["port_discovery", "vuln_assessment"]
    assert host_evidence_gaps(db_session, []) == {}
