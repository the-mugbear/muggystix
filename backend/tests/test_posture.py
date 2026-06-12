"""Security Posture composition — the parts most likely to regress: the
deterministic label, the headline measures, and that the label/reasons/
priorities share one signal pass (so they can't disagree).
"""
from __future__ import annotations

from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.services.posture_service import compute_posture


def _host(db, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _finding(db, project_id, *, severity, status="open", owner_id=None, host=None):
    f = Finding(project_id=project_id, title=f"{severity} finding", severity=severity,
                status=status, source="manual", owner_id=owner_id)
    db.add(f)
    db.flush()
    if host is not None:
        db.add(FindingHost(finding_id=f.id, host_id=host.id, host_status="open"))
        db.flush()
    return f


def test_empty_project_reads_needs_assessment(db_session, test_project):
    """Absence of findings is NOT health — a never-scanned estate must not read
    as 'no urgent signals'."""
    out = compute_posture(db_session, test_project.id)
    assert out["label"] == "needs_assessment"
    assert any(p["kind"] == "onboard" for p in out["priorities"])
    assert out["headline"]["active_exposure"]["active_findings"] == 0
    # Evidence currency is always present (even with no scans).
    assert "scan_staleness_days" in out["evidence"]
    # Systemic carries an adoption flag so the UI can distinguish
    # "can't assess" from "assessed, nothing found".
    assert "adopted" in out["headline"]["systemic"]


def test_unowned_critical_is_action_required(db_session, test_project):
    """An unowned critical active finding escalates to action_required, and the
    top reason names it. The headline severity breakdown reflects it."""
    host = _host(db_session, test_project.id, "10.0.0.10")
    _finding(db_session, test_project.id, severity="critical", owner_id=None, host=host)
    db_session.commit()

    out = compute_posture(db_session, test_project.id)
    assert out["label"] == "action_required"
    assert out["headline"]["active_exposure"]["by_severity"]["critical"] == 1
    assert out["headline"]["ownership"]["unowned"] == 1
    # The label, reasons, and priorities share one pass — the ownership signal
    # is both the top reason and the top priority.
    assert any("unowned" in r["text"].lower() for r in out["reasons"])
    assert out["priorities"][0]["kind"] == "ownership"


def test_owned_reviewed_finding_no_urgent_signals(db_session, test_project, test_user):
    """A single owned, non-critical finding on a reviewed host, with no systemic
    spread, produces no action/assess signals."""
    host = _host(db_session, test_project.id, "10.0.0.20")
    db_session.add(models.HostFollow(
        host_id=host.id, user_id=test_user.id, status=models.FollowStatus.REVIEWED.value,
    ))
    _finding(db_session, test_project.id, severity="low", owner_id=test_user.id, host=host)
    db_session.commit()

    out = compute_posture(db_session, test_project.id)
    assert out["label"] == "no_urgent_signals"
    assert out["headline"]["review_coverage"]["pct"] == 100
    assert out["headline"]["ownership"]["unowned"] == 0


def test_blocked_runs_count_only_latest_session_per_plan(db_session, test_project):
    """A superseded failed session (a newer run was started) must NOT leave a
    permanent 'blocked' flag — only the latest session per plan counts."""
    from app.db.models_agent import TestPlan, ExecutionSession

    plan = TestPlan(project_id=test_project.id, title="plan")
    db_session.add(plan)
    db_session.flush()
    # Older session failed; a newer session is active → the plan is progressing.
    db_session.add(ExecutionSession(test_plan_id=plan.id, status="failed"))
    db_session.flush()
    db_session.add(ExecutionSession(test_plan_id=plan.id, status="active"))
    db_session.commit()

    out = compute_posture(db_session, test_project.id)
    assert out["decisions"]["blocked_sessions"] == 0
    assert not any(p["kind"] == "blocked" for p in out["priorities"])

    # Now the LATEST session fails → it counts.
    db_session.add(ExecutionSession(test_plan_id=plan.id, status="failed"))
    db_session.commit()
    out2 = compute_posture(db_session, test_project.id)
    assert out2["decisions"]["blocked_sessions"] == 1


def test_posture_response_contract(db_session, test_project):
    """Pin the response shape the frontend TypeScript depends on — renames
    (confirmed_exposure→active_exposure, analyst_active→non_scanner_active) have
    drifted from the manual TS interface before; this fails loudly on the next."""
    out = compute_posture(db_session, test_project.id)
    assert set(out) >= {
        "label", "reasons", "headline", "priorities", "decisions",
        "sites", "systemic", "disposition", "evidence",
    }
    assert set(out["headline"]) >= {
        "active_exposure", "review_coverage", "ownership", "systemic", "detected_exposure",
    }
    assert "adopted" in out["headline"]["systemic"]
    assert set(out["disposition"]) >= {"scanner_active", "non_scanner_active", "by_status"}
    assert set(out["decisions"]) >= {"pending_approvals", "blocked_sessions"}
    assert set(out["evidence"]) >= {"scan_count", "scan_staleness_days"}
    for p in out["priorities"]:
        assert set(p) >= {"kind", "title", "blast_radius", "action", "severity", "owner", "link"}
