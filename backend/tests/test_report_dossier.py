"""Host-centric dossier report — the correlated per-host record.

The comprehensive report is host-first: one consolidated dossier per host that
pulls together canonical findings (with their resolved source row + per-host
status), untriaged scanner observations, execution findings, tester summaries,
and notes.  These tests pin the assembly (``_build_export_context`` +
``_build_host_export_record``) and the per-format caps.
"""

from unittest.mock import MagicMock

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.db.models_findings import Finding, FindingHost
# Aliased so pytest doesn't try to collect the ``Test*``-named ORM classes.
from app.db.models_agent import (
    TestPlan as PlanModel,
    TestPlanEntry as PlanEntryModel,
    ExecutionSession,
    TestExecutionResult as ExecResultModel,
)
from app.api.v1.endpoints.reports import ReportGenerator


def _gen(db, project_id, user_id):
    return ReportGenerator(db=db, current_user=MagicMock(id=user_id), project_id=project_id)


def test_dossier_record_correlates_every_source(db_session, test_project, test_user):
    """One host with a promoted scanner finding, an untriaged scanner vuln, a
    note, a tester summary, and an execution finding — the record carries all of
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
        note_type="finding", status="open",
    ))

    plan = PlanModel(project_id=test_project.id, title="Web plan", status="approved",
                    created_by_user_id=test_user.id)
    db_session.add(plan)
    db_session.flush()
    entry = PlanEntryModel(
        test_plan_id=plan.id, host_id=host.id, priority="high", test_phase="enumeration",
        proposed_tests=[], rationale="x", status="completed", findings="Found admin panel",
    )
    db_session.add(entry)
    db_session.flush()

    sess = ExecutionSession(test_plan_id=plan.id, started_by_id=test_user.id,
                            status="active", mode="in_session")
    db_session.add(sess)
    db_session.flush()
    db_session.add(ExecResultModel(
        execution_session_id=sess.id, entry_id=entry.id, test_index=0,
        is_finding=True, severity="high", findings_summary="popped a shell",
        command_run="nmap -sV",
    ))
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
    assert rec["execution_findings"][0]["findings_summary"] == "popped a shell"
    assert rec["execution_findings"][0]["promoted"] is False
    assert rec["analyst_context"]["notes"], "host note should be present"

    summary = rec["dossier_summary"]
    assert summary["active_findings"] == 1
    assert summary["untriaged_vulns"] == 1
    assert summary["execution_findings"] == 1
    assert summary["tester_summaries"] == 1
    # Finding-severity and vuln-severity tallies are kept separate.
    assert summary["findings_by_severity"] == {"high": 1}
    assert summary["vulns_by_severity"]["high"] == 1


def test_inmemory_cap_is_lower_than_streamed_cap():
    """PDF/JSON/bundle build in memory and must cap lower than the streamed HTML."""
    assert ReportGenerator.MAX_INMEMORY_REPORT_HOSTS < ReportGenerator.MAX_REPORT_HOSTS


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
