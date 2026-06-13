"""Systemic insights — "what does this environment systematically get wrong?"

The per-subnet insights view (subnet_insight_service) ranks *locations* by how
bad they are.  This view asks a different, cross-sectional question for a
single engagement's snapshot: **which weaknesses recur across the estate, and
how widely do they spread?**  A weakness on one host is incidental; the SAME
weakness across many hosts spanning multiple subnets and sites is a process
failure — and when it spans essentially the whole estate regardless of site,
it points at an organisational blind spot about a particular threat/vector
(SMB signing off everywhere → nobody understands relay; every cert self-signed
→ no PKI governance; Telnet everywhere → no concept of cleartext-credential
risk).  The spread IS the diagnosis.

Three nested tiers, broad → narrow:

  1. Estate blind spots — a *condition* (e.g. end-of-life OS, guest auth) scored
     by breadth (host fraction) × spread (distinct subnets / sites) × severity.
     A condition that spans most sites and clears a host-fraction floor is
     surfaced as the misunderstood vector, with its evidence inline.
  2. Segment outliers — subnets whose issue density (issues per host) is a
     statistical outlier versus the estate's OWN median.  Normalised by host
     count, so a big subnet doesn't always win — the point is anomaly, not size.
  3. Diagnostic profiles — the co-occurrence signature of conditions within a
     subnet, mapped to a likely root cause (patch-gap / no-PKI / cred-hygiene /
     flat-network / abandoned).

Everything is computed from one snapshot — no trends (engagements are short and
don't re-ingest).  Like subnet_insight_service, it gathers a handful of
project-scoped bulk queries and buckets in Python via the host→subnet/site map;
no per-subnet query loop.  Reuses that service's helpers verbatim so the two
views can never disagree on what a cert issue / weak auth / EOL OS is.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import models
from app.db.models import WebInterface
from app.db.models_confidence import NetexecResult
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.services.os_eol import match_eol_os
from app.services.ports_of_interest import ports_by_number
from app.services.cert_fields import cert_issue_from_columns
from app.services.subnet_insight_service import (
    _EPOCH,
    _is_weak_user,
    _load_subnet_meta,
    _normalize_dt,
    resolve_host_locations,
)

# A weakness must touch at least this fraction of in-scope hosts before it's
# considered a *systemic* pattern rather than a handful of incidents.
_SYSTEMIC_HOST_FRACTION = 0.10
# To be promoted to an estate-wide "blind spot", a condition must additionally
# span at least this fraction of the sites that exist (when >1 site exists).
_BLINDSPOT_SITE_FRACTION = 0.6
# A segment is an outlier when its issue density is at least this multiple of
# the estate median density (guarded by a small host floor so tiny subnets with
# one issue don't dominate).
_OUTLIER_FACTOR = 2.0
_OUTLIER_MIN_HOSTS = 3
# Cleartext-credential ports (credentials observable on the wire).
_CLEARTEXT_PORTS = {21, 23, 110, 143}


# (key, label, vector, severity_weight, recommended_action) for the conditions
# computed from per-host state.  vuln monoculture is handled separately because
# it's keyed per plugin, not a single estate-wide set.
_CONDITIONS = [
    ("eol_os", "End-of-life operating systems",
     "No OS lifecycle / patch programme — unsupported systems accrete unpatched.",
     5, "Inventory and upgrade or isolate end-of-life systems."),
    ("cleartext_services", "Cleartext credential services (Telnet/FTP/POP/IMAP)",
     "No policy against unencrypted protocols — credentials are observable on the wire.",
     6, "Disable cleartext services or migrate to encrypted equivalents."),
    ("tls_hygiene", "Expired or self-signed TLS certificates",
     "No certificate / PKI governance — TLS trust is unmanaged.",
     3, "Stand up certificate issuance/renewal; replace self-signed/expired certs."),
    ("weak_auth", "Guest / anonymous authentication succeeds",
     "Unauthenticated access is tolerated — access control is not enforced.",
     7, "Disable guest/null sessions; require authenticated, least-privilege access."),
    ("smb_signing", "SMB message signing disabled",
     "No SMB hardening baseline — exposed to NTLM relay and lateral movement.",
     7, "Enable and require SMB signing across the estate."),
]


def _zero_root() -> Dict[str, int]:
    return {}


def compute_systemic_insights(db: Session, project_id: int) -> Dict[str, Any]:
    """Cross-sectional systemic analysis for a project's in-scope hosts.

    Returns ``adopted=False`` when the project has no scoped subnets (the UI
    shows an onboarding state).  Otherwise: ``estate`` summary, ``blind_spots``
    (estate-wide conditions, worst-first), ``segment_outliers`` (subnets whose
    issue density is an outlier), and ``conditions`` (every systemic condition
    with its spread), plus per-subnet ``diagnostic_profiles``.
    """
    now = datetime.now(timezone.utc)

    subnet_meta = _load_subnet_meta(db, project_id)
    if not subnet_meta:
        return {"adopted": False}

    locations = resolve_host_locations(db, project_id, subnet_meta)
    if not locations:
        return {
            "adopted": True, "estate": _empty_estate(),
            "blind_spots": [], "segment_outliers": [],
            "conditions": [], "diagnostic_profiles": [],
        }

    host_subnet: Dict[int, int] = {h: loc["subnet_id"] for h, loc in locations.items()}
    host_site: Dict[int, Optional[int]] = {h: loc["site_id"] for h, loc in locations.items()}
    in_scope: Set[int] = set(locations.keys())
    total_hosts = len(in_scope)
    subnet_hosts: Dict[int, List[int]] = defaultdict(list)
    for h, sid in host_subnet.items():
        subnet_hosts[sid].append(h)
    total_subnets = len(subnet_hosts)
    total_sites = len({s for s in host_site.values() if s is not None})

    # --- per-host os (for EOL) + smb signing posture ----------------------
    host_os: Dict[int, Optional[str]] = {}
    host_ip: Dict[int, Optional[str]] = {}
    host_smb: Dict[int, Optional[str]] = {}
    for hid, os_name, ip, smb_signing in (
        db.query(models.Host.id, models.Host.os_name, models.Host.ip_address, models.Host.smb_signing)
        .filter(models.Host.project_id == project_id)
        .all()
    ):
        if hid in in_scope:
            host_os[hid] = os_name
            host_ip[hid] = ip
            host_smb[hid] = smb_signing

    # --- condition → set(host_ids) ---------------------------------------
    affected: Dict[str, Set[int]] = {k: set() for k, *_ in _CONDITIONS}

    for hid, os_name in host_os.items():
        if match_eol_os(os_name) is not None:
            affected["eol_os"].add(hid)

    for hid, sig in host_smb.items():
        if sig == "disabled":
            affected["smb_signing"].add(hid)

    for hid, port_number in (
        db.query(models.Port.host_id, models.Port.port_number)
        .join(models.Host, models.Port.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            models.Port.state == "open",
            models.Port.port_number.in_(_CLEARTEXT_PORTS),
        )
        .all()
    ):
        if hid in in_scope:
            affected["cleartext_services"].add(hid)

    # TLS: latest observation per (host, url), then judge — mirrors the subnet
    # service so a stale expired cert can't outlive a clean re-observation.
    cert_latest: Dict[tuple, tuple] = {}
    for hid, url, not_after, self_signed, last_seen in (
        db.query(
            WebInterface.host_id, WebInterface.url,
            WebInterface.cert_not_after, WebInterface.cert_self_signed,
            WebInterface.last_seen,
        )
        .filter(
            WebInterface.project_id == project_id,
            WebInterface.host_id.isnot(None),
            or_(
                WebInterface.cert_not_after.isnot(None),
                WebInterface.cert_self_signed.isnot(None),
            ),
        )
        .all()
    ):
        if hid not in in_scope:
            continue
        ls = _normalize_dt(last_seen) or _EPOCH
        prev = cert_latest.get((hid, url))
        if prev is None or ls >= prev[0]:
            cert_latest[(hid, url)] = (ls, not_after, self_signed)
    for (hid, _url), (_ls, not_after, self_signed) in cert_latest.items():
        if cert_issue_from_columns(not_after, self_signed, now):
            affected["tls_hygiene"].add(hid)

    # Weak auth: latest netexec observation per (host, proto, port).
    nxc_latest: Dict[tuple, tuple] = {}
    for hid, proto, port, auth_success, username, discovered_at in (
        db.query(
            NetexecResult.host_id, NetexecResult.protocol, NetexecResult.port,
            NetexecResult.auth_success, NetexecResult.username, NetexecResult.discovered_at,
        )
        .join(models.Host, NetexecResult.host_id == models.Host.id)
        .filter(models.Host.project_id == project_id)
        .all()
    ):
        if hid not in in_scope:
            continue
        d = _normalize_dt(discovered_at) or _EPOCH
        prev = nxc_latest.get((hid, proto, port))
        if prev is None or d >= prev[0]:
            nxc_latest[(hid, proto, port)] = (d, auth_success, username)
    for (hid, _p, _pt), (_d, auth_success, username) in nxc_latest.items():
        if auth_success and _is_weak_user(username):
            affected["weak_auth"].add(hid)

    # --- per-condition spread metrics ------------------------------------
    min_hosts = max(1, round(_SYSTEMIC_HOST_FRACTION * total_hosts))
    conditions_out: List[Dict[str, Any]] = []
    blind_spots: List[Dict[str, Any]] = []
    # subnet → set(condition keys present) for the diagnostic profiles
    subnet_conditions: Dict[int, Set[str]] = defaultdict(set)
    # subnet → count of (condition, host) incidences for density
    subnet_issue_incidences: Dict[int, int] = defaultdict(int)

    for key, label, vector, weight, action in _CONDITIONS:
        hosts = affected[key]
        if not hosts:
            continue
        subnets = {host_subnet[h] for h in hosts}
        sites = {host_site[h] for h in hosts if host_site[h] is not None}
        for h in hosts:
            subnet_conditions[host_subnet[h]].add(key)
            subnet_issue_incidences[host_subnet[h]] += 1
        host_fraction = len(hosts) / total_hosts if total_hosts else 0.0
        systemic_score = round(weight * len(hosts) * (1 + len(subnets) + len(sites)), 1)
        row = {
            "key": key, "label": label, "vector": vector,
            "severity_weight": weight, "recommended_action": action,
            "affected_hosts": len(hosts),
            "host_fraction": round(host_fraction, 3),
            "subnet_spread": len(subnets),
            "site_spread": len(sites),
            "systemic_score": systemic_score,
            "example_ips": [host_ip.get(h) for h in list(hosts)[:5]],
        }
        # Estate-wide blind spot: touches a meaningful host fraction AND spans
        # most sites (or, in a single-site estate, just clears the host floor).
        is_systemic = len(hosts) >= min_hosts and host_fraction >= _SYSTEMIC_HOST_FRACTION
        spans_estate = (
            total_sites <= 1
            or len(sites) >= max(2, round(_BLINDSPOT_SITE_FRACTION * total_sites))
        )
        row["is_blind_spot"] = bool(is_systemic and spans_estate)
        conditions_out.append(row)
        if row["is_blind_spot"]:
            blind_spots.append(row)

    # --- vuln monoculture: one plugin firing across many hosts/subnets ----
    # Exclude info/unknown severity: scanners (esp. Nessus) emit dozens of
    # informational plugins per host — service detection, OS fingerprint, SYN
    # scanner, etc. — that fire on EVERY host.  Counted as "systemic conditions"
    # they (a) bury the actionable signal under info noise and (b) are the bulk
    # of the rows this scan transfers, which is the dominant cost at 40k+ hosts.
    # Systemic analysis is about *actionable* weaknesses, so floor at low.
    plugin_hosts: Dict[str, Set[int]] = defaultdict(set)
    plugin_meta: Dict[str, tuple] = {}
    for hid, plugin_id, severity, title in (
        db.query(Vulnerability.host_id, Vulnerability.plugin_id, Vulnerability.severity, Vulnerability.title)
        .join(models.Host, Vulnerability.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            Vulnerability.plugin_id.isnot(None),
            Vulnerability.severity.notin_([VulnerabilitySeverity.INFO, VulnerabilitySeverity.UNKNOWN]),
        )
        .all()
    ):
        if hid in in_scope:
            plugin_hosts[plugin_id].add(hid)
            plugin_meta[plugin_id] = (severity, title)
    for plugin_id, hosts in plugin_hosts.items():
        if len(hosts) < min_hosts:
            continue
        subnets = {host_subnet[h] for h in hosts}
        sites = {host_site[h] for h in hosts if host_site[h] is not None}
        host_fraction = len(hosts) / total_hosts if total_hosts else 0.0
        if host_fraction < _SYSTEMIC_HOST_FRACTION:
            continue
        spans_estate = total_sites <= 1 or len(sites) >= max(2, round(_BLINDSPOT_SITE_FRACTION * total_sites))
        if not spans_estate:
            continue
        sev, title = plugin_meta[plugin_id]
        sev_label = sev.value if hasattr(sev, "value") else str(sev)
        blind_spots.append({
            "key": f"vuln:{plugin_id}", "label": f"Shared vulnerability: {title}"[:160],
            "vector": "A single exposure replicated estate-wide — one root cause, many hosts.",
            "severity_weight": 8, "recommended_action": "Remediate the shared root cause once across all affected hosts.",
            "affected_hosts": len(hosts), "host_fraction": round(host_fraction, 3),
            "subnet_spread": len(subnets), "site_spread": len(sites),
            "systemic_score": round(8 * len(hosts) * (1 + len(subnets) + len(sites)), 1),
            "example_ips": [host_ip.get(h) for h in list(hosts)[:5]],
            "is_blind_spot": True, "severity": sev_label,
        })

    conditions_out.sort(key=lambda r: -r["systemic_score"])
    blind_spots.sort(key=lambda r: -r["systemic_score"])

    # --- segment outliers: density vs estate median ----------------------
    densities = []
    per_subnet_density: Dict[int, float] = {}
    for sid, hosts in subnet_hosts.items():
        hc = len(hosts)
        dens = (subnet_issue_incidences.get(sid, 0) / hc) if hc else 0.0
        per_subnet_density[sid] = dens
        densities.append(dens)
    median_density = _median(densities)
    segment_outliers: List[Dict[str, Any]] = []
    for sid, hosts in subnet_hosts.items():
        hc = len(hosts)
        dens = per_subnet_density[sid]
        if hc >= _OUTLIER_MIN_HOSTS and median_density > 0 and dens >= _OUTLIER_FACTOR * median_density:
            meta = subnet_meta[sid]
            segment_outliers.append({
                "subnet_id": sid, "cidr": meta["cidr"], "site": meta["site"],
                "host_count": hc,
                "issue_density": round(dens, 3),
                "estate_median_density": round(median_density, 3),
                "times_median": round(dens / median_density, 1),
                "conditions": sorted(subnet_conditions.get(sid, set())),
            })
    segment_outliers.sort(key=lambda r: -r["times_median"])

    # --- diagnostic profiles: co-occurrence → root cause -----------------
    diagnostic_profiles: List[Dict[str, Any]] = []
    for sid, conds in subnet_conditions.items():
        meta = subnet_meta[sid]
        diagnostic_profiles.append({
            "subnet_id": sid, "cidr": meta["cidr"], "site": meta["site"],
            "host_count": len(subnet_hosts.get(sid, [])),
            "conditions": sorted(conds),
            "root_cause": _root_cause(conds),
        })
    diagnostic_profiles.sort(key=lambda r: (-len(r["conditions"]), -r["host_count"]))

    return {
        "adopted": True,
        "estate": {
            "hosts_in_scope": total_hosts,
            "subnets": total_subnets,
            "sites": total_sites,
            "blind_spot_count": len(blind_spots),
        },
        "blind_spots": blind_spots,
        "segment_outliers": segment_outliers,
        "conditions": conditions_out,
        "diagnostic_profiles": diagnostic_profiles,
    }


def _root_cause(conds: Set[str]) -> Dict[str, str]:
    """Map a subnet's co-occurring conditions to a likely management failure."""
    eol = "eol_os" in conds
    pki = "tls_hygiene" in conds
    cred = "weak_auth" in conds
    cleartext = "cleartext_services" in conds
    if eol and cred and (pki or cleartext):
        return {"kind": "abandoned", "text": "Multiple compounding weaknesses — segment looks unmanaged/abandoned."}
    if eol and not (pki or cred or cleartext):
        return {"kind": "patch-gap", "text": "End-of-life systems dominate — no patch/lifecycle programme."}
    if pki and not (eol or cred):
        return {"kind": "no-pki", "text": "Certificate hygiene only — no PKI governance."}
    if cred and not (eol or pki):
        return {"kind": "cred-hygiene", "text": "Weak/guest auth — credential and access-control hygiene."}
    if cleartext and not (eol or pki or cred):
        return {"kind": "flat-network", "text": "Cleartext/legacy services exposed — no hardening baseline."}
    return {"kind": "mixed", "text": "Mixed weaknesses — review the per-condition breakdown."}


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _empty_estate() -> Dict[str, Any]:
    return {"hosts_in_scope": 0, "subnets": 0, "sites": 0, "blind_spot_count": 0}
