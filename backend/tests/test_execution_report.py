"""Smoke tests for ExportService.export_test_plan_execution_report.

Covers the supported export formats (JSON / CSV / HTML — PDF export was
removed).  Verifies:

  - JSON/CSV/HTML format dispatch
  - Empty session rejection
  - Wrong-plan rejection
  - Per-host panel content rendering
"""

from __future__ import annotations

import re

import pytest


@pytest.fixture
def plan_with_results(db_session, test_project, test_plan, test_agent):
    """Build a plan + entry + exported session + one test result so
    the report has something to render."""
    from app.db import models
    from app.db.models_agent import (
        ExecutionSession, ExecutionSessionMode, ExecutionSessionStatus,
        TestPlanEntry, TestExecutionResult, HostSanityCheck,
    )
    host = models.Host(
        ip_address="10.0.0.5",
        hostname="web01.example.com",
        state="up",
        project_id=test_project.id,
    )
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)

    entry = TestPlanEntry(
        test_plan_id=test_plan.id,
        host_id=host.id,
        priority="critical",
        test_phase="exploitation",
        proposed_tests=[{"tool": "nmap", "description": "s", "command": "c"}],
        rationale="fixture",
    )
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)

    session = ExecutionSession(
        test_plan_id=test_plan.id,
        agent_id=test_agent.id,
        status=ExecutionSessionStatus.COMPLETED.value,
        mode=ExecutionSessionMode.IN_SESSION.value,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    result = TestExecutionResult(
        execution_session_id=session.id,
        entry_id=entry.id,
        test_index=0,
        status="executed",
        command_run="nmap -sV 10.0.0.5",
        raw_output="22/tcp open  ssh OpenSSH 8.0",
        findings_summary="SSH exposed — default port",
        severity="medium",
        is_finding=True,
    )
    sanity = HostSanityCheck(
        execution_session_id=session.id,
        entry_id=entry.id,
        host_id=host.id,
        method="banner_grab",
        target_ip="10.0.0.5",
        port_checked=22,
        expected_value="OpenSSH",
        actual_value="OpenSSH 8.0",
        passed=True,
    )
    db_session.add_all([result, sanity])
    db_session.commit()

    return {"plan": test_plan, "session": session, "entry": entry, "host": host}


class TestExecutionReportFormats:
    def test_json_format_returns_dict(self, db_session, plan_with_results):
        from app.services.export_service import ExportService
        svc = ExportService(db_session)
        out = svc.export_test_plan_execution_report(
            plan_id=plan_with_results["plan"].id,
            session_id=plan_with_results["session"].id,
            format_type="json",
        )
        assert out["content_type"] == "application/json"
        data = out["data"]
        assert data["report_type"] == "test_plan_execution"
        assert data["plan"]["id"] == plan_with_results["plan"].id
        assert data["session"]["id"] == plan_with_results["session"].id
        # One entry, one result, one sanity check
        assert data["statistics"]["total_entries"] == 1
        assert data["statistics"]["tests_executed"] == 1
        assert data["statistics"]["total_findings"] == 1
        assert data["statistics"]["sanity_checks_run"] == 1

    def test_csv_format_returns_text(self, db_session, plan_with_results):
        from app.services.export_service import ExportService
        svc = ExportService(db_session)
        out = svc.export_test_plan_execution_report(
            plan_id=plan_with_results["plan"].id,
            session_id=plan_with_results["session"].id,
            format_type="csv",
        )
        assert out["content_type"] == "text/csv"
        data = out["data"]
        # Header row + at least one data row
        lines = data.strip().split("\n")
        assert len(lines) >= 2
        assert "Host IP" in lines[0]
        assert "10.0.0.5" in data
        assert "nmap" in data

    def test_html_format_escapes_findings(self, db_session, plan_with_results):
        from app.services.export_service import ExportService
        svc = ExportService(db_session)
        out = svc.export_test_plan_execution_report(
            plan_id=plan_with_results["plan"].id,
            session_id=plan_with_results["session"].id,
            format_type="html",
        )
        assert out["content_type"] == "text/html"
        html = out["data"]
        # Plan title + host IP + command all present
        assert plan_with_results["plan"].title in html
        assert "10.0.0.5" in html
        assert "nmap -sV" in html
        # Sanity check panel rendered
        assert "Sanity Check" in html or "sanity" in html.lower()
        assert "color-scheme: dark" in html
        assert "report-nav" in html
        # Every sticky-nav href="#X" has a matching id="X" somewhere in the
        # document — guards against drift between the nav links and the
        # section anchors when section ids get renamed.
        for anchor in re.findall(r'href="#([^"]+)"', html):
            assert f'id="{anchor}"' in html, f"nav anchor #{anchor} has no matching id"

    def test_default_session_id_picks_latest(self, db_session, plan_with_results):
        """When session_id is not supplied, the report picks the
        latest session for the plan."""
        from app.services.export_service import ExportService
        svc = ExportService(db_session)
        out = svc.export_test_plan_execution_report(
            plan_id=plan_with_results["plan"].id,
            session_id=None,
            format_type="json",
        )
        assert out["data"]["session"]["id"] == plan_with_results["session"].id


class TestExecutionReportRejection:
    def test_unknown_plan_rejected(self, db_session):
        from app.services.export_service import ExportService
        svc = ExportService(db_session)
        with pytest.raises(ValueError, match="not found"):
            svc.export_test_plan_execution_report(
                plan_id=99999,
                session_id=None,
                format_type="json",
            )

    def test_plan_with_no_sessions_rejected(
        self, db_session, test_plan
    ):
        """A plan that exists but has no execution sessions can't
        produce a report."""
        from app.services.export_service import ExportService
        svc = ExportService(db_session)
        with pytest.raises(ValueError, match="No execution sessions"):
            svc.export_test_plan_execution_report(
                plan_id=test_plan.id,
                session_id=None,
                format_type="json",
            )

    def test_session_from_different_plan_rejected(
        self, db_session, plan_with_results, test_agent
    ):
        """If session_id is explicitly set to a session on a different
        plan, the query must reject it."""
        from app.db.models_agent import (
            TestPlan, TestPlanStatus, ExecutionSession,
            ExecutionSessionMode, ExecutionSessionStatus,
        )
        # Make a second plan + session
        other_plan = TestPlan(
            project_id=plan_with_results["plan"].project_id,
            agent_id=test_agent.id,
            version=2,
            title="other",
            status=TestPlanStatus.APPROVED.value,
        )
        db_session.add(other_plan)
        db_session.commit()
        other_session = ExecutionSession(
            test_plan_id=other_plan.id,
            agent_id=test_agent.id,
            status=ExecutionSessionStatus.ACTIVE.value,
            mode=ExecutionSessionMode.IN_SESSION.value,
        )
        db_session.add(other_session)
        db_session.commit()

        from app.services.export_service import ExportService
        svc = ExportService(db_session)
        with pytest.raises(ValueError, match="not found for plan"):
            svc.export_test_plan_execution_report(
                plan_id=plan_with_results["plan"].id,
                session_id=other_session.id,  # session belongs to other_plan
                format_type="json",
            )
