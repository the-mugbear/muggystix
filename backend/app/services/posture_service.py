"""Security Posture — the manager-facing roll-up.

This service does NOT collect new data.  It *composes* aggregates that already
exist — the attention model (exposure + neglect, per project and per site), the
systemic-insight model (estate blind spots), the finding spine (disposition +
ownership), and the agent workflow (pending approvals, blocked runs) — into a
single snapshot a manager can read in ten seconds:

  * a deterministic posture LABEL (no synthetic 0–100 score) + its top reasons,
  * headline measures (confirmed exposure, review coverage, ownership, systemic),
  * a ranked decision list (management priorities),
  * the site/systemic/disposition breakdowns the page visualises.

Design rules carried from the attention post-mortem: every number is explainable
(a visible component, never an opaque score), and absence of findings is NOT
health — an unscanned/unreviewed estate must read as "needs assessment", which
the coverage + untriaged signals capture.

The label and the priorities list share ONE signal pass, so the headline and the
list can never disagree.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_findings import Finding
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.db.models_agent import (
    TestPlan, TestPlanStatus, TestPlanEntry, ExecutionSession,
    ExecutionSessionStatus, TestExecutionResult, TestExecutionStatus,
)
from app.services.attention_service import (
    compute_project_attention, compute_site_attention,
)
from app.services.systemic_insight_service import compute_systemic_insights

_ACTIVE = ("open", "confirmed", "retest")
_SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")
# Review-coverage floor below which the estate reads as under-assessed.
_REVIEW_FLOOR = 0.5
# Site criticality tiers that escalate a critical finding to "action required".
_HOT_TIERS = (1, 2)


# --------------------------------------------------------------------------
# Signals — the shared spine of the label, the reasons, and the priorities.
# A signal is one thing worth a manager's attention; its `tier` drives the
# label, its `score` drives ordering, and it carries the priority-row payload.
# --------------------------------------------------------------------------
def _signal(tier: str, score: float, reason: str, *, kind: str, title: str,
            blast_radius: str, action: str, severity: str,
            owner: Optional[str], link: Optional[str]) -> Dict[str, Any]:
    return {
        "tier": tier,            # "action" | "assess"
        "score": round(float(score), 1),
        "reason": reason,        # one-line, for the headline's top-3 reasons
        "priority": {
            "kind": kind, "title": title, "blast_radius": blast_radius,
            "action": action, "severity": severity, "owner": owner, "link": link,
        },
    }


def _gather_signals(
    db: Session, project_id: int, *, project_att: Dict[str, Any],
    site_att: Dict[str, Any], systemic: Dict[str, Any],
    unowned_by_sev: Dict[str, int], review_pct: Optional[float],
    pending_approvals: int, blocked_sessions: int, detected_vulns: int,
) -> List[Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []
    by_sev = project_att["exposure"]["by_severity"]
    active = project_att["exposure"]["active_findings"]
    total_hosts = project_att["neglect"]["total_hosts"]

    # 0. Onboarding — no recon yet. Absence of findings is NOT health: a
    # never-scanned estate must read as "needs assessment", not "all clear".
    if total_hosts == 0:
        signals.append(_signal(
            "assess", 35, "No hosts discovered yet — estate not assessed",
            kind="onboard", title="No recon data yet",
            blast_radius="Whole project unassessed", action="Upload a scan or start a recon run",
            severity="medium", owner=None, link="/scans",
        ))

    # A. Unowned critical/high findings → assign an owner (action).
    uo_ch = unowned_by_sev.get("critical", 0) + unowned_by_sev.get("high", 0)
    uo_total = project_att["neglect"]["unowned_active_findings"]
    if uo_ch > 0:
        signals.append(_signal(
            "action", 90 + uo_ch,
            f"{uo_ch} unowned critical/high finding{'' if uo_ch == 1 else 's'}",
            kind="ownership", title=f"{uo_ch} critical/high finding{'' if uo_ch == 1 else 's'} unowned",
            blast_radius=f"{uo_ch} of {active} active findings", action="Assign an owner to triage",
            severity="critical" if unowned_by_sev.get("critical", 0) else "high",
            owner=None, link="/findings",
        ))
    elif uo_total > 0:
        signals.append(_signal(
            "assess", 40 + uo_total,
            f"{uo_total} active finding{'' if uo_total == 1 else 's'} unowned",
            kind="ownership", title=f"{uo_total} active finding{'' if uo_total == 1 else 's'} unowned",
            blast_radius=f"{uo_total} of {active} active findings", action="Assign owners",
            severity="medium", owner=None, link="/findings",
        ))

    # B. Estate blind spots — one systemic weakness replicated estate-wide (action).
    for b in (systemic.get("blind_spots") or [])[:4]:
        pct = round((b.get("host_fraction") or 0) * 100)
        signals.append(_signal(
            "action", 80 + (b.get("systemic_score") or 0) / 10,
            f"{b['label']} across {pct}% of hosts (estate blind spot)",
            kind="systemic", title=b["label"],
            blast_radius=f"{b['affected_hosts']} hosts ({pct}%) · {b.get('subnet_spread', 0)} subnets · {b.get('site_spread', 0)} sites",
            action=b.get("recommended_action") or "Remediate estate-wide",
            severity=(b.get("severity") or "high"), owner=None, link="/insights/systemic",
        ))

    # C. Critical findings on a tier-1/2 site (action).
    if site_att.get("adopted"):
        for s in site_att.get("sites", []):
            tier = s.get("criticality_tier")
            crit = s["exposure"]["by_severity"].get("critical", 0)
            if tier in _HOT_TIERS and crit > 0:
                signals.append(_signal(
                    "action", 70 + s["exposure"]["weighted_score"],
                    f"{s['site']}: {crit} critical finding{'' if crit == 1 else 's'} (tier {tier})",
                    kind="site", title=f"{s['site']} — {crit} critical",
                    blast_radius=f"Tier {tier} site · {s['host_count']} hosts · {s['exposure']['active_findings']} active findings",
                    action=s["recommended_action"]["text"],
                    severity="critical", owner=None, link="/insights",
                ))

    # D. Blocked agent runs — failed/abandoned execution sessions (action).
    if blocked_sessions > 0:
        signals.append(_signal(
            "action", 60 + blocked_sessions,
            f"{blocked_sessions} agent session{'' if blocked_sessions == 1 else 's'} failed or abandoned",
            kind="blocked", title=f"{blocked_sessions} execution session{'' if blocked_sessions == 1 else 's'} blocked",
            blast_radius="Validation work stalled", action="Review and resume or close",
            severity="high", owner=None, link="/executions",
        ))

    # E. Under-reviewed estate (assess).
    if review_pct is not None and review_pct < _REVIEW_FLOOR:
        pct = round(review_pct * 100)
        signals.append(_signal(
            "assess", 50 * (1 - review_pct),
            f"Only {pct}% of hosts reviewed",
            kind="coverage", title=f"Review coverage at {pct}%",
            blast_radius=f"{project_att['neglect']['unreviewed_hosts']} of {project_att['neglect']['total_hosts']} hosts unreviewed",
            action="Work the review queue", severity="medium", owner=None, link="/hosts",
        ))

    # F. Untriaged: scan data present, scanner vulns exist, but nothing curated (assess).
    if active == 0 and detected_vulns > 0:
        signals.append(_signal(
            "assess", 55,
            f"{detected_vulns} scanner finding{'' if detected_vulns == 1 else 's'} present, none triaged",
            kind="triage", title="Scan data not yet triaged",
            blast_radius=f"{detected_vulns} scanner-detected vulnerabilities", action="Promote real issues to findings",
            severity="medium", owner=None, link="/findings",
        ))

    # G. Pending plan approvals (assess).
    if pending_approvals > 0:
        signals.append(_signal(
            "assess", 45 + pending_approvals,
            f"{pending_approvals} test plan{'' if pending_approvals == 1 else 's'} awaiting approval",
            kind="approval", title=f"{pending_approvals} test plan{'' if pending_approvals == 1 else 's'} pending approval",
            blast_radius="Validation blocked on a decision", action="Review and approve or reject",
            severity="low", owner=None, link="/test-plans",
        ))

    # H. Coverage gaps — configured sites under their expected host count (assess).
    if site_att.get("adopted"):
        for s in site_att.get("sites", []):
            gap = s.get("coverage_gap") or 0
            if gap > 0:
                signals.append(_signal(
                    "assess", 30 + min(gap, 30),
                    f"{s['site']}: {gap} of {s['expected_host_count']} expected hosts not found",
                    kind="coverage", title=f"{s['site']} — coverage gap",
                    blast_radius=f"{gap} expected hosts undiscovered", action="Extend recon to close the gap",
                    severity="medium", owner=None, link="/insights",
                ))

    signals.sort(key=lambda s: -s["score"])
    return signals


def compute_posture(db: Session, project_id: int) -> Dict[str, Any]:
    project_att = compute_project_attention(db, project_id)
    site_att = compute_site_attention(db, project_id)
    systemic = compute_systemic_insights(db, project_id)

    # Unowned active findings, by severity (the attention model only gives the
    # total) — needed to escalate unowned crit/high to "action required".
    unowned_by_sev = {
        sev: int(c) for sev, c in (
            db.query(Finding.severity, func.count(Finding.id))
            .filter(
                Finding.project_id == project_id,
                Finding.status.in_(_ACTIVE),
                Finding.owner_id.is_(None),
            )
            .group_by(Finding.severity)
            .all()
        )
    }

    total_hosts = project_att["neglect"]["total_hosts"]
    reviewed_hosts = max(0, total_hosts - project_att["neglect"]["unreviewed_hosts"])
    review_pct = (reviewed_hosts / total_hosts) if total_hosts else None

    # Ownership of active findings.
    active = project_att["exposure"]["active_findings"]
    unowned_total = project_att["neglect"]["unowned_active_findings"]
    owned = max(0, active - unowned_total)

    # Detected exposure — scanner vulnerabilities, kept SEPARATE from curated
    # findings (a scanner hit is not an analyst conclusion). Exclude info/unknown.
    detected_vulns = (
        db.query(func.count(Vulnerability.id))
        .join(models.Host, Vulnerability.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            Vulnerability.severity.notin_([VulnerabilitySeverity.INFO, VulnerabilitySeverity.UNKNOWN]),
        )
        .scalar()
        or 0
    )

    # Validated hosts — distinct hosts with an executed test result (raw count;
    # there is no persisted "test-worthy" denominator, so this is NOT a ratio).
    validated_hosts = (
        db.query(func.count(func.distinct(TestPlanEntry.host_id)))
        .join(TestExecutionResult, TestExecutionResult.entry_id == TestPlanEntry.id)
        .join(models.Host, TestPlanEntry.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            TestExecutionResult.status == TestExecutionStatus.EXECUTED.value,
        )
        .scalar()
        or 0
    )

    # Decisions awaiting a human.
    pending_approvals = (
        db.query(func.count(TestPlan.id))
        .filter(TestPlan.project_id == project_id, TestPlan.status == TestPlanStatus.PROPOSED.value)
        .scalar()
        or 0
    )
    # Execution sessions scope to a project through their test plan.
    blocked_sessions = (
        db.query(func.count(ExecutionSession.id))
        .join(TestPlan, ExecutionSession.test_plan_id == TestPlan.id)
        .filter(
            TestPlan.project_id == project_id,
            ExecutionSession.status.in_([
                ExecutionSessionStatus.FAILED.value, ExecutionSessionStatus.ABANDONED.value,
            ]),
        )
        .scalar()
        or 0
    )

    signals = _gather_signals(
        db, project_id, project_att=project_att, site_att=site_att, systemic=systemic,
        unowned_by_sev=unowned_by_sev, review_pct=review_pct,
        pending_approvals=int(pending_approvals), blocked_sessions=int(blocked_sessions),
        detected_vulns=int(detected_vulns),
    )

    # Deterministic label from the same signal pass.
    if any(s["tier"] == "action" for s in signals):
        label = "action_required"
    elif any(s["tier"] == "assess" for s in signals):
        label = "needs_assessment"
    else:
        label = "no_urgent_signals"
    reasons = [{"text": s["reason"], "severity": s["priority"]["severity"]} for s in signals[:3]]

    # Disposition: status × severity, scanner-source kept countable but separate.
    disp_rows = (
        db.query(Finding.status, Finding.severity, func.count(Finding.id))
        .filter(Finding.project_id == project_id)
        .group_by(Finding.status, Finding.severity)
        .all()
    )
    by_status: Dict[str, int] = {}
    by_status_severity: Dict[str, Dict[str, int]] = {}
    for status, sev, c in disp_rows:
        by_status[status] = by_status.get(status, 0) + int(c)
        by_status_severity.setdefault(status, {})[sev] = int(c)
    scanner_active = (
        db.query(func.count(Finding.id))
        .filter(
            Finding.project_id == project_id,
            Finding.status.in_(_ACTIVE), Finding.source == "scanner",
        )
        .scalar()
        or 0
    )

    return {
        "label": label,
        "reasons": reasons,
        "headline": {
            "confirmed_exposure": {
                "active_findings": active,
                "by_severity": project_att["exposure"]["by_severity"],
            },
            "review_coverage": {
                "reviewed": reviewed_hosts, "total": total_hosts,
                "pct": round(review_pct * 100) if review_pct is not None else None,
                "validated_hosts": int(validated_hosts),
            },
            "ownership": {
                "owned": owned, "unowned": unowned_total, "total": active,
                "pct": round(owned / active * 100) if active else None,
            },
            "systemic": {
                "blind_spot_count": systemic.get("estate", {}).get("blind_spot_count", 0),
                "condition_count": len(systemic.get("conditions", [])),
            },
            "detected_exposure": {"vuln_count": int(detected_vulns)},
        },
        "priorities": [s["priority"] | {"score": s["score"]} for s in signals[:8]],
        "decisions": {
            "pending_approvals": int(pending_approvals),
            "blocked_sessions": int(blocked_sessions),
        },
        "sites": {"adopted": site_att.get("adopted", False), "items": site_att.get("sites", [])},
        "systemic": {
            "adopted": systemic.get("adopted", False),
            "estate": systemic.get("estate", {}),
            "conditions": systemic.get("conditions", []),
            "blind_spots": systemic.get("blind_spots", []),
        },
        "disposition": {
            "by_status": by_status,
            "by_status_severity": by_status_severity,
            "active_total": active,
            "scanner_active": int(scanner_active),
            "analyst_active": max(0, active - int(scanner_active)),
        },
    }
