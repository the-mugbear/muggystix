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


def test_review_states_add_up_and_coverage_does_not_set_health(
    client, db_session, test_project, test_user,
):
    """v2.389.0 — the card said "64 of 416 tested · 387 unreviewed · 7%":
    three definitions that did not add up.  And any project under 50%
    reviewed was "warning" and "needs attention", so every new project was."""
    hosts = [models.Host(project_id=test_project.id, ip_address=f"10.7.9.{i}", state="up") for i in range(1, 6)]
    db_session.add_all(hosts)
    db_session.flush()
    db_session.add_all([
        models.HostFollow(host_id=hosts[0].id, user_id=test_user.id, status=models.FollowStatus.REVIEWED),
        models.HostFollow(host_id=hosts[1].id, user_id=test_user.id, status=models.FollowStatus.IN_REVIEW),
    ])
    db_session.flush()

    body = client.get(PORTFOLIO_URL).json()
    card = _card_for(body, test_project.id)
    assert (card["host_count"], card["hosts_reviewed"], card["hosts_in_review"]) == (5, 1, 1)
    # 20% reviewed, nothing critical or high found: not a warning, no reason.
    assert card["health"] == "healthy"
    assert "unreviewed" not in card["attention_reasons"]
    assert body["summary"]["total_reviewed"] >= 1 and body["summary"]["total_in_review"] >= 1


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

    # An untriaged critical scanner row: not a finding yet, but still a
    # critical signal on the project.
    assert "critical_unjudged" in card["attention_reasons"]
    assert "critical_findings" not in card["attention_reasons"]
    assert "pending_review" in card["attention_reasons"]
    assert card["pending_plan_reviews"] == 1
    assert card["unjudged_observations"]["critical"] == 1
    assert card["findings"]["critical"] == 0
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


def test_no_admin_governance_moved_to_oversight(client, db_session, test_project):
    """v2.377.0 — "no project admin" is an administrator's question, answered
    on the admin-only Oversight page (tests/test_oversight.py); Portfolio,
    which every member sees, no longer carries it."""
    body = client.get(PORTFOLIO_URL).json()
    card = _card_for(body, test_project.id)
    assert "no_admin" not in card["attention_reasons"]
    assert "has_admin" not in card and "admins" not in card
    assert "projects_without_admin" not in body["summary"]


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


def test_only_an_active_project_is_flagged_quiet(client, db_session, test_project):
    """v2.374.1 — a project runs for weeks and is then kept for posterity.
    "No import for a fortnight" is a question about a project still marked
    active (finished? mark it completed); on a completed one it was permanent
    noise that also put it under "requires attention"."""
    from datetime import timedelta

    old = datetime.now(timezone.utc) - timedelta(days=40)
    scan = models.Scan(project_id=test_project.id, filename="old.xml", tool_name="nmap")
    scan.created_at = old
    db_session.add(scan)
    db_session.add(models.Host(project_id=test_project.id, ip_address="10.2.0.1", state="up"))
    db_session.commit()

    card = _card_for(client.get(PORTFOLIO_URL).json(), test_project.id)
    assert card["is_stale"] is True and "stale" in card["attention_reasons"]

    test_project.status = "completed"
    db_session.commit()
    body = client.get(PORTFOLIO_URL).json()
    card = _card_for(body, test_project.id)
    assert card["is_stale"] is False
    assert "stale" not in card["attention_reasons"]
    assert card["health"] != "stale"
    # The date itself stays: it is provenance, not a verdict.
    assert card["days_since_last_scan"] >= 40
