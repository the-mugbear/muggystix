"""Project "needs help" attention model (site-metrics arc, P1).

Answers "which scope needs the most help?" along TWO axes that must not be
collapsed (the lesson from the deleted risk-scoring system):

  * Exposure — how bad is what we've found (severity-weighted active findings).
  * Neglect  — how under-served the scope is (stale/absent scans, untriaged
               backlog, unreviewed hosts).

The single most important property: **absence of findings ≠ healthy**. A
project with zero findings because it was never scanned must surface as
"needs help" (onboard/scan it), which the neglect axis + the recommended
action capture — not as the greenest scoreboard entry.

Design constraints (deliberate, from the risk-scoring post-mortem):
  * Explainable — every number is a visible component, never an opaque score.
  * Populated   — runs on real Finding/scan/host data that already exists.
  * Actionable  — maps the dominant component to a recommended next action.

Project-level for now; the same component computation is intended to become
group-key-parameterized (site / label) in a later phase — only the GROUP BY
key changes, not the model.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.db.models import FollowStatus, HostFollow, Scan
from app.db.models_findings import Finding

# Findings still demanding work (mirrors the host badge + read service).
_ACTIVE_FINDING_STATUSES = ("open", "confirmed", "retest")
# Severity weights for the exposure raw score — transparent, not hidden.
_SEVERITY_WEIGHT = {"critical": 10, "high": 5, "medium": 2, "low": 1, "info": 0}
# Days since the last scan before a scope reads as "stale".
_STALE_DAYS = 14


def compute_project_attention(db: Session, project_id: int) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)

    # --- Exposure: severity-weighted active findings -----------------------
    sev_rows = dict(
        db.query(Finding.severity, func.count(Finding.id))
        .filter(Finding.project_id == project_id, Finding.status.in_(_ACTIVE_FINDING_STATUSES))
        .group_by(Finding.severity)
        .all()
    )
    by_severity = {s: int(sev_rows.get(s, 0)) for s in ("critical", "high", "medium", "low", "info")}
    active_findings = sum(by_severity.values())
    exposure_raw = sum(_SEVERITY_WEIGHT.get(s, 0) * c for s, c in by_severity.items())

    # --- Neglect: staleness, untriaged backlog, unreviewed hosts -----------
    unowned = (
        db.query(func.count(Finding.id))
        .filter(
            Finding.project_id == project_id,
            Finding.status.in_(_ACTIVE_FINDING_STATUSES),
            Finding.owner_id.is_(None),
        )
        .scalar()
        or 0
    )
    total_hosts = (
        db.query(func.count(models.Host.id))
        .filter(models.Host.project_id == project_id)
        .scalar()
        or 0
    )
    reviewed_hosts = (
        db.query(func.count(func.distinct(HostFollow.host_id)))
        .join(models.Host, HostFollow.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            HostFollow.status == FollowStatus.REVIEWED.value,
        )
        .scalar()
        or 0
    )
    unreviewed_hosts = max(0, total_hosts - reviewed_hosts)

    scan_count = (
        db.query(func.count(Scan.id)).filter(Scan.project_id == project_id).scalar() or 0
    )
    latest_scan = (
        db.query(func.max(Scan.created_at)).filter(Scan.project_id == project_id).scalar()
    )
    staleness_days: Optional[int] = None
    if latest_scan is not None:
        # SQLite returns naive datetimes; Postgres tz-aware. Normalize.
        ls = latest_scan if latest_scan.tzinfo else latest_scan.replace(tzinfo=timezone.utc)
        staleness_days = max(0, (now - ls).days)

    # --- Recommended action: dominant component → next step ----------------
    # Order matters: a never-scanned scope is the loudest signal, then
    # staleness, then untriaged backlog, then open criticals, then review gap.
    if scan_count == 0:
        action = {"kind": "onboard", "text": "No recon yet — upload a scan or start a recon run."}
    elif staleness_days is not None and staleness_days >= _STALE_DAYS:
        action = {"kind": "scan", "text": f"Stale — last scan was {staleness_days} days ago."}
    elif unowned > 0:
        action = {"kind": "triage", "text": f"{unowned} active finding{'' if unowned == 1 else 's'} unowned — assign an owner."}
    elif by_severity["critical"] > 0:
        n = by_severity["critical"]
        action = {"kind": "remediate", "text": f"{n} critical finding{'' if n == 1 else 's'} open."}
    elif unreviewed_hosts > 0 and total_hosts > 0:
        action = {"kind": "review", "text": f"{unreviewed_hosts} host{'' if unreviewed_hosts == 1 else 's'} not yet reviewed."}
    else:
        action = {"kind": "ok", "text": "No outstanding attention items."}

    return {
        "project_id": project_id,
        "exposure": {
            "raw_score": exposure_raw,  # transparent weighted sum, not opaque
            "active_findings": active_findings,
            "by_severity": by_severity,
        },
        "neglect": {
            "scan_count": int(scan_count),
            "scan_staleness_days": staleness_days,
            "unowned_active_findings": int(unowned),
            "unreviewed_hosts": int(unreviewed_hosts),
            "total_hosts": int(total_hosts),
        },
        "recommended_action": action,
    }
