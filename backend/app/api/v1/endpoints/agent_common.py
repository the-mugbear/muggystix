"""
Agent API — shared helpers.

Non-route helper functions used by more than one agent endpoint module.
Must not import from the endpoint modules (agent_browse / agent_recon /
agent_assist…) to avoid circular imports.
"""

from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_agent import AgentSession
from app.services.vulnerability_service import VulnerabilityService


def load_agent_session(db: Session, request: Request) -> AgentSession:
    """The unified ``AgentSession`` the caller's key belongs to.

    v2.337.0 — every ``/agent/*`` handler resolves its session from here
    (``get_current_agent`` stashed the id after authenticating).  Since
    v2.442.0 a session has no phases: test plans and execution runs are gone,
    and what an agent proposes or records (host tests, evidence) carries the
    session id itself.
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
    "Comma-separated port numbers; a host matches with ANY of them open. "
    "For hosts with ALL of them open use q instead: q=port:80 port:443. "
    "No ranges or names (a value that is not a number is a 422)."
)
SERVICES_PARAM_HELP = (
    "Comma-separated service names, matched on the service the scanner "
    "identified on an OPEN port, on any port number — the Hosts page's "
    "services filter and the DSL's service:. A port found open without a "
    "service name (masscan) does not match; ask ports= for standard ports. "
    "The name is the scanner's own: SMB is usually 'microsoft-ds' (or ask "
    "ports=445), not 'smb'. A comma list is ANY of them."
)


def require_project_host(db: Session, project_id: int, host_id: Optional[int]) -> None:
    """404 when a ``host_id`` FILTER names a host that is not in the project.

    A list filtered by a host that is not there used to answer 200 with no
    rows — "no tests / no evidence / no findings on that host" said about a
    host that does not exist here, while the host reads answered 404 for the
    same id (agent feedback #30, 2026-10-02).  Another project's host and a
    nonexistent one are the same answer.
    """
    if host_id is None:
        return
    found = (
        db.query(models.Host.id)
        .filter(models.Host.id == host_id, models.Host.project_id == project_id)
        .first()
    )
    if found is None:
        raise HTTPException(status_code=404, detail="Host not found in this project")


def unknown_value_error(name: str, value: str, allowed) -> HTTPException:
    """The 422 for a filter value that is not one of a closed set: an unknown
    value must never read as an ordinary empty result."""
    return HTTPException(
        status_code=422,
        detail=f"Unknown {name} {value!r}. Accepted: {', '.join(sorted(allowed))}.",
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

def apply_agent_host_filters(
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
                P.port_match_subquery(db, ports=port_nums, require_open=True, project_id=project_id)
            ))

    if services:
        names = [s.strip() for s in services.split(",") if s.strip()]
        if names:
            q = q.filter(models.Host.id.in_(
                P.port_match_subquery(db, services=names, require_open=True, project_id=project_id)
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


    return q


def batch_host_enrichment(
    db: Session, host_ids: List[int],
) -> Tuple[Dict[int, int], Dict[int, Dict[str, int]]]:
    """Open-port counts and scanner-row counts by severity for a page of hosts.

    Returns ``(port_counts, vuln_map)``: ``port_counts[host_id]`` is the number
    of OPEN ports; ``vuln_map[host_id]`` maps a severity name (``critical`` …
    ``info``) to its row count, and is ``{}`` for a host with none.

    The severity rollup is the Hosts page's own —
    ``VulnerabilityService.get_bulk_host_vulnerability_summaries`` — so an
    agent's host list and the page cannot disagree on a count (CLAUDE.md,
    "Agent parity with the pages": the SAME service, never a second rollup;
    pinned by ``tests/test_agent_host_enrichment.py``).

    Review 2026-10-01 B4 — this returned five results and every caller
    discarded three: a DISTINCT services query ran on every agent host list
    for nothing, and the port-detail / top-vulnerabilities branch
    (``include_ports=True``) had no caller.  Both are gone, with the function's
    own GROUP BY over ``vulnerabilities``.
    """
    if not host_ids:
        return {}, {}

    # Open port counts
    port_counts_raw = (
        db.query(models.Port.host_id, func.count(models.Port.id))
        .filter(models.Port.host_id.in_(host_ids), models.Port.state == "open")
        .group_by(models.Port.host_id)
        .all()
    )
    port_counts = {hid: cnt for hid, cnt in port_counts_raw}

    summaries = VulnerabilityService(db).get_bulk_host_vulnerability_summaries(host_ids)
    vuln_map: Dict[int, Dict[str, int]] = {
        hid: dict(summary.get("by_severity") or {}) for hid, summary in summaries.items()
    }

    return port_counts, vuln_map
