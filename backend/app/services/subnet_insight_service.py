"""Per-subnet insights — "which network ranges are neglected / in bad shape?"

This is the attention model (exposure + neglect) re-grouped by subnet — the
parameterization attention_service's docstring promised ("only the GROUP BY
key changes, not the model") — plus a third **hygiene** lens that reads
point-in-time asset state to surface "lack of IT management":

  * Exposure — severity-weighted active findings, scaled by the subnet's
    site criticality tier (reuses attention_service's weights verbatim so
    the project / site / subnet views can never disagree on what a
    "critical" is worth).
  * Neglect  — unowned findings, unreviewed hosts, scan staleness, and the
    coverage signal of a scoped subnet with no discovered hosts.
  * Hygiene  — EOL / unsupported OS, expired or self-signed TLS, weak/guest
    authentication, and risky exposed services.  Every number stays
    DECOMPOSED (3 EOL hosts, 2 cert issues, 1 weak-auth host) — never a
    single opaque score.  That explainability is the hard-won lesson the
    deleted risk-scoring system left behind (see attention_service).

A host is attributed to exactly ONE subnet — its most-specific (longest
prefix) matching subnet — so overlapping ranges never double-count.

Performance: every signal is gathered with a handful of project-scoped bulk
queries (each filtered on an indexed ``project_id`` join), then bucketed in
Python by the host→subnet map.  No per-subnet query loop, and no giant
``IN (host_ids…)`` clause — work is bounded by project size, not subnet
count.
"""
from __future__ import annotations

import ipaddress
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db import models
from app.db.models import (
    FollowStatus,
    HostFollow,
    HostSubnetMapping,
    Scope,
    Site,
    Subnet,
    WebInterface,
)
from app.db.models_confidence import NetexecResult
from app.db.models_findings import Finding, FindingHost
from app.services.attention_service import (
    _ACTIVE_FINDING_STATUSES,
    _SEVERITY_WEIGHT,
    _STALE_DAYS,
    _TIER_WEIGHT,
)
from app.services.os_eol import match_eol_os
from app.services.ports_of_interest import ports_by_number
from app.services.subnet_calculator import SubnetCalculator

# Usernames netexec reports for an unauthenticated / guest / null session.
_WEAK_USERNAMES = {"", "guest", "anonymous", "null", "<blank>", "''"}
# Cap the per-subnet EOL host detail list so the payload stays bounded on a
# huge flat subnet; the count is always the true total.
_EOL_DETAIL_CAP = 10
# Floor for "no timestamp" when picking the latest observation per host.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _normalize_dt(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes, Postgres tz-aware — normalize to UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_cert_dt(value: Any) -> Optional[datetime]:
    """Best-effort parse of a certificate ``not_after`` string across tools."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%b %d %H:%M:%S %Y %Z", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _cert_issue(tls_info: Any, now: datetime) -> Optional[str]:
    """Return 'expired' / 'self-signed' if this cert is a hygiene concern, else None.

    Keys vary by tool (httpx uses ``*_dn``; the model comment uses the bare
    names), so we probe the common variants.
    """
    if not isinstance(tls_info, dict):
        return None
    not_after = _parse_cert_dt(tls_info.get("not_after"))
    if not_after is not None and not_after < now:
        return "expired"
    issuer = tls_info.get("issuer_dn") or tls_info.get("issuer")
    subject = tls_info.get("subject_dn") or tls_info.get("subject") or tls_info.get("subject_cn")
    if issuer and subject and str(issuer) == str(subject):
        return "self-signed"
    return None


def _is_weak_user(username: Optional[str]) -> bool:
    """True only for an EXPLICIT guest / anonymous / blank (null-session) identity.

    ``None`` means the tool did not record an identity (unknown) — NOT weak.
    The NetExec parser currently never persists a username, so treating
    ``None`` as weak flagged every successful authentication as "weak auth";
    an unknown identity must never count against a host.  Once the parser
    captures identities, an explicit guest/blank login is still caught here.
    """
    if username is None:
        return False
    return username.strip().lower() in _WEAK_USERNAMES


def _zero_sev() -> Dict[str, int]:
    return {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}


def _load_subnet_meta(db: Session, project_id: int) -> Dict[int, Dict[str, Any]]:
    """Per-subnet metadata for a project's scopes, keyed by subnet id.

    Shared by ``compute_subnet_insights`` and ``resolve_host_locations`` so the
    "what subnets does this project have, and how specific is each" question
    has one implementation.  Returns ``{}`` when the project has no subnets.
    """
    subnet_rows = (
        db.query(
            Subnet.id, Subnet.cidr, Subnet.description, Subnet.site,
            Subnet.site_id, Scope.name,
        )
        .join(Scope, Subnet.scope_id == Scope.id)
        .filter(Scope.project_id == project_id)
        .all()
    )
    meta: Dict[int, Dict[str, Any]] = {}
    for sid, cidr, desc, site, site_id, scope_name in subnet_rows:
        try:
            prefixlen = ipaddress.ip_network(cidr, strict=False).prefixlen
        except ValueError:
            prefixlen = 0
        metrics = SubnetCalculator.calculate_subnet_metrics(cidr)
        meta[sid] = {
            "cidr": cidr,
            "description": desc,
            "site": site,
            "site_id": site_id,
            "scope_name": scope_name,
            "prefixlen": prefixlen,
            "usable_addresses": metrics.get("usable_addresses", 0),
        }
    return meta


def resolve_host_locations(
    db: Session, project_id: int, subnet_meta: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[int, Dict[str, Any]]:
    """Map each host to its ONE most-specific (longest-prefix) subnet + site.

    Returns ``host_id -> {subnet_id, cidr, site, site_id, scope_name}`` for
    every host that falls inside a scoped subnet.  Hosts not in any subnet are
    absent (they are out of scope).  Most-specific-wins mirrors the site
    attention model so a host inside both a /16 and a /24 resolves to the /24
    and is never double-counted.  Reused by the report exporter to tag each
    host entry with its site without duplicating the resolution rule.
    """
    if subnet_meta is None:
        subnet_meta = _load_subnet_meta(db, project_id)
    if not subnet_meta:
        return {}
    mappings = (
        db.query(HostSubnetMapping.host_id, HostSubnetMapping.subnet_id)
        .join(models.Host, HostSubnetMapping.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            HostSubnetMapping.subnet_id.in_(list(subnet_meta.keys())),
        )
        .all()
    )
    locations: Dict[int, Dict[str, Any]] = {}
    best_prefix: Dict[int, int] = {}
    for host_id, sid in mappings:
        m = subnet_meta[sid]
        pfx = m["prefixlen"]
        if host_id not in best_prefix or pfx > best_prefix[host_id]:
            best_prefix[host_id] = pfx
            locations[host_id] = {
                "subnet_id": sid,
                "cidr": m["cidr"],
                "site": m["site"],
                "site_id": m["site_id"],
                "scope_name": m["scope_name"],
            }
    return locations


def compute_subnet_insights(
    db: Session, project_id: int, limit: Optional[int] = 50, offset: int = 0,
) -> Dict[str, Any]:
    """Worst-first subnet insights, paginated.

    The aggregation is bounded by project size (a handful of bulk queries), but
    the *response* must be bounded too — a 6,000-subnet project would otherwise
    ship a multi-MB body and render 6,000 rows.  ``totals`` stays project-wide;
    ``subnets`` is the requested page of the worst-first list, with ``total``
    for the pager.  ``limit=None`` returns everything (used internally).
    """
    now = datetime.now(timezone.utc)

    # --- Subnets in this project's scopes ---------------------------------
    subnet_meta = _load_subnet_meta(db, project_id)
    if not subnet_meta:
        return {"adopted": False, "subnets": [], "total": 0,
                "limit": limit, "offset": offset, "totals": _empty_totals()}

    # --- Resolve each host to its most-specific subnet --------------------
    host_locations = resolve_host_locations(db, project_id, subnet_meta)
    host_to_subnet: Dict[int, int] = {hid: loc["subnet_id"] for hid, loc in host_locations.items()}
    subnet_hosts: Dict[int, List[int]] = defaultdict(list)
    for host_id, sid in host_to_subnet.items():
        subnet_hosts[sid].append(host_id)

    # --- Per-host metadata (os_name + last_seen) for in-scope hosts -------
    # Filter on the indexed project_id and bucket in Python rather than an
    # IN(host_ids) clause that would balloon on a 40k-host project.
    host_os: Dict[int, Optional[str]] = {}
    host_ip: Dict[int, Optional[str]] = {}
    host_last_seen: Dict[int, Optional[datetime]] = {}
    for hid, os_name, last_seen, ip in (
        db.query(models.Host.id, models.Host.os_name, models.Host.last_seen, models.Host.ip_address)
        .filter(models.Host.project_id == project_id)
        .all()
    ):
        if hid in host_to_subnet:
            host_os[hid] = os_name
            host_ip[hid] = ip
            host_last_seen[hid] = last_seen

    # --- Reviewed hosts (any user) ----------------------------------------
    reviewed = {
        h for (h,) in (
            db.query(HostFollow.host_id)
            .join(models.Host, HostFollow.host_id == models.Host.id)
            .filter(
                models.Host.project_id == project_id,
                HostFollow.status == FollowStatus.REVIEWED.value,
            )
            .distinct()
        )
    }

    # --- Exposure: active findings → distinct subnets they touch ----------
    finding_rows = (
        db.query(FindingHost.host_id, Finding.id, Finding.severity, Finding.owner_id)
        .join(Finding, FindingHost.finding_id == Finding.id)
        .filter(Finding.project_id == project_id, Finding.status.in_(_ACTIVE_FINDING_STATUSES))
        .all()
    )
    finding_subnets: Dict[int, set] = defaultdict(set)
    finding_meta: Dict[int, tuple] = {}
    for host_id, fid, sev, owner in finding_rows:
        sid = host_to_subnet.get(host_id)
        if sid is None:
            continue
        finding_subnets[fid].add(sid)
        finding_meta[fid] = (sev, owner)
    subnet_sev: Dict[int, Dict[str, int]] = defaultdict(_zero_sev)
    subnet_unowned: Dict[int, int] = defaultdict(int)
    for fid, sids in finding_subnets.items():
        sev, owner = finding_meta[fid]
        for sid in sids:
            if sev in subnet_sev[sid]:
                subnet_sev[sid][sev] += 1
            if owner is None:
                subnet_unowned[sid] += 1

    # --- Hygiene: EOL OS ---------------------------------------------------
    eol_by_subnet: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for hid, os_name in host_os.items():
        eol = match_eol_os(os_name)
        if eol is not None:
            eol_by_subnet[host_to_subnet[hid]].append({
                "host_id": hid,
                "ip_address": host_ip.get(hid),
                "os_name": os_name,
                "eol_label": eol.label,
                "eol_date": eol.eol_date,
            })

    # --- Hygiene: TLS cert issues (expired / self-signed), CURRENT only ---
    # A rescan adds a new WebInterface row per scan (unique on scan_id, url,
    # source), so reading all history would let a cert that was expired in an
    # old scan keep the host flagged after a later clean re-observation.
    # Reduce to the latest observation per (host, interface) before judging.
    cert_latest: Dict[tuple, tuple] = {}  # (host_id, url) -> (last_seen, tls_info)
    for host_id, url, tls_info, last_seen in (
        db.query(
            WebInterface.host_id, WebInterface.url,
            WebInterface.tls_info, WebInterface.last_seen,
        )
        .filter(
            WebInterface.project_id == project_id,
            WebInterface.host_id.isnot(None),
            WebInterface.tls_info.isnot(None),
        )
        .all()
    ):
        if host_to_subnet.get(host_id) is None:
            continue
        ls = _normalize_dt(last_seen) or _EPOCH
        key = (host_id, url)
        prev = cert_latest.get(key)
        if prev is None or ls >= prev[0]:
            cert_latest[key] = (ls, tls_info)
    cert_issue_hosts: Dict[int, set] = defaultdict(set)
    for (host_id, _url), (_ls, tls_info) in cert_latest.items():
        if _cert_issue(tls_info, now):
            cert_issue_hosts[host_to_subnet[host_id]].add(host_id)

    # --- Hygiene: weak / guest authentication, CURRENT only ---------------
    # Latest NetExec observation per (host, protocol, port).  We no longer
    # pre-filter to auth_success=True: if the most recent observation is a
    # failure (creds rotated), an older guest success must not still flag the
    # host.  ``_is_weak_user`` only flags an EXPLICIT guest/blank identity.
    nxc_latest: Dict[tuple, tuple] = {}  # (host_id, proto, port) -> (discovered_at, auth, user)
    for host_id, protocol, port, auth_success, username, discovered_at in (
        db.query(
            NetexecResult.host_id, NetexecResult.protocol, NetexecResult.port,
            NetexecResult.auth_success, NetexecResult.username, NetexecResult.discovered_at,
        )
        .join(models.Host, NetexecResult.host_id == models.Host.id)
        .filter(models.Host.project_id == project_id)
        .all()
    ):
        if host_to_subnet.get(host_id) is None:
            continue
        d = _normalize_dt(discovered_at) or _EPOCH
        key = (host_id, protocol, port)
        prev = nxc_latest.get(key)
        if prev is None or d >= prev[0]:
            nxc_latest[key] = (d, auth_success, username)
    weak_auth_hosts: Dict[int, set] = defaultdict(set)
    for (host_id, _proto, _port), (_d, auth_success, username) in nxc_latest.items():
        if auth_success and _is_weak_user(username):
            weak_auth_hosts[host_to_subnet[host_id]].add(host_id)

    # --- Hygiene: risky exposed services (ports of interest) --------------
    poi_map = ports_by_number()
    poi_numbers = list(poi_map.keys())
    risky_hosts: Dict[int, set] = defaultdict(set)
    risky_ports: Dict[int, Dict[int, set]] = defaultdict(lambda: defaultdict(set))
    if poi_numbers:
        for host_id, port_number in (
            db.query(models.Port.host_id, models.Port.port_number)
            .join(models.Host, models.Port.host_id == models.Host.id)
            .filter(
                models.Host.project_id == project_id,
                models.Port.state == "open",
                models.Port.port_number.in_(poi_numbers),
            )
            .all()
        ):
            sid = host_to_subnet.get(host_id)
            if sid is None:
                continue
            risky_hosts[sid].add(host_id)
            risky_ports[sid][port_number].add(host_id)

    # --- Site tiers -------------------------------------------------------
    sites_by_id = {s.id: s for s in db.query(Site).filter(Site.project_id == project_id).all()}

    # --- Assemble per-subnet rows -----------------------------------------
    out: List[Dict[str, Any]] = []
    totals = _empty_totals()
    for sid, meta in subnet_meta.items():
        hids = subnet_hosts.get(sid, [])
        host_count = len(hids)

        by_sev = dict(subnet_sev.get(sid, _zero_sev()))
        active = sum(by_sev.values())
        unowned = subnet_unowned.get(sid, 0)
        unreviewed = sum(1 for h in hids if h not in reviewed)

        site_obj = sites_by_id.get(meta["site_id"]) if meta["site_id"] else None
        tier = site_obj.criticality_tier if site_obj else 3
        exposure_raw = sum(_SEVERITY_WEIGHT.get(s, 0) * c for s, c in by_sev.items())
        weighted = round(exposure_raw * _TIER_WEIGHT.get(tier, 1.0), 1)

        # Staleness should describe the SUBNET, not just its freshest host:
        # the old max(last_seen) let one recently-seen host make an otherwise
        # stale subnet read as fresh.  Use the MEDIAN host age plus the count
        # / share of hosts past the stale threshold.
        ages = sorted(
            max(0, (now - _normalize_dt(host_last_seen[h])).days)
            for h in hids if host_last_seen.get(h) is not None
        )
        if ages:
            median_age = ages[len(ages) // 2]
            stale_host_count = sum(1 for a in ages if a >= _STALE_DAYS)
            stale_host_pct = round(100.0 * stale_host_count / len(ages), 1)
        else:
            median_age = None
            stale_host_count = 0
            stale_host_pct = None

        eol_list = eol_by_subnet.get(sid, [])
        eol_count = len(eol_list)
        cert_count = len(cert_issue_hosts.get(sid, set()))
        weak_count = len(weak_auth_hosts.get(sid, set()))
        risky_count = len(risky_hosts.get(sid, set()))
        risky_breakdown = sorted(
            (
                {
                    "port": pn,
                    "label": poi_map[pn].label,
                    "category": poi_map[pn].category,
                    "host_count": len(hset),
                }
                for pn, hset in risky_ports.get(sid, {}).items()
            ),
            key=lambda r: r["host_count"],
            reverse=True,
        )

        no_coverage = host_count == 0
        action = _recommend(
            no_coverage=no_coverage, unowned=unowned, critical=by_sev["critical"],
            eol=eol_count, weak=weak_count, stale=median_age, cert=cert_count,
            unreviewed=unreviewed,
        )

        # Neglect+hygiene magnitude — the worst-first tiebreaker after
        # exposure.  A zero-host scoped subnet is a loud coverage signal.
        neglect_magnitude = (
            unowned + unreviewed + eol_count + weak_count + cert_count
            + (5 if no_coverage else 0)
        )

        out.append({
            "subnet_id": sid,
            "cidr": meta["cidr"],
            "scope_name": meta["scope_name"],
            "site": meta["site"],
            "site_id": meta["site_id"],
            "criticality_tier": tier,
            "host_count": host_count,
            "usable_addresses": meta["usable_addresses"],
            "no_coverage": no_coverage,
            "exposure": {
                "raw_score": exposure_raw,
                "weighted_score": weighted,
                "active_findings": active,
                "by_severity": by_sev,
            },
            "neglect": {
                "unowned_active_findings": unowned,
                "unreviewed_hosts": unreviewed,
                "median_host_age_days": median_age,
                "stale_host_count": stale_host_count,
                "stale_host_pct": stale_host_pct,
            },
            "hygiene": {
                "eol_os_hosts": eol_count,
                "eol_os_detail": eol_list[:_EOL_DETAIL_CAP],
                "cert_issue_hosts": cert_count,
                "weak_auth_hosts": weak_count,
                "risky_service_hosts": risky_count,
                "risky_services": risky_breakdown,
            },
            "recommended_action": action,
            "_neglect_magnitude": neglect_magnitude,
        })

        # These per-subnet counts don't double-count across the project: a
        # host belongs to exactly ONE subnet (most-specific), so summing
        # per-subnet host counts yields project distinct totals.
        totals["subnet_count"] += 1
        totals["hosts_in_scope"] += host_count
        totals["eol_os_hosts"] += eol_count
        totals["cert_issue_hosts"] += cert_count
        totals["weak_auth_hosts"] += weak_count

    # Finding totals are computed from UNIQUE findings, not by summing the
    # per-subnet counts: a finding spanning hosts in N subnets is counted
    # once per subnet above (an "incidence"), which would inflate the project
    # total N-fold.  finding_meta is keyed by Finding.id and already limited
    # to findings touching an in-scope subnet.
    for _fid, (sev, _owner) in finding_meta.items():
        totals["active_findings"] += 1
        if sev in totals["by_severity"]:
            totals["by_severity"][sev] += 1

    # Worst-first: tier-weighted exposure desc, then neglect+hygiene desc,
    # then larger host_count (a bigger blast radius breaks ties).
    out.sort(key=lambda r: (-r["exposure"]["weighted_score"], -r["_neglect_magnitude"], -r["host_count"]))
    for r in out:
        r.pop("_neglect_magnitude", None)

    total = len(out)
    page = out[offset:offset + limit] if limit is not None else out[offset:]
    return {"adopted": True, "subnets": page, "total": total,
            "limit": limit, "offset": offset, "totals": totals}


def _recommend(*, no_coverage, unowned, critical, eol, weak, stale, cert, unreviewed) -> Dict[str, str]:
    """Map the loudest component to the next action.  Order is deliberate:
    a never-discovered scoped range, then untriaged backlog, then open
    criticals, then the management-hygiene signals, then review/staleness."""
    if no_coverage:
        return {"kind": "scan", "text": "Scoped range with no discovered hosts — confirm scan coverage."}
    if unowned > 0:
        return {"kind": "triage", "text": f"{unowned} active finding{_s(unowned)} unowned — assign an owner."}
    if critical > 0:
        return {"kind": "remediate", "text": f"{critical} critical finding{_s(critical)} open."}
    if eol > 0:
        return {"kind": "modernize", "text": f"{eol} host{_s(eol)} on end-of-life OS — upgrade or isolate."}
    if weak > 0:
        return {"kind": "harden", "text": f"{weak} host{_s(weak)} with weak/guest authentication."}
    if cert > 0:
        return {"kind": "renew-cert", "text": f"{cert} host{_s(cert)} with an expired or self-signed certificate."}
    if stale is not None and stale >= _STALE_DAYS:
        return {"kind": "rescan", "text": f"Stale — typical host last seen {stale} days ago."}
    if unreviewed > 0:
        return {"kind": "review", "text": f"{unreviewed} host{_s(unreviewed)} not yet reviewed."}
    return {"kind": "ok", "text": "No outstanding attention items."}


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _empty_totals() -> Dict[str, Any]:
    return {
        "subnet_count": 0,
        "hosts_in_scope": 0,
        "eol_os_hosts": 0,
        "cert_issue_hosts": 0,
        "weak_auth_hosts": 0,
        "active_findings": 0,
        "by_severity": _zero_sev(),
    }
