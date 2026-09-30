"""
Agent API — shared helpers.

Non-route helper functions used by more than one agent endpoint module.
Must not import from the endpoint modules (agent_browse / agent_test_plans
/ agent_execution / agent_recon) to avoid circular imports.
"""

from typing import Dict, List, Optional

from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.db.models_agent import AgentSession, TestPlan, TestPlanEntry
from app.services.test_plan_service import TestPlanService

from app.api.v1.endpoints.agent_schemas import PlanResponse


def load_agent_session(db: Session, request: Request) -> AgentSession:
    """The unified ``AgentSession`` the caller's key belongs to.

    v2.337.0 — every ``/agent/*`` handler resolves its session from here
    (``get_current_agent`` stashed the id after authenticating).  The phase a
    call is about (a recon run, an execution run) is resolved from the session
    by the ``agent_session_service.resolve_*_phase`` helpers, replacing the
    per-key scope binding the deleted workflow guards used to carry.
    """
    session_id = getattr(request.state, "agent_session_id", None)
    if session_id is None:
        raise HTTPException(status_code=403, detail="No agent session bound to this key")
    session = db.query(AgentSession).filter(AgentSession.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found")
    return session


# ---------------------------------------------------------------------------
# Shared helpers for host filtering and enrichment
# ---------------------------------------------------------------------------

# Scope membership (host ids in a scope) is
# ``scope_targets_service.scope_host_ids`` since v2.433.1.


#: The discrete host filters' meaning, shown to agents in every route that
#: takes them (OpenAPI and the MCP schemas carry it).
PORTS_PARAM_HELP = (
    "Comma-separated port numbers; a host matches with any of them OPEN. "
    "No ranges or names (a value that is not a number is a 422)."
)
SERVICES_PARAM_HELP = (
    "Comma-separated service names, matched on the service the scanner "
    "identified on an OPEN port, on any port number — the Hosts page's "
    "services filter and the DSL's service:. A port found open without a "
    "service name (masscan) does not match; ask ports= for standard ports."
)


def parse_port_list(ports: str) -> List[int]:
    """``"22,80,443"`` → ``[22, 80, 443]``.  A value that is not a port
    number (a range, ``445/tcp``, a name) is a 422 naming it — skipping it
    silently dropped the whole filter when nothing else was left."""
    out: List[int] = []
    bad: List[str] = []
    for raw in ports.split(","):
        value = raw.strip()
        if not value:
            continue
        if value.isdigit() and int(value) <= 65535:  # the DSL's port: range, 0-65535
            out.append(int(value))
        else:
            bad.append(value)
    if bad:
        raise HTTPException(
            status_code=422,
            detail=(
                f"ports must be comma-separated port numbers (0-65535); not understood: {bad}. "
                "There are no ranges: list each port (ports=5900,5901,5902). For a port in "
                "another state use q= (q=port:445@any); for a service by name use services= "
                "(matched on the service the scanner identified, on any port)."
            ),
        )
    return out

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

    The port/service dimensions match an *open* port, as the Hosts page and
    the DSL do (``port:`` / ``service:`` are open-by-default since v2.403.0).

    v2.440.0 (diag 4: an agent's VNC count disagreed with the Hosts page) —
    ``services`` is the PAGE'S definition: a service name matched on an open
    port (``port_match_subquery(services=…)``, the same predicate as the
    Hosts page's ``services=`` and the DSL's ``service:``).  It used to
    expand names to standard port numbers, so ``services=vnc`` meant "5900–5905
    open, whatever runs there" — counting masscan-only ports and missing VNC
    elsewhere — and a name missing from that map dropped the filter, returning
    every host.  Standard-port questions use ``ports``.  And a ``ports`` value
    that is not a port number is refused (422), never silently ignored: an
    ignored filter answers "how many?" with the whole project.
    """
    from app.services import host_query_predicates as P

    if state:
        q = q.filter(P.state_predicate([state]))

    if ports:
        port_nums = parse_port_list(ports)
        if port_nums:
            q = q.filter(models.Host.id.in_(
                P.port_match_subquery(db, ports=port_nums, require_open=True)
            ))

    if services:
        names = [s.strip() for s in services.split(",") if s.strip()]
        if names:
            q = q.filter(models.Host.id.in_(
                P.port_match_subquery(db, services=names, require_open=True)
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
