"""
Agent API — shared helpers.

Non-route helper functions used by more than one agent endpoint module.
Must not import from the endpoint modules (agent_browse / agent_test_plans
/ agent_execution / agent_recon) to avoid circular imports.
"""

from ipaddress import ip_network
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.db.models_agent import ReconSession, TestPlan, TestPlanEntry
from app.services.test_plan_service import TestPlanService

from app.api.v1.endpoints.agent_schemas import HostBrief, PlanResponse, VulnCounts

# Reuse canonical service-port mappings from the hosts endpoint
from app.api.v1.endpoints.hosts import SERVICE_PORT_MAPPINGS as _SERVICE_PORT_MAP


# ---------------------------------------------------------------------------
# Shared helpers for host filtering and enrichment
# ---------------------------------------------------------------------------

def _scoped_host_ids_subq(db: Session, scope_id: int):
    """Host IDs mapped into the given scope via subnet correlation.

    A host is in a scope iff any of its HostSubnetMapping rows point at
    a subnet that belongs to the scope.  Used to isolate data reads by
    recon-scoped API keys so the agent can't see hosts from other scopes
    in the same project.

    v2.68.0 — returns a SQLAlchemy 2.0 ``Select`` rather than the legacy
    ``Subquery``.  The previous form raised
    ``SAWarning: Coercing Subquery object into a select() for use in
    IN()`` on every call.  Callers don't change: ``Column.in_(...)``
    accepts a ``Select`` directly.  Function name kept (``_subq``
    suffix) because renaming across ~12 callers is churn without
    semantic gain — they all still use it the same way.
    """
    return (
        select(models.HostSubnetMapping.host_id)
        .join(models.Subnet, models.Subnet.id == models.HostSubnetMapping.subnet_id)
        .where(models.Subnet.scope_id == scope_id)
        .distinct()
    )


def _scoped_scan_ids_subq(db: Session, scope_id: int):
    """Scan IDs produced by IngestionJobs under any ReconSession of the scope.

    Lets recon-scoped API keys see only the scans they actually
    produced, not other scopes' scans in the same project.

    v2.68.0 — same Select-vs-Subquery refactor as
    ``_scoped_host_ids_subq``.
    """
    return (
        select(models.IngestionJob.scan_id)
        .join(ReconSession, ReconSession.id == models.IngestionJob.recon_session_id)
        .where(
            ReconSession.scope_id == scope_id,
            models.IngestionJob.scan_id.isnot(None),
        )
        .distinct()
    )


def _apply_agent_host_filters(
    q, db: Session, *,
    state: Optional[str] = None,
    ports: Optional[str] = None,
    services: Optional[str] = None,
    subnets: Optional[str] = None,
    min_severity: Optional[str] = None,
    has_critical_vulns: Optional[bool] = None,
    has_high_vulns: Optional[bool] = None,
    has_exploit_available: Optional[bool] = None,
    min_risk_score: Optional[int] = None,
    search: Optional[str] = None,
    not_in_plan_id: Optional[int] = None,
):
    """Apply optional filters to a Host query. Returns the modified query."""
    from sqlalchemy import text as sa_text

    if state:
        q = q.filter(models.Host.state == state)

    if ports:
        port_nums = [int(p.strip()) for p in ports.split(",") if p.strip().isdigit()]
        if port_nums:
            q = q.filter(
                models.Host.id.in_(
                    db.query(models.Port.host_id).filter(
                        models.Port.port_number.in_(port_nums),
                        models.Port.state == "open",
                    )
                )
            )

    if services:
        svc_ports = set()
        for svc in services.split(","):
            svc_ports.update(_SERVICE_PORT_MAP.get(svc.strip().lower(), []))
        if svc_ports:
            q = q.filter(
                models.Host.id.in_(
                    db.query(models.Port.host_id).filter(
                        models.Port.port_number.in_(list(svc_ports)),
                        models.Port.state == "open",
                    )
                )
            )

    if subnets:
        from sqlalchemy import or_
        cidr_conditions = []
        for idx, cidr in enumerate(subnets.split(",")):
            cidr = cidr.strip()
            try:
                ip_network(cidr, strict=False)
                param_name = f"cidr_{idx}"
                cidr_conditions.append(
                    sa_text(f"hosts_v2.ip_address::inet <<= :{param_name}::inet")
                    .bindparams(**{param_name: cidr})
                )
            except ValueError:
                pass
        if cidr_conditions:
            q = q.filter(or_(*cidr_conditions))

    if min_severity:
        # Severity tiers — picking "high" matches hosts with at least one
        # vulnerability of severity high OR critical (i.e. high-or-above).
        # That's the mental model most users have, and avoids the AND
        # surprise of the legacy has_critical_vulns + has_high_vulns pair
        # below.  Both filters can coexist on the same plan; they layer.
        _tiers = {
            "critical": [VulnerabilitySeverity.CRITICAL],
            "high":     [VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH],
            "medium":   [VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH, VulnerabilitySeverity.MEDIUM],
            "low":      [VulnerabilitySeverity.CRITICAL, VulnerabilitySeverity.HIGH, VulnerabilitySeverity.MEDIUM, VulnerabilitySeverity.LOW],
        }
        sevs = _tiers.get(min_severity.lower())
        if sevs:
            q = q.filter(
                models.Host.id.in_(
                    db.query(Vulnerability.host_id).filter(
                        Vulnerability.severity.in_(sevs)
                    )
                )
            )

    if has_critical_vulns:
        q = q.filter(
            models.Host.id.in_(
                db.query(Vulnerability.host_id).filter(
                    Vulnerability.severity == VulnerabilitySeverity.CRITICAL
                )
            )
        )

    if has_high_vulns:
        q = q.filter(
            models.Host.id.in_(
                db.query(Vulnerability.host_id).filter(
                    Vulnerability.severity == VulnerabilitySeverity.HIGH
                )
            )
        )

    # v2.85.0 — exploit-available filter, surfaced on the agent side now
    # that v2.83.2 actually persists Vulnerability.exploitable from the
    # Nessus parser.  Matches the user-side filter at host_query.py:371.
    # Plan-gen agents use this to bias the entry rubric toward
    # confirmed-real-world-exploitable findings instead of severity
    # alone.
    if has_exploit_available:
        q = q.filter(
            models.Host.id.in_(
                db.query(Vulnerability.host_id).filter(
                    Vulnerability.exploitable.is_(True)
                )
            )
        )

    if min_risk_score is not None:
        from app.db.models_risk import RiskAssessment
        q = q.filter(
            models.Host.id.in_(
                db.query(RiskAssessment.host_id).filter(
                    RiskAssessment.overall_score >= min_risk_score
                )
            )
        )

    if search:
        from sqlalchemy import or_
        pattern = f"%{search}%"
        q = q.filter(
            or_(
                models.Host.ip_address.ilike(pattern),
                models.Host.hostname.ilike(pattern),
                models.Host.os_name.ilike(pattern),
            )
        )

    if not_in_plan_id is not None:
        q = q.filter(
            ~models.Host.id.in_(
                db.query(TestPlanEntry.host_id).filter(
                    TestPlanEntry.test_plan_id == not_in_plan_id
                )
            )
        )

    return q


def _batch_host_enrichment(db: Session, host_ids: List[int], include_ports: bool = False):
    """Batch-compute open port counts, vuln summaries, services, and optionally
    full port details and top vulnerabilities for a list of host IDs.

    Returns (port_counts, vuln_map, svc_map, port_details_map, top_vulns_map).
    port_details_map and top_vulns_map are only populated when
    include_ports=True; otherwise they are empty dicts.
    """
    if not host_ids:
        return {}, {}, {}, {}, {}

    # Open port counts
    port_counts_raw = (
        db.query(models.Port.host_id, func.count(models.Port.id))
        .filter(models.Port.host_id.in_(host_ids), models.Port.state == "open")
        .group_by(models.Port.host_id)
        .all()
    )
    port_counts = {hid: cnt for hid, cnt in port_counts_raw}

    # Vuln counts by severity
    vuln_rows = (
        db.query(
            Vulnerability.host_id,
            Vulnerability.severity,
            func.count(Vulnerability.id),
        )
        .filter(Vulnerability.host_id.in_(host_ids))
        .group_by(Vulnerability.host_id, Vulnerability.severity)
        .all()
    )
    vuln_map: Dict[int, Dict[str, int]] = {}
    for hid, sev, cnt in vuln_rows:
        vuln_map.setdefault(hid, {})[sev.value if hasattr(sev, "value") else sev] = cnt

    # Distinct services
    svc_rows = (
        db.query(models.Port.host_id, models.Port.service_name)
        .filter(
            models.Port.host_id.in_(host_ids),
            models.Port.state == "open",
            models.Port.service_name.isnot(None),
            models.Port.service_name != "",
        )
        .distinct()
        .all()
    )
    svc_map: Dict[int, List[str]] = {}
    for hid, svc in svc_rows:
        svc_map.setdefault(hid, []).append(svc)

    # Full port details (only for context endpoint — open ports only)
    port_details: Dict[int, List] = {}
    if include_ports:
        port_rows = (
            db.query(models.Port)
            .filter(
                models.Port.host_id.in_(host_ids),
                models.Port.state == "open",
            )
            .order_by(models.Port.host_id, models.Port.port_number)
            .all()
        )
        for p in port_rows:
            port_details.setdefault(p.host_id, []).append(p)

    # Top vulnerabilities per host (critical/high, up to 5 each).
    # v2.90.4 (code review #3) — was ``.all() + Python trim``, which
    # materialised every critical/high vulnerability for up to 2000
    # hosts before truncating to 5/host.  On a Nessus-heavy project
    # (hundreds of findings × hundreds of hosts) that ballooned the
    # working set without need.  Switched to a window-function
    # subquery — ``ROW_NUMBER() OVER (PARTITION BY host_id ORDER BY
    # severity ASC, cvss_score DESC NULLS LAST, id ASC)`` — so the
    # database returns at most 5 IDs per host.  A second query
    # hydrates the ORM objects for those IDs.  Severity ordering
    # exploits the enum's lowercase string values: "critical" <
    # "high" alphabetically, so ASC puts critical first.
    top_vulns: Dict[int, List] = {}
    if include_ports and host_ids:
        ranked = (
            select(
                Vulnerability.id.label("vid"),
                func.row_number().over(
                    partition_by=Vulnerability.host_id,
                    order_by=(
                        Vulnerability.severity.asc(),
                        func.coalesce(Vulnerability.cvss_score, 0).desc(),
                        Vulnerability.id.asc(),
                    ),
                ).label("rn"),
            )
            .where(
                Vulnerability.host_id.in_(host_ids),
                Vulnerability.severity.in_([
                    VulnerabilitySeverity.CRITICAL,
                    VulnerabilitySeverity.HIGH,
                ]),
            )
            .subquery()
        )
        top_ids = [
            row.vid for row in db.execute(
                select(ranked.c.vid).where(ranked.c.rn <= 5)
            ).all()
        ]
        if top_ids:
            top_vuln_rows = (
                db.query(Vulnerability)
                .filter(Vulnerability.id.in_(top_ids))
                .all()
            )
            for v in top_vuln_rows:
                top_vulns.setdefault(v.host_id, []).append(v)

    return port_counts, vuln_map, svc_map, port_details, top_vulns


# ---------------------------------------------------------------------------
# Shared test-plan response builder
# ---------------------------------------------------------------------------

def _plan_response(plan: TestPlan, db: Session) -> PlanResponse:
    svc = TestPlanService(db)
    progress = svc.get_progress(plan.id)
    return PlanResponse(
        id=plan.id,
        version=plan.version,
        title=plan.title,
        description=plan.description,
        status=plan.status,
        entry_count=progress["total_entries"],
        completion_pct=progress["completion_pct"],
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )
