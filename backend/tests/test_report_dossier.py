"""Host-centric dossier report — the correlated per-host record.

The comprehensive report is host-first: one consolidated dossier per host that
pulls together canonical findings (with their resolved source row + per-host
status), untriaged scanner observations, test findings (evidence records whose
outcome is ``finding`` — the dataset key is still ``execution_findings``),
tester summaries (``HostTest.tester_summary``), and notes.  These tests pin the assembly (``_build_export_context`` +
``_build_host_export_record``) and the per-format caps.
"""

from unittest.mock import MagicMock

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.db.models_findings import Finding, FindingHost
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord
from app.api.v1.endpoints.reports import ReportGenerator


def _gen(db, project_id, user_id):
    return ReportGenerator(db=db, current_user=MagicMock(id=user_id), project_id=project_id)


def test_dossier_record_correlates_every_source(db_session, test_project, test_user):
    """One host with a promoted scanner finding, an untriaged scanner vuln, a
    note, a tester summary, and a test finding — the record carries all of
    them, and untriaged vulns exclude the one already promoted."""
    host = models.Host(project_id=test_project.id, ip_address="10.55.0.5", state="up", os_name="Linux")
    db_session.add(host)
    db_session.flush()

    scan = models.Scan(project_id=test_project.id, filename="nessus.xml")
    db_session.add(scan)
    db_session.flush()

    promoted = Vulnerability(
        host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
        severity=VulnerabilitySeverity.HIGH, title="Promoted RCE", cve_id="CVE-2024-1",
        solution="Patch it",
    )
    lonely = Vulnerability(
        host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
        severity=VulnerabilitySeverity.MEDIUM, title="Lonely vuln", cve_id="CVE-2024-2",
    )
    db_session.add_all([promoted, lonely])
    db_session.flush()

    finding = Finding(
        project_id=test_project.id, title="Promoted RCE", severity="high",
        status="open", source="scanner", vuln_id=promoted.id,
    )
    db_session.add(finding)
    db_session.flush()
    db_session.add(FindingHost(finding_id=finding.id, host_id=host.id, host_status="open"))

    db_session.add(models.Annotation(
        host_id=host.id, user_id=test_user.id, body="left an SSH key",
        note_type="finding",
    ))

    test = HostTest(
        project_id=test_project.id, host_id=host.id, tool="nmap", description="Version scan",
        rationale="x", priority="high", status="done", label="Web review",
        tester_summary="Found admin panel", source="person", created_by_user_id=test_user.id,
        request_key="dossier-1", request_hash="0" * 64,
    )
    # A test with no summary contributes no tester-summary row.
    quiet = HostTest(
        project_id=test_project.id, host_id=host.id, tool="curl", description="Headers",
        rationale="x", priority="low", status="proposed", tester_summary="   ", source="person",
        request_key="dossier-2", request_hash="1" * 64,
    )
    db_session.add_all([test, quiet])
    db_session.flush()
    db_session.add_all([
        EvidenceRecord(
            project_id=test_project.id, host_id=host.id, host_test_id=test.id, tool="nmap",
            command="nmap -sV", outcome="finding", summary="popped a shell",
        ),
        # Only an outcome of ``finding`` is a test finding.
        EvidenceRecord(
            project_id=test_project.id, host_id=host.id, host_test_id=test.id, tool="nmap",
            command="nmap -sC", outcome="no_finding", summary="nothing there",
        ),
    ])
    db_session.commit()

    gen = _gen(db_session, test_project.id, test_user.id)
    ctx = gen._build_export_context([host])
    rec = gen._build_host_export_record(host, ctx, {})

    # Canonical finding with per-host status + resolved scanner source detail.
    assert [c["title"] for c in rec["canonical_findings"]] == ["Promoted RCE"]
    cf = rec["canonical_findings"][0]
    assert cf["host_status"] == "open"
    assert cf["source_detail"]["cve_id"] == "CVE-2024-1"
    assert cf["source_detail"]["solution"] == "Patch it"

    # Untriaged excludes the promoted vuln, keeps the lonely one.
    untriaged = [v["title"] for v in rec["untriaged_vulnerabilities"]]
    assert "Lonely vuln" in untriaged
    assert "Promoted RCE" not in untriaged

    assert [t["findings"] for t in rec["tester_summaries"]] == ["Found admin panel"]
    assert rec["tester_summaries"][0]["label"] == "Web review"
    assert [x["findings_summary"] for x in rec["execution_findings"]] == ["popped a shell"]
    ef = rec["execution_findings"][0]
    assert (ef["tool"], ef["label"], ef["command"], ef["host_test_id"]) == (
        "nmap", "Web review", "nmap -sV", test.id,
    )
    assert ef["promoted"] is False
    assert gen._inventory_finding_counts([host.id])[host.id]["exec"] == 1
    assert rec["analyst_context"]["notes"], "host note should be present"

    summary = rec["dossier_summary"]
    assert summary["active_findings"] == 1
    assert summary["untriaged_vulns"] == 1
    assert summary["execution_findings"] == 1
    assert summary["tester_summaries"] == 1
    # Finding-severity and vuln-severity tallies are kept separate.
    assert summary["findings_by_severity"] == {"high": 1}
    assert summary["vulns_by_severity"]["high"] == 1


def test_a_test_finding_cited_by_a_finding_is_marked_promoted(db_session, test_project, test_user):
    """An evidence record that points at a canonical finding (``finding_id``)
    is the finding's source detail and is marked ``promoted`` in the host's
    test findings; an unrelated one is not.  (Until v2.442.0 the link was
    ``findings.exec_result_id``.)"""
    host = models.Host(project_id=test_project.id, ip_address="10.55.0.6", state="up")
    db_session.add(host)
    db_session.flush()
    finding = Finding(project_id=test_project.id, title="Default creds", severity="high",
                      status="confirmed", source="execution")
    db_session.add(finding)
    db_session.flush()
    db_session.add(FindingHost(finding_id=finding.id, host_id=host.id, host_status="open"))
    cited = EvidenceRecord(project_id=test_project.id, host_id=host.id, finding_id=finding.id,
                           tool="hydra", command="hydra -l admin", outcome="finding",
                           summary="admin/admin works")
    loose = EvidenceRecord(project_id=test_project.id, host_id=host.id, tool="curl",
                           command="curl -I", outcome="finding", summary="no HSTS")
    db_session.add_all([cited, loose])
    db_session.commit()

    gen = _gen(db_session, test_project.id, test_user.id)
    rec = gen._build_host_export_record(host, gen._build_export_context([host]), {})
    detail = rec["canonical_findings"][0]["source_detail"]
    assert (detail["kind"], detail["tool"], detail["command"], detail["findings_summary"]) == (
        "execution", "hydra", "hydra -l admin", "admin/admin works",
    )
    promoted = {x["evidence_id"]: x["promoted"] for x in rec["execution_findings"]}
    assert promoted == {cited.id: True, loose.id: False}
    assert "exec_result_id" not in rec["canonical_findings"][0]


def test_test_findings_csv_names_tool_and_label_and_neutralizes_formulas(
    db_session, test_project, test_user,
):
    """``execution_findings.csv`` carries what an AGENT wrote (tool, command,
    summary) and a person's label, so every cell goes through the formula
    guard.  Replaces the execution-report CSV cases that went with
    ``ExportService`` in v2.442.0."""
    import csv
    import io

    host = models.Host(project_id=test_project.id, ip_address="10.55.0.7", state="up",
                       hostname="=HYPERLINK(\"http://attacker.tld\")")
    db_session.add(host)
    db_session.flush()
    test = HostTest(
        project_id=test_project.id, host_id=host.id, tool="curl", description="d", rationale="r",
        priority="high", status="done", label="@label", source="agent",
        request_key="csv-1", request_hash="2" * 64,
    )
    db_session.add(test)
    db_session.flush()
    db_session.add(EvidenceRecord(
        project_id=test_project.id, host_id=host.id, host_test_id=test.id, tool="=tool()",
        command="+attacker_payload(1)", outcome="finding", summary="-malicious_thing()",
    ))
    db_session.commit()

    gen = _gen(db_session, test_project.id, test_user.id)
    rec = gen._build_host_export_record(host, gen._build_export_context([host]), {})
    rows = list(csv.reader(io.StringIO(gen._generate_execution_findings_csv([rec]))))
    assert rows[0] == ["IP Address", "Hostname", "Tool", "Label", "Promoted", "Command", "Summary"]
    assert rows[1][0] == "10.55.0.7"
    assert rows[1][1].startswith("'=")
    assert rows[1][2].startswith("'=")
    assert rows[1][3].startswith("'@")
    assert rows[1][4] == "no"
    assert rows[1][5].startswith("'+")
    assert rows[1][6].startswith("'-")


def test_a_row_judged_through_its_issue_is_not_untriaged(db_session, test_project, test_user):
    """Review 2026-09-23 R7: the exports subtracted only ``Finding.vuln_id``
    (host A's row), so host B's row of the same promoted issue read
    "Untriaged" here while every other surface called it judged."""
    a = models.Host(project_id=test_project.id, ip_address="10.56.0.1", state="up")
    b = models.Host(project_id=test_project.id, ip_address="10.56.0.2", state="up")
    scan = models.Scan(project_id=test_project.id, filename="n.nessus")
    db_session.add_all([a, b, scan])
    db_session.flush()
    rows = {}
    for host in (a, b):
        rows[host.ip_address] = Vulnerability(
            host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.NESSUS,
            severity=VulnerabilitySeverity.MEDIUM, title="SMB Signing not required",
        )
        db_session.add(rows[host.ip_address])
    db_session.flush()
    key = rows["10.56.0.1"].issue_key
    assert key and key == rows["10.56.0.2"].issue_key
    finding = Finding(project_id=test_project.id, title="SMB signing", severity="medium", status="confirmed",
                      source="scanner", vuln_id=rows["10.56.0.1"].id, dedup_key=key)
    db_session.add(finding)
    db_session.flush()
    for host in (a, b):
        db_session.add(FindingHost(finding_id=finding.id, host_id=host.id, host_status="open"))
    db_session.commit()

    gen = _gen(db_session, test_project.id, test_user.id)
    ctx = gen._build_export_context([a, b])
    assert gen._build_host_export_record(b, ctx, {})["untriaged_vulnerabilities"] == []
    assert gen._inventory_finding_counts([b.id])[b.id]["promoted_vuln_ids"] == {rows["10.56.0.2"].id}


def test_inmemory_cap_is_wired_and_not_above_the_streamed_cap():
    """The in-memory cap must be a real, applied limit — not dead config.

    This previously asserted ``INMEMORY < MAX``, which held trivially (2000 vs
    50000) while nothing actually passed the in-memory value as a cap: when the
    JSON/bundle formats moved onto the report worker they took the full
    ``MAX_REPORT_HOSTS`` instead, so the setting an operator could tune did
    nothing.  The meaningful invariants now are that it is bounded by the
    streamed cap and that the worker genuinely applies it.
    """
    assert ReportGenerator.MAX_INMEMORY_REPORT_HOSTS <= ReportGenerator.MAX_REPORT_HOSTS

    import inspect
    from app.services.report_job_service import ReportJobService

    src = inspect.getsource(ReportJobService)
    assert "MAX_INMEMORY_REPORT_HOSTS" in src, (
        "the report worker must cap in-memory formats with "
        "MAX_INMEMORY_REPORT_HOSTS, or the setting is dead config again"
    )


def test_report_metadata_reports_the_cap_that_actually_applied(
    db_session, test_project, test_user
):
    """host_cap in the export metadata must reflect the cap used for THIS run.

    Both metadata blocks used to hard-code a constant, so the JSON export
    advertised host_cap=2000 while 50000 had applied — telling a consumer the
    wrong thing about where truncation could have happened.
    """
    gen = ReportGenerator(db_session, test_user, project_id=test_project.id)
    gen.get_hosts_for_report({}, cap=7)
    assert gen.applied_host_cap == 7


def test_html_report_renders_dossier_and_cross_links(db_session, test_project, test_user):
    """The HTML report renders a host dossier anchored #host-{id} and a findings
    index whose rows are anchored #finding-{id} and link to the host."""
    host = models.Host(project_id=test_project.id, ip_address="10.55.0.9", state="up")
    db_session.add(host)
    db_session.flush()
    finding = Finding(project_id=test_project.id, title="Weak TLS", severity="medium",
                      status="open", source="manual")
    db_session.add(finding)
    db_session.flush()
    db_session.add(FindingHost(finding_id=finding.id, host_id=host.id, host_status="open"))
    db_session.commit()

    gen = _gen(db_session, test_project.id, test_user.id)
    html = gen.generate_html_report([host], filters={})
    assert f'id="host-{host.id}"' in html
    assert f'id="finding-{finding.id}"' in html
    # The index links the finding to the host dossier, and the dossier links back.
    assert f'href="#host-{host.id}"' in html
    assert f'href="#finding-{finding.id}"' in html
    assert 'host-dossiers' in html


def test_dossier_scope_has_three_states(db_session, test_project, test_user):
    """v2.328.0 — a host in no declared subnet that an in-scope name currently
    resolves to is 'via_name': not in_scope (name scope never confers subnet
    scope), not out_of_scope either.  Same derivation the coverage page uses."""
    from datetime import datetime, timezone
    from app.services import dns_name_service as svc

    scope = models.Scope(project_id=test_project.id, name="default")
    db_session.add(scope)
    db_session.flush()
    db_session.add(models.Subnet(scope_id=scope.id, cidr="10.77.0.0/24"))
    db_session.flush()
    svc.upsert_scope_domains(db_session, scope, [("portal.example.com", False, None)])

    in_subnet = models.Host(project_id=test_project.id, ip_address="10.77.0.5", state="up")
    via_name = models.Host(project_id=test_project.id, ip_address="203.0.113.20", state="up")
    outside = models.Host(project_id=test_project.id, ip_address="203.0.113.99", state="up")
    db_session.add_all([in_subnet, via_name, outside])
    db_session.flush()
    scan = models.Scan(project_id=test_project.id, filename="dnsx.jsonl")
    db_session.add(scan)
    db_session.flush()
    svc.record_observation(
        db_session, project_id=test_project.id, name="portal.example.com",
        record_type="A", value="203.0.113.20", scan_id=scan.id,
        observed_at=datetime.now(timezone.utc),
    )
    from app.services.subnet_correlation import SubnetCorrelationService
    SubnetCorrelationService(db_session).correlate_all_hosts_to_subnets(project_id=test_project.id)
    db_session.commit()

    from app.services.report_generator import _scope_label
    gen = _gen(db_session, test_project.id, test_user.id)
    hosts = [in_subnet, via_name, outside]
    ctx = gen._build_export_context(hosts)
    recs = {h.ip_address: gen._build_host_export_record(h, ctx, {})["scope"] for h in hosts}

    assert recs["10.77.0.5"]["status"] == "in_scope"
    assert recs["10.77.0.5"]["in_scope"] and not recs["10.77.0.5"]["via_name"]
    assert recs["203.0.113.20"]["status"] == "via_name"
    assert recs["203.0.113.20"]["via_name"] and not recs["203.0.113.20"]["in_scope"]
    assert recs["203.0.113.20"]["out_of_scope"] is False
    assert recs["203.0.113.99"]["status"] == "out_of_scope"
    assert recs["203.0.113.99"]["out_of_scope"] is True
    assert [_scope_label(recs[ip]) for ip in ("10.77.0.5", "203.0.113.20", "203.0.113.99")] == [
        "in-scope", "in-scope via name", "out-of-scope",
    ]
