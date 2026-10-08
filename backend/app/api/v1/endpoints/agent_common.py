"""
Agent API — shared helpers.

Non-route helper functions used by more than one agent endpoint module.
Must not import from the endpoint modules (agent_browse / agent_recon /
agent_assist…) to avoid circular imports.
"""

import ipaddress
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models, models_auth
from app.db.models_agent import AgentSession
# ``parse_port_list`` lives with the filter assembly both doors use; the name
# stays importable from here.
from app.services.host_query import parse_port_list  # noqa: F401
from app.services.vulnerability_service import VulnerabilityService


def load_agent_session(db: Session, request: Request) -> AgentSession:
    """The unified ``AgentSession`` the caller's key belongs to.

    v2.337.0 — every ``/agent/*`` handler resolves its session from here
    (``get_current_agent`` stashed the id after authenticating).  Since
    v2.442.0 a session has no phases: test plans and execution runs are gone,
    and what an agent proposes or records (host tests, evidence) carries the
    session id itself.

    For the session ROW only (its purpose, attribution, timestamps): the
    project is ``request.state.agent_project_id`` and the operator
    ``request.state.key_operator_id``.  The auth chain read this row with the
    key, so ``db.get`` answers from the session without a statement.
    """
    session_id = getattr(request.state, "agent_session_id", None)
    if session_id is None:
        raise HTTPException(status_code=403, detail="No agent session bound to this key")
    session = db.get(AgentSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found")
    return session


def load_operator(db: Session, request: Request) -> Optional[models_auth.User]:
    """The user row of the operator the key acts for
    (``request.state.key_operator_id``), for the reads that judge something
    as that person (the DSL's ``follow:`` / ``assigned:``, the workbench)."""
    return db.get(models_auth.User, request.state.key_operator_id)


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
    "No ranges or names (a value that is not a number is a 422). "
    "Given together with services=, ONE open port must meet both, as on "
    "the Hosts page."
)
SERVICES_PARAM_HELP = (
    "Comma-separated service names, matched on the service the scanner "
    "identified on an OPEN port, on any port number — the Hosts page's "
    "services filter and the DSL's service:. A port found open without a "
    "service name (masscan) does not match; ask ports= for standard ports. "
    "The name is the scanner's own: SMB is usually 'microsoft-ds' (or ask "
    "ports=445), not 'smb'. A comma list is ANY of them."
)
SEARCH_PARAM_HELP = (
    "Free text, the Hosts page's search box: address, host name, OS name or "
    "family, or — on any of the host's ports — a port number, a service name "
    "or a product."
)
SEVERITY_FLAGS_HELP = (
    "has_critical_vulns / has_high_vulns together mean a critical OR a high "
    "scanner observation, as the Hosts page's severity chips do; for both on "
    "one host use q=has:critical has:high."
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


#: The values ``Host.state`` takes — the DSL's ``state:`` enum.
HOST_STATES = ("up", "down", "unknown")
STATE_PARAM_HELP = "Host state: up, down or unknown. Any other value is a 422."
SUBNETS_PARAM_HELP = (
    "Comma-separated CIDR blocks (or single addresses); a host matches inside "
    "ANY of them. A value that is not a network or an address is a 422."
)


def check_host_filters(
    *, state: Optional[str] = None, ports: Optional[str] = None,
    services: Optional[str] = None, subnets: Optional[str] = None,
) -> None:
    """Refuse a discrete host filter the shared builder would not understand.

    The builder matches ``state`` exactly and turns a ``subnets`` value that
    is not a network into an address-prefix match, so a mistyped value reads
    as an ordinary (usually empty) result.  An agent counts with these, so
    each is a 422 naming the value, as is a list that names nothing — dropped,
    it would answer with every host in the project."""
    for name, value in (("ports", ports), ("services", services), ("subnets", subnets)):
        if value and value.strip() and not [v for v in value.split(",") if v.strip()]:
            raise HTTPException(
                status_code=422,
                detail=f"{name} was given but names nothing: {value!r}. Omit it, or pass a comma-separated list.",
            )
    if state is not None and state.strip() and state not in HOST_STATES:
        raise unknown_value_error("state", state, HOST_STATES)
    bad = []
    for item in (subnets or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ipaddress.ip_network(item, strict=False)
        except ValueError:
            bad.append(item)
    if bad:
        raise HTTPException(
            status_code=422,
            detail=(
                f"subnets must be comma-separated CIDR blocks or addresses; not understood: {bad}. "
                "For an address fragment use q= (q=ip:10.0.5)."
            ),
        )


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
