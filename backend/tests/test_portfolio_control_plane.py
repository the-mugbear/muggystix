"""Tests for the P4 portfolio control-plane signals.

``GET /portfolio/dashboard`` gained per-project attention_reasons +
workflow/finding counts and portfolio-wide attention rollups.  The
``client`` fixture is a global admin, so every non-archived project is
visible.
"""
from __future__ import annotations

from app.db import models
from app.db.models_agent import TestPlan, ExecutionSession
from app.db.models_vulnerability import (
    Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
)

PORTFOLIO_URL = "/api/v1/portfolio/dashboard"


def _card_for(body, pid):
    return next(c for c in body["projects"] if c["id"] == pid)


def test_portfolio_surfaces_critical_and_pending_review(
    client, db_session, test_project,
):
    scan = models.Scan(project_id=test_project.id, filename="s.xml")
    host = models.Host(project_id=test_project.id, ip_address="10.7.0.1", state="up")
    db_session.add_all([scan, host])
    db_session.flush()
    db_session.add(Vulnerability(
        title="rce", severity=VulnerabilitySeverity.CRITICAL,
        source=VulnerabilitySource.MANUAL, host_id=host.id, scan_id=scan.id,
    ))
    db_session.add(TestPlan(
        project_id=test_project.id, version=1, title="draft", status="proposed",
    ))
    db_session.flush()

    r = client.get(PORTFOLIO_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    card = _card_for(body, test_project.id)

    assert "critical_findings" in card["attention_reasons"]
    assert "pending_review" in card["attention_reasons"]
    assert card["pending_plan_reviews"] == 1
    assert card["vuln_summary"]["critical"] == 1
    assert card["health"] == "critical"
    assert card["user_role"] == "admin"  # global admin, no membership row

    summary = body["summary"]
    assert summary["projects_with_critical"] >= 1
    assert summary["pending_approvals_total"] >= 1
    assert summary["projects_requiring_attention"] >= 1


def test_blocked_uses_latest_session_only(client, db_session, test_project, test_agent):
    """CR-A3/#6 — a paused OLD session superseded by an active newer one is
    not 'blocked'; only the latest session per plan counts."""
    plan = TestPlan(
        project_id=test_project.id, agent_id=test_agent.id, version=1,
        title="exec plan", status="approved",
    )
    db_session.add(plan)
    db_session.flush()
    # Older session paused (superseded), newer session active.
    db_session.add(ExecutionSession(
        test_plan_id=plan.id, agent_id=test_agent.id, status="paused",
    ))
    db_session.flush()
    db_session.add(ExecutionSession(
        test_plan_id=plan.id, agent_id=test_agent.id, status="active",
    ))
    db_session.flush()

    card = _card_for(client.get(PORTFOLIO_URL).json(), test_project.id)
    assert card["blocked_sessions"] == 0
    assert "blocked_session" not in card["attention_reasons"]


def test_blocked_when_latest_session_paused(client, db_session, test_project, test_agent):
    """Inverse — when the latest session itself is paused/failed, it blocks."""
    plan = TestPlan(
        project_id=test_project.id, agent_id=test_agent.id, version=1,
        title="exec plan 2", status="approved",
    )
    db_session.add(plan)
    db_session.flush()
    db_session.add(ExecutionSession(
        test_plan_id=plan.id, agent_id=test_agent.id, status="active",
    ))
    db_session.flush()
    db_session.add(ExecutionSession(
        test_plan_id=plan.id, agent_id=test_agent.id, status="failed",
    ))
    db_session.flush()

    card = _card_for(client.get(PORTFOLIO_URL).json(), test_project.id)
    assert card["blocked_sessions"] == 1
    assert "blocked_session" in card["attention_reasons"]


def test_portfolio_flags_no_data_project(client, db_session, test_project):
    # test_project has no hosts/scans → no_data attention reason.
    r = client.get(PORTFOLIO_URL)
    body = r.json()
    card = _card_for(body, test_project.id)
    assert "no_data" in card["attention_reasons"]
    assert card["host_count"] == 0
    assert body["summary"]["projects_no_data"] >= 1
