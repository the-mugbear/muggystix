"""
Agent API — shared helpers.

Non-route helper functions used by more than one agent endpoint module.
Must not import from the endpoint modules (agent_browse / agent_test_plans
/ agent_execution / agent_recon) to avoid circular imports.
"""

from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.db.models_agent import ReconSession, TestPlan, TestPlanEntry
from app.services.test_plan_service import TestPlanService

from app.api.v1.endpoints.agent_schemas import PlanResponse

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
    project_id: int,
    state: Optional[str] = None,
    ports: Optional[str] = None,
    services: Optional[str] = None,
    subnets: Optional[str] = None,
    min_severity: Optional[str] = None,
    has_critical_vulns: Optional[bool] = None,
    has_high_vulns: Optional[bool] = None,
    has_exploit_available: Optional[bool] = None,
    search: Optional[str] = None,
    not_in_plan_id: Optional[int] = None,
):
    """Apply optional filters to a Host query. Returns the modified query.

    Filter semantics live in the shared predicate library
    (``host_query_predicates``) so the agent surface and the user-side
    ``q=`` DSL / discrete-filter path can't drift.  The vuln-dimension
    predicates (severity / exploit) take ``project_id`` and scope their
    child-table subquery to it via a Host join — without that the
    ``vulnerabilities`` scan materializes matching host-ids across EVERY
    project before the outer filter trims them (the perf trap fixed
    project-wide in the host_query refactor; the agent path had the same
    unscoped subqueries until this unification).

    The port/service dimensions keep the agent's "must be an *open* port"
    semantics (the DSL ``port:`` leaf matches any state); services still
    expand through ``_SERVICE_PORT_MAP`` to port numbers rather than
    matching ``service_name`` text, which is the agent's intended model
    ("give me web hosts" = standard web ports).
    """
    from app.services import host_query_predicates as P

    if state:
        q = q.filter(P.state_predicate([state]))

    if ports:
        port_nums = [int(p.strip()) for p in ports.split(",") if p.strip().isdigit()]
        if port_nums:
            q = q.filter(models.Host.id.in_(
                P.port_match_subquery(db, ports=port_nums, require_open=True)
            ))

    if services:
        svc_ports = set()
        for svc in services.split(","):
            svc_ports.update(_SERVICE_PORT_MAP.get(svc.strip().lower(), []))
        if svc_ports:
            q = q.filter(models.Host.id.in_(
                P.port_match_subquery(db, ports=list(svc_ports), require_open=True)
            ))

    if subnets:
        pred = P.subnet_predicate([c.strip() for c in subnets.split(",") if c.strip()])
        if pred is not None:
            q = q.filter(pred)

    if min_severity:
        # Severity tiers — picking "high" matches hosts with at least one
        # vulnerability of severity high OR critical (i.e. high-or-above).
        # That's the mental model most users have, and avoids the AND
        # surprise of the has_critical_vulns + has_high_vulns pair below.
        # Both filters can coexist on the same plan; they layer.
        _tiers = {
            "critical": ["CRITICAL"],
            "high":     ["CRITICAL", "HIGH"],
            "medium":   ["CRITICAL", "HIGH", "MEDIUM"],
            "low":      ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
        }
        sevs = _tiers.get(min_severity.lower())
        if sevs:
            q = q.filter(P.severity_predicate(db, sevs, project_id))

    if has_critical_vulns:
        q = q.filter(P.severity_predicate(db, ["CRITICAL"], project_id))

    if has_high_vulns:
        q = q.filter(P.severity_predicate(db, ["HIGH"], project_id))

    # v2.85.0 — exploit-available filter, surfaced on the agent side now
    # that v2.83.2 actually persists Vulnerability.exploitable from the
    # Nessus parser.  Plan-gen agents use this to bias the entry rubric
    # toward confirmed-real-world-exploitable findings over severity alone.
    if has_exploit_available:
        q = q.filter(P.has_exploit_predicate(db, project_id))

    if search:
        from sqlalchemy import or_
        from app.services.host_query_common import escape_like
        # Escape LIKE metacharacters so a literal % / _ in the agent's search
        # term isn't treated as a wildcard (matches the user-side filters).
        # Kept inline: the agent search is ip/hostname/os_name only, narrower
        # than os_predicate's os_name-OR-os_family union.
        pattern = f"%{escape_like(search)}%"
        q = q.filter(
            or_(
                models.Host.ip_address.ilike(pattern, escape='\\'),
                models.Host.hostname.ilike(pattern, escape='\\'),
                models.Host.os_name.ilike(pattern, escape='\\'),
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
