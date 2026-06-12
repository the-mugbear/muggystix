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

import ipaddress
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.db import models
from app.db.models import FollowStatus, HostFollow, HostSubnetMapping, Scan, Scope, Site, Subnet
from app.db.models_findings import Finding, FindingHost

_UNASSIGNED = "__unassigned__"

# Findings still demanding work (mirrors the host badge + read service).
_ACTIVE_FINDING_STATUSES = ("open", "confirmed", "retest")
# Severity weights for the exposure raw score — transparent, not hidden.
_SEVERITY_WEIGHT = {"critical": 10, "high": 5, "medium": 2, "low": 1, "info": 0}
# Site criticality (tier 1 = most critical … 4) scales exposure so a tier-1
# site's findings outrank a tier-4 site's equal findings.  tier-3 (×1.0) is
# the neutral default for unrated/auto-created sites.
_TIER_WEIGHT = {1: 2.0, 2: 1.5, 3: 1.0, 4: 0.5}
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


def _resolve_host_sites(db: Session, project_id: int) -> Dict[int, str]:
    """Map each host in the project to ONE site — the site of its most-specific
    (longest-prefix) matching subnet that carries a site.  Most-specific wins so
    a host inside both 10.0.0.0/8 (no site) and 10.1.2.0/24 ("DC-East") resolves
    to DC-East and is never double-counted across sites."""
    subnet_rows = (
        db.query(Subnet.id, Subnet.cidr, Subnet.site)
        .join(Scope, Subnet.scope_id == Scope.id)
        .filter(Scope.project_id == project_id, Subnet.site.isnot(None), Subnet.site != "")
        .all()
    )
    if not subnet_rows:
        return {}
    subnet_site: Dict[int, tuple] = {}
    for sid, cidr, site in subnet_rows:
        try:
            prefixlen = ipaddress.ip_network(cidr, strict=False).prefixlen
        except ValueError:
            prefixlen = 0
        subnet_site[sid] = (prefixlen, site)

    host_best: Dict[int, tuple] = {}  # host_id -> (prefixlen, site)
    mappings = (
        db.query(HostSubnetMapping.host_id, HostSubnetMapping.subnet_id)
        .join(models.Host, HostSubnetMapping.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            HostSubnetMapping.subnet_id.in_(list(subnet_site.keys())),
        )
        .all()
    )
    for host_id, subnet_id in mappings:
        prefixlen, site = subnet_site[subnet_id]
        cur = host_best.get(host_id)
        if cur is None or prefixlen > cur[0]:
            host_best[host_id] = (prefixlen, site)
    return {hid: v[1] for hid, v in host_best.items()}


def compute_site_attention(db: Session, project_id: int) -> Dict[str, Any]:
    """Per-site decomposition of the attention model — the same exposure +
    neglect components grouped by site (group-key = site instead of project).

    Returns ``adopted=False`` when no subnet carries a site, so the UI can
    suppress the per-site view (and the "Unassigned gap" nag) until the
    project actually organises subnets into sites.
    """
    host_to_site = _resolve_host_sites(db, project_id)
    # Site metadata catalog, loaded up front so configured sites with ZERO
    # discovered hosts still surface — a site with an expected host count but
    # none found is the strongest coverage failure, not something to hide.
    # selectinload(owner) so the per-site owner_name read below doesn't N+1 a
    # query per owned site.
    sites_by_name = {
        s.name: s for s in db.query(Site)
        .options(selectinload(Site.owner))
        .filter(Site.project_id == project_id).all()
    }
    if not host_to_site and not sites_by_name:
        return {"adopted": False, "sites": []}

    # Every project host, bucketed by resolved site (unassigned otherwise).
    all_host_ids = [h for (h,) in db.query(models.Host.id).filter(models.Host.project_id == project_id).all()]
    site_host_ids: Dict[str, List[int]] = defaultdict(list)
    for hid in all_host_ids:
        site_host_ids[host_to_site.get(hid, _UNASSIGNED)].append(hid)
    # Seed every configured site (even zero-host ones) so a site with expected
    # hosts but none discovered surfaces with its full coverage gap.
    for name in sites_by_name:
        site_host_ids.setdefault(name, [])

    # Reviewed hosts (any user) → per-site review gap.
    reviewed = {
        h for (h,) in (
            db.query(HostFollow.host_id)
            .join(models.Host, HostFollow.host_id == models.Host.id)
            .filter(models.Host.project_id == project_id, HostFollow.status == FollowStatus.REVIEWED.value)
            .distinct()
        )
    }

    # Active findings → the distinct sites each touches (a multi-host finding
    # counts once per site it affects, never twice within one site).
    rows = (
        db.query(FindingHost.host_id, Finding.id, Finding.severity, Finding.owner_id)
        .join(Finding, FindingHost.finding_id == Finding.id)
        .filter(Finding.project_id == project_id, Finding.status.in_(_ACTIVE_FINDING_STATUSES))
        .all()
    )
    finding_sites: Dict[int, set] = defaultdict(set)
    finding_meta: Dict[int, tuple] = {}
    # Finding-HOST incidences per site: each (finding, host) link is one
    # incidence, so a single finding spanning 100 hosts in a site contributes
    # 100 — the honest input for a per-host exposure density (distinct-finding
    # counts divided by hosts understate estate-wide findings).
    site_incidences: Dict[str, int] = defaultdict(int)
    for host_id, fid, sev, owner in rows:
        site = host_to_site.get(host_id, _UNASSIGNED)
        finding_sites[fid].add(site)
        finding_meta[fid] = (sev, owner)
        site_incidences[site] += 1

    site_sev: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    )
    site_unowned: Dict[str, int] = defaultdict(int)
    for fid, sites in finding_sites.items():
        sev, owner = finding_meta[fid]
        for site in sites:
            if sev in site_sev[site]:
                site_sev[site][sev] += 1
            if owner is None:
                site_unowned[site] += 1

    out: List[Dict[str, Any]] = []
    for site, host_ids in site_host_ids.items():
        by_sev = dict(site_sev.get(site, {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}))
        active = sum(by_sev.values())
        unowned = site_unowned.get(site, 0)
        unreviewed = sum(1 for hid in host_ids if hid not in reviewed)
        is_unassigned = site == _UNASSIGNED

        site_obj = None if is_unassigned else sites_by_name.get(site)
        tier = site_obj.criticality_tier if site_obj else 3
        expected = site_obj.expected_host_count if site_obj else None
        owner_name = None
        if site_obj and site_obj.owner:
            owner_name = site_obj.owner.full_name or site_obj.owner.username
        # Coverage gap = expected − discovered (only when expected is set) —
        # a neglect signal: a site with far fewer hosts than expected is
        # under-scanned, not "clean".
        coverage_gap = max(0, expected - len(host_ids)) if expected is not None else None

        exposure_raw = sum(_SEVERITY_WEIGHT.get(s, 0) * c for s, c in by_sev.items())
        weighted = round(exposure_raw * _TIER_WEIGHT.get(tier, 1.0), 1)

        if is_unassigned:
            action = {"kind": "assign", "text": f"{len(host_ids)} host{'' if len(host_ids) == 1 else 's'} not assigned to a site."}
        elif unowned > 0:
            action = {"kind": "triage", "text": f"{unowned} active finding{'' if unowned == 1 else 's'} unowned."}
        elif by_sev["critical"] > 0:
            action = {"kind": "remediate", "text": f"{by_sev['critical']} critical open."}
        elif coverage_gap:
            action = {"kind": "scan", "text": f"Coverage gap — {coverage_gap} of {expected} expected hosts not yet found."}
        elif unreviewed > 0:
            action = {"kind": "review", "text": f"{unreviewed} host{'' if unreviewed == 1 else 's'} not reviewed."}
        else:
            action = {"kind": "ok", "text": "No outstanding attention items."}

        out.append({
            "site": None if is_unassigned else site,
            "site_id": site_obj.id if site_obj else None,
            "unassigned": is_unassigned,
            "criticality_tier": None if is_unassigned else tier,
            "owner_name": owner_name,
            "host_count": len(host_ids),
            "expected_host_count": expected,
            "coverage_gap": coverage_gap,
            "exposure": {
                "raw_score": exposure_raw,
                "weighted_score": weighted,
                "active_findings": active,
                "finding_host_incidences": site_incidences.get(site, 0),
                "by_severity": by_sev,
            },
            "neglect": {"unowned_active_findings": unowned, "unreviewed_hosts": unreviewed},
            "recommended_action": action,
        })

    # Worst-first: tier-weighted exposure desc, then neglect (unowned +
    # unreviewed + coverage gap) desc; Unassigned sinks unless it has exposure.
    out.sort(key=lambda s: (
        -s["exposure"]["weighted_score"],
        -(s["neglect"]["unowned_active_findings"] + s["neglect"]["unreviewed_hosts"] + (s["coverage_gap"] or 0)),
    ))
    return {"adopted": True, "sites": out}
