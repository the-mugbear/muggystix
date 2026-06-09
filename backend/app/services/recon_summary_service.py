"""
Recon summary helpers — extracted from agent_recon.py in v2.27.0.

Three functions that translate scan-history rows into the agent-facing
summary shapes:

* ``recon_session_host_breakdown`` — per-host rollup (ip, hostname,
  open_port_count, services, open_ports) for the hosts a recon session
  has discovered.  Used by both ``/agent/recon/summary`` and the
  ``/agent/recon/complete`` response.
* ``web_targets_from_hosts`` — derive a web-fingerprint target list
  (http/https URLs) from a host's open ports so agents feeding
  httpx / eyewitness / nikto don't have to walk the service strings
  themselves.
* ``build_known_hosts_probe`` — given a scope that already has hosts
  with open ports, produce a ready-to-use service-probe command + IP
  list so an agent that the user explicitly asks to narrow to the
  known set has a pre-built command instead of having to query
  ``/agent/hosts`` and build its own targets file.

These are pure read-only functions taking a ``Session`` and identifiers,
returning typed agent_schemas shapes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_agent import ReconSession
from app.api.v1.endpoints.agent_common import _scoped_host_ids_subq
from app.api.v1.endpoints.agent_schemas import (
    KnownHostsProbeHelper, ReconHostBrief, ReconPortBrief, WebTarget,
)


def recon_session_host_breakdown(
    db: Session, recon_session_id: int,
) -> List[ReconHostBrief]:
    """Return per-host rollup (ip, hostname, open_port_count, services)
    for a recon session's ingested scans.

    v2.11.1 — the prompt always claimed /recon/summary returned this,
    but the response was only totals.  Shared by the summary and
    complete endpoints so both return the same shape.  Bounded by
    scan membership (the join through IngestionJob) so a host that
    lives in the same scope but was ingested by a different session
    doesn't pollute the breakdown.

    v2.13.1 — also filters by scope membership via HostSubnetMapping,
    matching the scope-isolation applied to /agent/hosts et al. in
    v2.13.0.  Prior behavior surfaced out-of-scope entries (e.g.
    ``127.0.0.1``, ``localhost`` strings from httpx TLS-SAN expansion)
    whenever a parser wrote a row whose IP/hostname didn't belong to
    the scope — observed live during recon session #6 testing.
    """
    # Resolve the session's scope_id so we can scope-filter hosts.
    session_row = (
        db.query(ReconSession)
        .filter(ReconSession.id == recon_session_id)
        .first()
    )
    scope_id = session_row.scope_id if session_row else None

    # Distinct host rows reachable from this session's scans, bounded
    # by scope membership (HostSubnetMapping → Subnet under scope_id).
    # If the session has no scope (shouldn't happen for recon-scoped
    # keys but guard anyway), fall through to the unscoped behavior.
    query = (
        db.query(models.Host)
        .join(
            models.HostScanHistory,
            models.HostScanHistory.host_id == models.Host.id,
        )
        .join(
            models.IngestionJob,
            models.IngestionJob.scan_id == models.HostScanHistory.scan_id,
        )
        .filter(models.IngestionJob.recon_session_id == recon_session_id)
    )
    if scope_id is not None:
        query = query.filter(
            models.Host.id.in_(_scoped_host_ids_subq(db, scope_id))
        )
    host_rows = query.distinct().all()
    if not host_rows:
        return []

    host_ids = [h.id for h in host_rows]
    # Port counts in one batch query.
    port_count_rows = dict(
        db.query(models.Port.host_id, func.count(models.Port.id))
        .filter(
            models.Port.host_id.in_(host_ids),
            models.Port.state == "open",
        )
        .group_by(models.Port.host_id)
        .all()
    )
    # Distinct service names per host.
    service_rows = (
        db.query(models.Port.host_id, models.Port.service_name)
        .filter(
            models.Port.host_id.in_(host_ids),
            models.Port.state == "open",
            models.Port.service_name.isnot(None),
        )
        .distinct()
        .all()
    )
    services_by_host: Dict[int, List[str]] = {}
    for host_id, svc in service_rows:
        services_by_host.setdefault(host_id, []).append(svc)

    # v2.13.2 — per-port detail so agents don't have to cross-reference
    # /agent/hosts or parse uploaded XML.  One batched query for all
    # hosts in this session, grouped into a list per host_id.
    port_rows = (
        db.query(models.Port)
        .filter(
            models.Port.host_id.in_(host_ids),
            models.Port.state == "open",
        )
        .order_by(models.Port.host_id, models.Port.port_number)
        .all()
    )
    ports_by_host: Dict[int, List[ReconPortBrief]] = {}
    for p in port_rows:
        ports_by_host.setdefault(p.host_id, []).append(
            ReconPortBrief(
                port=p.port_number,
                protocol=p.protocol or "tcp",
                state=p.state or "open",
                service=p.service_name,
                product=p.service_product,
                version=p.service_version,
            )
        )

    result = [
        ReconHostBrief(
            host_id=h.id,
            ip_address=h.ip_address,
            hostname=h.hostname,
            open_port_count=port_count_rows.get(h.id, 0),
            services=sorted(services_by_host.get(h.id, [])),
            open_ports=ports_by_host.get(h.id, []),
        )
        for h in host_rows
    ]
    # v2.43.3 (AUD-N2): sort IP-naturally, not lexicographically.  The
    # pre-fix string sort put "10.0.0.100" before "10.0.0.2" because
    # '1' < '2' at character index 8.  ipaddress.ip_address gives the
    # numeric ordering operators expect; fallback to the original
    # string for non-IP rows (the model nullably accepts hostnames in
    # this column on legacy data).
    import ipaddress

    def _ip_sort_key(row: ReconHostBrief):
        try:
            return (0, ipaddress.ip_address(row.ip_address or ""))
        except (ValueError, TypeError):
            # Bucket non-IP values after real IPs so the operator-facing
            # table still groups consistently.
            return (1, row.ip_address or "")

    result.sort(key=_ip_sort_key)
    return result


# ---------------------------------------------------------------------------
# Host-stats rollup — replaces the per-host table on the recon detail page.
# ---------------------------------------------------------------------------
#
# A recon session that ingested a /20 sweep can easily land 40k+ hosts.
# The old per-host list (recon_session_host_breakdown) materialised every
# one of them on every detail-page load — 10-30 MB JSON, hundreds of
# thousands of frontend DOM nodes.  Nothing on that page actually needed
# the host list; what users want is "what did this run produce, per
# tool, in aggregate" plus a link to /inventory for the full list.
#
# This helper computes the aggregate with a fixed number of GROUP BY
# queries (six) that don't scale with host count.  Each query operates
# on indexed columns and returns at most ~10 rows.

def _session_scan_ids_subq(db: Session, recon_session_id: int):
    """Scalar subquery of scan_ids belonging to this recon session.

    Mirrors the join recon_session_host_breakdown uses
    (IngestionJob.recon_session_id) but returns just the scan_id
    column so it can be plugged into ``WHERE scan_id IN (...)``
    filters.  Filter out NULL scan_ids — pending uploads that never
    produced a scan.
    """
    return (
        db.query(models.IngestionJob.scan_id)
        .filter(models.IngestionJob.recon_session_id == recon_session_id)
        .filter(models.IngestionJob.scan_id.isnot(None))
        .subquery()
    )


def _session_host_ids_subq(
    db: Session, recon_session_id: int, scope_id: Optional[int],
):
    """Distinct host_ids touched by any scan in this session, optionally
    bounded by scope membership.

    Mirrors the bounding applied in recon_session_host_breakdown — a
    host reachable from the session's scans but living outside the
    scope (e.g. a hostname resolution that landed at a public IP
    outside the subnet) shouldn't be counted as part of the run.
    """
    scan_ids = _session_scan_ids_subq(db, recon_session_id)
    query = (
        db.query(models.HostScanHistory.host_id)
        .filter(models.HostScanHistory.scan_id.in_(scan_ids.select()))
    )
    if scope_id is not None:
        query = query.filter(
            models.HostScanHistory.host_id.in_(_scoped_host_ids_subq(db, scope_id))
        )
    return query.distinct().subquery()


def recon_session_host_stats(
    db: Session, recon_session_id: int,
) -> Dict[str, Any]:
    """Return the aggregate stats rollup for a recon session.

    Returns a dict with the shape expected by ``ReconHostStats``:
    ``host_count``, ``host_count_with_open_ports``, ``by_tool``,
    ``top_services``, ``top_open_ports``.  All five fields are
    computed with constant-time-in-host-count queries — the rollup
    is O(distinct tools) + O(distinct services) + O(distinct port
    numbers), all of which are small.

    Returns zero-filled defaults (not None) for empty sessions so the
    UI doesn't have to special-case "no data yet" — it sees the same
    shape and renders empty rows.
    """
    session_row = (
        db.query(ReconSession)
        .filter(ReconSession.id == recon_session_id)
        .first()
    )
    scope_id = session_row.scope_id if session_row else None
    if session_row is None:
        return _empty_host_stats()

    scan_ids_subq = _session_scan_ids_subq(db, recon_session_id)
    host_ids_subq = _session_host_ids_subq(db, recon_session_id, scope_id)

    # 1. Total distinct hosts.
    host_count = (
        db.query(func.count())
        .select_from(host_ids_subq)
        .scalar() or 0
    )
    if host_count == 0:
        return _empty_host_stats()

    # 2. Hosts with at least one open port (current state).
    host_count_with_open_ports = (
        db.query(func.count(distinct(models.Port.host_id)))
        .filter(
            models.Port.host_id.in_(host_ids_subq.select()),
            models.Port.state == "open",
        )
        .scalar() or 0
    )

    # 3a. Per-tool scan + host counts.
    #     Split from the port count below to avoid the
    #     HostScanHistory ⨯ PortScanHistory cross-product that a
    #     single GROUP BY would force.
    tool_host_rows = (
        db.query(
            models.Scan.tool_name,
            func.count(distinct(models.Scan.id)).label("scan_count"),
            func.count(distinct(models.HostScanHistory.host_id)).label("host_count"),
        )
        .outerjoin(
            models.HostScanHistory,
            models.HostScanHistory.scan_id == models.Scan.id,
        )
        .filter(models.Scan.id.in_(scan_ids_subq.select()))
        .group_by(models.Scan.tool_name)
        .all()
    )

    # 3b. Per-tool port counts.
    tool_port_rows = dict(
        db.query(
            models.Scan.tool_name,
            func.count(distinct(models.PortScanHistory.port_id)).label("port_count"),
        )
        .outerjoin(
            models.PortScanHistory,
            models.PortScanHistory.scan_id == models.Scan.id,
        )
        .filter(models.Scan.id.in_(scan_ids_subq.select()))
        .group_by(models.Scan.tool_name)
        .all()
    )

    by_tool = [
        {
            "tool_name": (row.tool_name or "unknown"),
            "scan_count": int(row.scan_count or 0),
            "host_count": int(row.host_count or 0),
            "port_count": int(tool_port_rows.get(row.tool_name, 0)),
        }
        for row in tool_host_rows
    ]
    # Stable, useful sort: most hosts first, then most ports.
    by_tool.sort(key=lambda t: (-t["host_count"], -t["port_count"], t["tool_name"]))

    # 4. Top 10 services by distinct host count.  Bounded to open
    #    ports — a service rolled up across hundreds of filtered
    #    states isn't actionable.
    service_rows = (
        db.query(
            models.Port.service_name,
            func.count(distinct(models.Port.host_id)).label("host_count"),
        )
        .filter(
            models.Port.host_id.in_(host_ids_subq.select()),
            models.Port.state == "open",
            models.Port.service_name.isnot(None),
            models.Port.service_name != "",
        )
        .group_by(models.Port.service_name)
        .order_by(func.count(distinct(models.Port.host_id)).desc())
        .limit(10)
        .all()
    )
    top_services = [
        {"service_name": r.service_name, "host_count": int(r.host_count or 0)}
        for r in service_rows
    ]

    # 5. Top 10 open (port_number, protocol) pairs by distinct host count.
    port_rows = (
        db.query(
            models.Port.port_number,
            models.Port.protocol,
            func.count(distinct(models.Port.host_id)).label("host_count"),
        )
        .filter(
            models.Port.host_id.in_(host_ids_subq.select()),
            models.Port.state == "open",
        )
        .group_by(models.Port.port_number, models.Port.protocol)
        .order_by(func.count(distinct(models.Port.host_id)).desc())
        .limit(10)
        .all()
    )
    top_open_ports = [
        {
            "port_number": int(r.port_number),
            "protocol": r.protocol or "tcp",
            "host_count": int(r.host_count or 0),
        }
        for r in port_rows
    ]

    return {
        "host_count": int(host_count),
        "host_count_with_open_ports": int(host_count_with_open_ports),
        "by_tool": by_tool,
        "top_services": top_services,
        "top_open_ports": top_open_ports,
    }


def _empty_host_stats() -> Dict[str, Any]:
    return {
        "host_count": 0,
        "host_count_with_open_ports": 0,
        "by_tool": [],
        "top_services": [],
        "top_open_ports": [],
    }


def recon_session_diff_ips(
    db: Session,
    session_a_id: int,
    session_b_id: int,
    limit: int = 50,
) -> Dict[str, Any]:
    """SQL-side IP set difference between two recon sessions.

    Returns capped lists of hosts that appear in one session but not
    the other.  The full counts (uncapped) are returned alongside so
    the UI can render "{cap} of {total} new hosts — view all in
    Inventory" affordances.

    Each "host" row carries ``host_id``, ``ip_address``, and
    ``hostname`` so the comparison view can link straight to the host
    detail page.  Ports/services are deliberately omitted — the
    purpose is "what changed at the host level"; if the user wants
    per-host detail they go to the host page or to Inventory.

    Bounded by each session's own scope (the same scope filter used
    by the per-session breakdown), so a host scanned by both sessions
    but living in only one session's scope is treated as unique to
    that session.  This matches what an operator means by "what did
    run B find that A didn't".
    """
    session_a = db.query(ReconSession).filter(ReconSession.id == session_a_id).first()
    session_b = db.query(ReconSession).filter(ReconSession.id == session_b_id).first()
    if session_a is None or session_b is None:
        return {
            "in_a_not_b_count": 0,
            "in_b_not_a_count": 0,
            "shared_count": 0,
            "in_a_not_b_sample": [],
            "in_b_not_a_sample": [],
            "limit": limit,
        }

    hosts_a = _session_host_ids_subq(db, session_a_id, session_a.scope_id)
    hosts_b = _session_host_ids_subq(db, session_b_id, session_b.scope_id)

    # Resolve diff host_ids — set differences in SQL.  EXCEPT works
    # on PostgreSQL; for SQLite fall back to a NOT IN form so the
    # test suite still passes.
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        in_a_not_b_ids = [
            row[0] for row in
            db.execute(
                hosts_a.select().except_(hosts_b.select())
            ).all()
        ]
        in_b_not_a_ids = [
            row[0] for row in
            db.execute(
                hosts_b.select().except_(hosts_a.select())
            ).all()
        ]
        shared_count = (
            db.query(func.count())
            .select_from(hosts_a.select().intersect(hosts_b.select()).subquery())
            .scalar() or 0
        )
    else:
        a_ids = {row[0] for row in db.query(hosts_a.c.host_id).all()}
        b_ids = {row[0] for row in db.query(hosts_b.c.host_id).all()}
        in_a_not_b_ids = list(a_ids - b_ids)
        in_b_not_a_ids = list(b_ids - a_ids)
        shared_count = len(a_ids & b_ids)

    def _sample_rows(host_ids: List[int]) -> List[Dict[str, Any]]:
        if not host_ids:
            return []
        # Cap before the per-row query so we don't fetch 10k Host rows
        # just to slice to 50.  Sort by ip_address so the cap is
        # deterministic — same first-50 every render.
        import ipaddress

        def _ip_key(h):
            try:
                return (0, ipaddress.ip_address(h.ip_address or ""))
            except (ValueError, TypeError):
                return (1, h.ip_address or "")

        rows = (
            db.query(models.Host)
            .filter(models.Host.id.in_(host_ids))
            .all()
        )
        rows.sort(key=_ip_key)
        return [
            {
                "host_id": h.id,
                "ip_address": h.ip_address,
                "hostname": h.hostname,
            }
            for h in rows[:limit]
        ]

    return {
        "in_a_not_b_count": len(in_a_not_b_ids),
        "in_b_not_a_count": len(in_b_not_a_ids),
        "shared_count": int(shared_count),
        "in_a_not_b_sample": _sample_rows(in_a_not_b_ids),
        "in_b_not_a_sample": _sample_rows(in_b_not_a_ids),
        "limit": limit,
    }


# Common HTTP/HTTPS port → scheme mapping used when deriving web
# targets from open-port data.  Service-name detection in nmap is
# inconsistent across versions, but port numbers are reliable
# signals for the common cases.
_WEB_PORT_SCHEMES: Dict[int, str] = {
    80: "http",
    8080: "http",
    8000: "http",
    81: "http",
    443: "https",
    8443: "https",
    4443: "https",
}


def web_targets_from_hosts(hosts: List[ReconHostBrief]) -> List[WebTarget]:
    """Derive a web-fingerprint target list from per-host open ports.

    Maps common HTTP / HTTPS ports to a concrete URL so agents feeding
    httpx / eyewitness / nikto can skip the "walk hosts[].services
    looking for 'http' or 'ssl/http' or 'https-alt'" step.  Service-
    name detection in nmap is inconsistent across versions, but port
    numbers are reliable signals for the common cases.

    v2.13.2 — added after feedback #5.
    """
    targets: List[WebTarget] = []
    for h in hosts:
        for p in h.open_ports:
            scheme = _WEB_PORT_SCHEMES.get(p.port)
            if scheme is None:
                # Honor explicit service-name hits too (e.g. https on
                # 10443 that a version probe identified).
                svc = (p.service or "").lower()
                if "https" in svc or "ssl/http" in svc:
                    scheme = "https"
                elif "http" in svc:
                    scheme = "http"
            if scheme is None:
                continue
            default_port = 443 if scheme == "https" else 80
            host_part = h.ip_address
            url = (
                f"{scheme}://{host_part}/"
                if p.port == default_port
                else f"{scheme}://{host_part}:{p.port}/"
            )
            targets.append(WebTarget(
                host_id=h.host_id,
                ip_address=h.ip_address,
                hostname=h.hostname,
                port=p.port,
                protocol=scheme,
                url=url,
            ))
    return targets


def session_hosts_file_content(hosts: List[ReconHostBrief]) -> str:
    """Newline-joined IP list of the hosts discovered so far this session,
    ready to redirect to a file and feed the next tool via ``-iL``.

    Mirrors the ``KnownHostsProbeHelper.live_hosts_file_content`` blob
    format (trailing newline included) but draws from the session's own
    breakdown rather than prior-recon known hosts.  Returns an empty string
    when nothing's been discovered yet.  ``hosts`` is already IP-sorted by
    ``recon_session_host_breakdown``, so the file order is deterministic.
    """
    ips = [h.ip_address for h in hosts if h.ip_address]
    return ("\n".join(ips) + "\n") if ips else ""


def build_known_hosts_probe(
    db: Session, project_id: int, scope_id: int,
) -> KnownHostsProbeHelper | None:
    """Build a ready-to-run service-probe helper for already-known hosts.

    Returns None when the scope has no hosts with open ports (no prior
    recon data to deepen on).  Otherwise returns a helper carrying the
    live-host IP list, a file-content blob the agent can redirect to
    ``live-hosts.txt``, and a pre-built nmap command.

    v2.13.2 — supports the "user narrows to known hosts" path during
    plan approval without forcing the agent to hit /agent/hosts and
    construct the file themselves.
    """
    # Hosts in-scope + currently having at least one open port.  Scope
    # membership via HostSubnetMapping matches the filter used by
    # /agent/hosts and /agent/recon/summary.
    scope_host_subq = _scoped_host_ids_subq(db, scope_id)
    rows = (
        db.query(models.Host.ip_address)
        .join(models.Port, models.Port.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            models.Host.id.in_(scope_host_subq),
            models.Port.state == "open",
        )
        .distinct()
        .order_by(models.Host.ip_address)
        .all()
    )
    live_hosts = [r[0] for r in rows]
    if not live_hosts:
        return None
    file_content = "\n".join(live_hosts) + "\n"
    # `nmap -iL -` reads the host list from stdin; the command uses
    # the file form for clarity.  Agents may write the file content
    # directly from live_hosts_file_content rather than hitting the
    # filesystem through an echo pipeline.
    command = (
        "nmap -sV -sC -T3 --top-ports 1000 -iL live-hosts.txt "
        "-oX nmap-services-known.xml"
    )
    note = (
        f"{len(live_hosts)} host(s) with open ports are already known in this "
        f"scope.  If the user asks you to narrow to the known set during plan "
        f"approval (skipping fresh discovery), write `live_hosts_file_content` "
        f"to `live-hosts.txt` and run `command`.  The default plan still leads "
        f"with comprehensive discovery (nmap sweep / masscan / rustscan) to "
        f"catch new or changed hosts — narrowing is a user-initiated trade-off "
        f"between speed and coverage."
    )
    return KnownHostsProbeHelper(
        live_hosts=live_hosts,
        live_hosts_file_content=file_content,
        command=command,
        note=note,
    )
