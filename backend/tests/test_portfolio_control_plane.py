"""Tests for the P4 portfolio control-plane signals.

``GET /portfolio/dashboard`` gained per-project attention_reasons +
workflow/finding counts and portfolio-wide attention rollups.  The
``client`` fixture is a global admin, so every non-archived project is
visible.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db import models
from app.db.models_agent import TestPlan, ExecutionSession
from app.db.models_project import ProjectMembership
from app.db.models_auth import User, UserRole
from app.db.models_vulnerability import (
    Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
)

PORTFOLIO_URL = "/api/v1/portfolio/dashboard"
_UID = [6000]


def _card_for(body, pid):
    return next(c for c in body["projects"] if c["id"] == pid)


def _make_user(db_session, username):
    _UID[0] += 1
    u = User(
        id=_UID[0], username=username, email=f"{username}@example.com",
        full_name=username.title(), hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER, is_active=True, is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(u)
    db_session.flush()
    return u


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


def test_no_admin_governance_flag(client, db_session, test_project):
    """SOC-P3 — a project with no admin MEMBER is flagged (global-admin
    caller doesn't count; has_admin is about ProjectMembership role=admin)."""
    body = client.get(PORTFOLIO_URL).json()
    card = _card_for(body, test_project.id)
    assert card["has_admin"] is False
    assert "no_admin" in card["attention_reasons"]
    assert body["summary"]["projects_without_admin"] >= 1


def test_admin_member_clears_no_admin(client, db_session, test_project):
    admin_user = _make_user(db_session, "proj-admin")
    db_session.add(ProjectMembership(
        project_id=test_project.id, user_id=admin_user.id, role="admin",
    ))
    db_session.flush()

    card = _card_for(client.get(PORTFOLIO_URL).json(), test_project.id)
    assert card["has_admin"] is True
    assert "no_admin" not in card["attention_reasons"]
    assert admin_user.full_name in card["admins"]


def test_team_roster_with_workload(client, db_session, test_project, test_agent):
    """SOC-P4 — /portfolio/team lists members with per-project roles +
    workload (assigned open tasks + hosts In Review)."""
    from app.db.models_agent import TestPlanEntry
    from app.db.models import HostFollow, FollowStatus

    u = _make_user(db_session, "soc-analyst")
    db_session.add(ProjectMembership(
        project_id=test_project.id, user_id=u.id, role="analyst",
    ))
    plan = TestPlan(
        project_id=test_project.id, agent_id=test_agent.id, version=1,
        title="t", status="approved",
    )
    host = models.Host(project_id=test_project.id, ip_address="10.7.7.7", state="up")
    db_session.add_all([plan, host])
    db_session.flush()
    db_session.add(TestPlanEntry(
        test_plan_id=plan.id, host_id=host.id, priority="high",
        test_phase="enumeration", proposed_tests=["x"], rationale="r",
        status="proposed", assigned_to_id=u.id,
    ))
    db_session.add(HostFollow(user_id=u.id, host_id=host.id, status=FollowStatus.IN_REVIEW))
    db_session.flush()

    r = client.get("/api/v1/portfolio/team")
    assert r.status_code == 200, r.text
    member = next(m for m in r.json()["members"] if m["user_id"] == u.id)
    assert member["project_count"] >= 1
    assert any(pr["project_id"] == test_project.id and pr["role"] == "analyst"
               for pr in member["projects"])
    assert member["open_tasks"] >= 1
    assert member["hosts_in_review"] >= 1


def test_portfolio_flags_no_data_project(client, db_session, test_project):
    # test_project has no hosts/scans → no_data attention reason.
    r = client.get(PORTFOLIO_URL)
    body = r.json()
    card = _card_for(body, test_project.id)
    assert "no_data" in card["attention_reasons"]
    assert card["host_count"] == 0
    assert body["summary"]["projects_no_data"] >= 1
