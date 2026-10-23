"""Review 2026-10-23, inventory reads (build E).

* H-INV1 — "active finding" has ONE definition
  (``engagement_metrics_service.finding_is_active``): Posture's active counts
  and the Findings list's ``status=active`` group (both doors) agree, and a
  finding judged a false positive on every endpoint is in neither.
"""
from __future__ import annotations

import pytest

from app.db import models
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import ProjectMembership, ProjectRole
from app.services import agent_session_service as sessions
from app.services.posture_service import compute_posture


def _host(db, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _finding(db, project_id, title, *, severity, status="open", source="manual", endpoints=()):
    f = Finding(project_id=project_id, title=title, severity=severity, status=status, source=source)
    db.add(f)
    db.flush()
    for host, state in endpoints:
        db.add(FindingHost(finding_id=f.id, host_id=host.id, host_status=state))
    db.flush()
    return f


@pytest.fixture()
def agent_headers(db_session, test_project):
    user = User(
        id=93101, username="operator-93101", email="operator-93101@example.com",
        hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=UserRole.MEMBER, is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(ProjectMembership(
        project_id=test_project.id, user_id=user.id, role=ProjectRole.ANALYST.value,
    ))
    db_session.commit()
    agent = sessions.resolve_project_agent(db_session, project_id=test_project.id, user=user)
    session = sessions.create_agent_session(
        db_session, project_id=test_project.id, agent_id=agent.id, started_by_id=user.id,
    )
    raw = sessions.mint_session_key(db_session, agent=agent, session=session)
    db_session.commit()
    return {"X-API-Key": raw}


def _seed_findings(db, project_id):
    a = _host(db, project_id, "10.231.0.1")
    b = _host(db, project_id, "10.231.0.2")
    # Active and a result.
    _finding(db, project_id, "crit real", severity="critical", endpoints=[(a, "open")])
    _finding(db, project_id, "high half fp", severity="high", status="confirmed",
             endpoints=[(a, "false_positive"), (b, "open")])
    _finding(db, project_id, "medium no endpoint", severity="medium")
    _finding(db, project_id, "high scanner", severity="high", source="scanner", endpoints=[(b, "open")])
    # Open by status, but a false positive on EVERY endpoint: not a result.
    _finding(db, project_id, "crit all fp", severity="critical",
             endpoints=[(a, "false_positive"), (b, "false_positive")])
    _finding(db, project_id, "high scanner all fp", severity="high", source="scanner",
             endpoints=[(a, "false_positive")])
    # Not active by status.
    _finding(db, project_id, "low closed", severity="low", status="remediated", endpoints=[(a, "remediated")])
    db.commit()


def test_posture_active_counts_are_the_active_lists_totals(client, db_session, test_project, agent_headers):
    _seed_findings(db_session, test_project.id)
    out = compute_posture(db_session, test_project.id, use_cache=False)
    exposure = out["headline"]["active_exposure"]
    base = f"/api/v1/projects/{test_project.id}/findings"

    whole = client.get(base, params={"status": "active"}).json()
    assert exposure["active_findings"] == whole["total"] == 4
    titles = {f["title"] for f in whole["items"]}
    assert "crit all fp" not in titles and "high scanner all fp" not in titles

    for sev in ("critical", "high", "medium", "low", "info"):
        page = client.get(base, params={"status": "active", "severity": sev}).json()
        assert exposure["by_severity"][sev] == page["total"], sev
        agent = client.get(
            "/api/v1/agent/assist/findings", params={"status": "active", "severity": sev},
            headers=agent_headers,
        ).json()
        assert agent["total"] == page["total"], sev

    d = out["disposition"]
    scanner = client.get(base, params={"status": "active", "source": "scanner"}).json()
    assert d["scanner_active"] == scanner["total"] == 1
    assert d["scanner_active"] + d["non_scanner_active"] == d["active_total"] == exposure["active_findings"]
    # Ownership is the same selection (every seeded finding is unowned).
    assert out["headline"]["ownership"]["unowned"] == whole["total"]
