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
    assert out["headline"]["confirmed_exposure"]["active_findings"] == 0


def test_unowned_critical_is_action_required(db_session, test_project):
    """An unowned critical active finding escalates to action_required, and the
    top reason names it. The headline severity breakdown reflects it."""
    host = _host(db_session, test_project.id, "10.0.0.10")
    _finding(db_session, test_project.id, severity="critical", owner_id=None, host=host)
    db_session.commit()

    out = compute_posture(db_session, test_project.id)
    assert out["label"] == "action_required"
    assert out["headline"]["confirmed_exposure"]["by_severity"]["critical"] == 1
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
