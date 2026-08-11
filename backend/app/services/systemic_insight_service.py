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
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.services.host_condition_sets import (
    cert_issue_host_ids,
    cleartext_host_ids,
    eol_os_host_ids,
    smb_unsigned_host_ids,
    weak_auth_host_ids,
    weak_tls_host_ids,
)
from app.services.subnet_insight_service import (
    _EPOCH,
    _load_subnet_meta,
    _normalize_dt,
    resolve_host_locations,
)
from app.services.pattern_families import (
    classify,
    family_for_condition,
    family_for_vuln,
    FAMILIES,
)
from app.schemas.metric import ratio_metric

# A weakness must touch at least this fraction of in-scope hosts before it's
# considered a *systemic* pattern rather than a handful of incidents.
_SYSTEMIC_HOST_FRACTION = 0.10
# ...and at least this many hosts in absolute terms.  A single affected host is
# an incident, not a pattern — the 10%-fraction floor alone rounds down to 1 on
# tiny estates, so a one-host project used to satisfy "systemic".
_SYSTEMIC_MIN_HOSTS_ABS = 2
# "Estate-wide blind spot" is a claim about the whole estate; it's meaningless
# below this many in-scope hosts (a 1–3 host lab can't have an estate-wide
# pattern — it surfaces conditions, never blind spots).
_BLINDSPOT_MIN_ESTATE_HOSTS = 4
# To be promoted to an estate-wide "blind spot", a condition must additionally
# span at least this fraction of the sites that exist (when >1 site exists).
_BLINDSPOT_SITE_FRACTION = 0.6
# A segment is an outlier when its issue density is at least this multiple of
# the estate median density (guarded by a small host floor so tiny subnets with
# one issue don't dominate).
_OUTLIER_FACTOR = 2.0
_OUTLIER_MIN_HOSTS = 3
# When the estate median density is zero (most subnets clean), the ratio rule
# can never fire — precisely the estate where one bad subnet IS the story.  Fall
# back to an absolute density floor in that case: >= 1.0 means the subnet's hosts
# each carry, on average, at least one weakness — a stark contrast to a clean estate.
_OUTLIER_ABS_DENSITY = 1.0


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
    ("weak_tls", "Weak TLS protocols (SSLv2 / SSLv3 / TLS 1.0 / 1.1)",
     "Deprecated TLS versions are offered — downgrade / interception risk; no modern-TLS baseline.",
     4, "Disable SSLv2/SSLv3/TLS 1.0/1.1; require TLS 1.2+ across the estate."),
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

    # --- per-host ip (for the example_ips evidence on each condition) -----
    host_ip: Dict[int, Optional[str]] = {}
    for hid, ip in (
        db.query(models.Host.id, models.Host.ip_address)
        .filter(models.Host.project_id == project_id)
        .all()
    ):
        if hid in in_scope:
            host_ip[hid] = ip

    # --- condition → set(host_ids), restricted to the in-scope estate -----
    # The per-condition judgments (EOL regex, SMB posture, cleartext ports,
    # latest-observation cert/auth) live in host_condition_sets so the /hosts
    # DSL drill-down (has:eol / has:smb_unsigned / has:cleartext /
    # has:cert_issue / has:weak_auth) resolves the SAME hosts this view counts.
    affected: Dict[str, Set[int]] = {
        "eol_os": eol_os_host_ids(db, project_id) & in_scope,
        "smb_signing": smb_unsigned_host_ids(db, project_id) & in_scope,
        "cleartext_services": cleartext_host_ids(db, project_id) & in_scope,
        "tls_hygiene": cert_issue_host_ids(db, project_id, now) & in_scope,
        "weak_tls": weak_tls_host_ids(db, project_id) & in_scope,
        "weak_auth": weak_auth_host_ids(db, project_id) & in_scope,
    }

    # --- per-condition spread metrics ------------------------------------
    min_hosts = max(_SYSTEMIC_MIN_HOSTS_ABS, round(_SYSTEMIC_HOST_FRACTION * total_hosts))
    # A blind spot is an estate-level claim — suppress it entirely on estates too
    # small to generalise from (conditions still surface; only the promotion is gated).
    estate_large_enough = total_hosts >= _BLINDSPOT_MIN_ESTATE_HOSTS
    conditions_out: List[Dict[str, Any]] = []
    blind_spots: List[Dict[str, Any]] = []
    # condition key -> its classification, for the per-family worst-of rollup.
    cond_class: Dict[str, str] = {}
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
        fam = family_for_condition(key)
        row = {
            "key": key, "label": label, "vector": vector,
            "severity_weight": weight, "recommended_action": action,
            "affected_hosts": len(hosts),
            "host_fraction": round(host_fraction, 3),
            "subnet_spread": len(subnets),
            "site_spread": len(sites),
            "systemic_score": systemic_score,
            "example_ips": [host_ip.get(h) for h in list(hosts)[:5]],
            "family": fam.key if fam else None,
            "family_label": fam.label if fam else None,
        }
        # Spread classification (isolated / recurring / estate_wide). estate_wide
        # is the old blind spot: a meaningful host fraction AND spanning most
        # sites (or, in a single-site estate, just clearing the host floor), on
        # an estate big enough to generalise from.
        is_systemic = len(hosts) >= min_hosts and host_fraction >= _SYSTEMIC_HOST_FRACTION
        spans_estate = (
            total_sites <= 1
            or len(sites) >= max(2, round(_BLINDSPOT_SITE_FRACTION * total_sites))
        )
        classification = classify(
            is_systemic=is_systemic, spans_estate=spans_estate,
            estate_large_enough=estate_large_enough,
        )
        row["classification"] = classification
        cond_class[key] = classification
        # Derived: single source of truth is `classification`. Kept for the
        # existing consumers (the _gather_signals blind-spot pass, the frontend).
        row["is_blind_spot"] = classification == "estate_wide"
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
        if not estate_large_enough or len(hosts) < min_hosts:
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
        vuln_fam = family_for_vuln()
        # A monoculture only reaches this list after clearing the estate-wide
        # gates above, so its classification is always estate_wide.
        blind_spots.append({
            "key": f"vuln:{plugin_id}", "label": f"Shared vulnerability: {title}"[:160],
            "vector": "A single exposure replicated estate-wide — one root cause, many hosts.",
            "severity_weight": 8, "recommended_action": "Remediate the shared root cause once across all affected hosts.",
            "affected_hosts": len(hosts), "host_fraction": round(host_fraction, 3),
            "subnet_spread": len(subnets), "site_spread": len(sites),
            "systemic_score": round(8 * len(hosts) * (1 + len(subnets) + len(sites)), 1),
            "example_ips": [host_ip.get(h) for h in list(hosts)[:5]],
            "is_blind_spot": True, "classification": "estate_wide", "severity": sev_label,
            "family": vuln_fam.key, "family_label": vuln_fam.label,
        })

    # --- technology monoculture: one versioned technology estate-wide --------
    # Analogous to the vuln monoculture, over the web-fingerprint technologies
    # blob (httpx / whatweb). A shared *versioned* technology (e.g. "nginx
    # 1.14.0", "Apache Tomcat 7.0.56") concentrated across the estate is a single
    # patchable flaw with a large blast radius. Require a version token (a digit)
    # to exclude ubiquitous version-less entries ("Bootstrap", "PHP") that would
    # otherwise flag as noise — the actionable target is a specific version to
    # upgrade. Python-side bucketing (no JSON WHERE/GROUP BY), exactly like the
    # vuln monoculture reads typed rows and buckets — so no typed technologies
    # table is required to emit this family.
    # Latest observation per (host, url) wins — a technology seen on an EARLIER
    # scan that a later scan no longer reports (e.g. nginx 1.14 after the host
    # was upgraded to 1.24) must NOT keep counting as current, exactly as the
    # cert / weak-TLS conditions retire stale observations (host_condition_sets).
    latest_tech: Dict[Tuple[int, Optional[str]], Tuple[Any, Any]] = {}
    for hid, url, technologies, last_seen in (
        db.query(
            models.WebInterface.host_id,
            models.WebInterface.url,
            models.WebInterface.technologies,
            models.WebInterface.last_seen,
        )
        .filter(
            models.WebInterface.project_id == project_id,
            models.WebInterface.host_id.isnot(None),
            models.WebInterface.technologies.isnot(None),
        )
        .all()
    ):
        if hid not in in_scope:
            continue
        ls = _normalize_dt(last_seen) or _EPOCH
        prev = latest_tech.get((hid, url))
        if prev is None or ls >= prev[0]:
            latest_tech[(hid, url)] = (ls, technologies)

    tech_hosts: Dict[str, Set[int]] = defaultdict(set)
    for (hid, _url), (_ls, technologies) in latest_tech.items():
        if not isinstance(technologies, list):
            continue
        for tech in technologies:
            name = str(tech).strip()
            if not name or not any(ch.isdigit() for ch in name):
                continue  # require a version token — skip version-less libraries
            tech_hosts[name[:160]].add(hid)
    tech_fam = FAMILIES["technology_monoculture"]
    for tech_name, hosts in tech_hosts.items():
        if not estate_large_enough or len(hosts) < min_hosts:
            continue
        subnets = {host_subnet[h] for h in hosts}
        sites = {host_site[h] for h in hosts if host_site[h] is not None}
        host_fraction = len(hosts) / total_hosts if total_hosts else 0.0
        if host_fraction < _SYSTEMIC_HOST_FRACTION:
            continue
        spans_estate = total_sites <= 1 or len(sites) >= max(2, round(_BLINDSPOT_SITE_FRACTION * total_sites))
        if not spans_estate:
            continue
        blind_spots.append({
            "key": f"tech:{tech_name}", "label": f"Technology monoculture: {tech_name}"[:180],
            "vector": "One technology/version concentrated across the estate — a single flaw exposes many hosts.",
            "severity_weight": 5, "recommended_action": "Diversify, or standardise on a patched version of the concentrated technology.",
            "affected_hosts": len(hosts), "host_fraction": round(host_fraction, 3),
            "subnet_spread": len(subnets), "site_spread": len(sites),
            "systemic_score": round(5 * len(hosts) * (1 + len(subnets) + len(sites)), 1),
            "example_ips": [host_ip.get(h) for h in list(hosts)[:5]],
            "is_blind_spot": True, "classification": "estate_wide",
            "family": tech_fam.key, "family_label": tech_fam.label,
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
        if hc < _OUTLIER_MIN_HOSTS:
            continue
        # Ratio rule when there's a non-zero baseline to compare against;
        # otherwise (a mostly-clean estate) an absolute-density floor, so the
        # one genuinely bad subnet isn't silently suppressed by a zero median.
        if median_density > 0:
            is_outlier = dens >= _OUTLIER_FACTOR * median_density
        else:
            is_outlier = dens >= _OUTLIER_ABS_DENSITY
        if not is_outlier:
            continue
        meta = subnet_meta[sid]
        segment_outliers.append({
            "subnet_id": sid, "cidr": meta["cidr"], "site": meta["site"],
            "host_count": hc,
            "issue_density": round(dens, 3),
            "estate_median_density": round(median_density, 3),
            # None when there's no non-zero baseline — an absolute-floor outlier
            # has no meaningful "×median". The UI shows the density instead.
            "times_median": round(dens / median_density, 1) if median_density > 0 else None,
            "conditions": sorted(subnet_conditions.get(sid, set())),
        })
    segment_outliers.sort(key=lambda r: (r["times_median"] is None, -(r["times_median"] or r["issue_density"])))

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

    family_matrix = _build_family_site_matrix(
        affected, host_site, in_scope, subnet_meta,
    )
    family_summary = _build_family_summary(
        affected, host_subnet, host_site, cond_class, total_hosts,
    )

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
        "family_matrix": family_matrix,
        "family_summary": family_summary,
    }


_CLASS_RANK = {"isolated": 0, "recurring": 1, "estate_wide": 2}


def _build_family_summary(
    affected: Dict[str, Set[int]],
    host_subnet: Dict[int, int],
    host_site: Dict[int, Optional[int]],
    cond_class: Dict[str, str],
    total_hosts: int,
) -> List[Dict[str, Any]]:
    """Per pattern-family rollup — the Patterns page's primary rows.

    Aggregates the per-host conditions that share a family: union of affected
    hosts, distinct subnets/sites touched, and the worst spread classification
    among the family's member conditions. Carries the family's root-cause
    hypothesis and recommended program-level control (from the taxonomy).
    Worst-first (estate-wide before recurring before isolated).
    """
    fam_members: Dict[str, List[str]] = defaultdict(list)
    fam_hosts: Dict[str, Set[int]] = defaultdict(set)
    for cond_key, hosts in affected.items():
        if not hosts:
            continue
        fam = family_for_condition(cond_key)
        if fam is None:
            continue
        fam_members[fam.key].append(cond_key)
        fam_hosts[fam.key] |= hosts

    out: List[Dict[str, Any]] = []
    for fam_key, hosts in fam_hosts.items():
        fam = FAMILIES[fam_key]
        subnets = {host_subnet[h] for h in hosts}
        sites = {host_site[h] for h in hosts if host_site[h] is not None}
        worst = max(
            (cond_class.get(c, "isolated") for c in fam_members[fam_key]),
            key=lambda c: _CLASS_RANK[c],
        )
        out.append({
            "family": fam_key,
            "family_label": fam.label,
            "root_cause_hypothesis": fam.root_cause_hypothesis,
            "recommended_control": fam.recommended_control,
            "conditions": sorted(fam_members[fam_key]),
            "affected_hosts": len(hosts),
            "host_fraction": round(len(hosts) / total_hosts, 3) if total_hosts else 0.0,
            "subnet_spread": len(subnets),
            "site_spread": len(sites),
            "classification": worst,
        })
    out.sort(key=lambda r: (-_CLASS_RANK[r["classification"]], -r["affected_hosts"]))
    return out


def _build_family_site_matrix(
    affected: Dict[str, Set[int]],
    host_site: Dict[int, Optional[int]],
    in_scope: Set[int],
    subnet_meta: Dict[int, Dict[str, Any]],
) -> Dict[str, Any]:
    """Condition-family × site matrix — the Overview heatmap.

    Rows are pattern families (each carrying the per-host conditions that roll up
    to it); columns are sites (plus an 'unassigned' column when in-scope hosts
    have no site). Each cell is a Metric: numerator = hosts in this site affected
    by any of the family's conditions, denominator = in-scope hosts in this site
    (Phase 2 uses the full site population as the assessed denominator; per-domain
    eligibility refines this in Phase 4). ``drilldown_filter`` carries the family's
    condition keys + the site so the frontend can open exactly those hosts.
    """
    # site_id -> label (from subnet metadata; a site may span several subnets).
    site_label: Dict[int, str] = {}
    for meta in subnet_meta.values():
        sid, name = meta.get("site_id"), meta.get("site")
        if sid is not None and name:
            site_label[sid] = name

    # Columns: hosts per segment (site_id or None -> the 'unassigned' bucket).
    UNASSIGNED = "unassigned"
    seg_hosts: Dict[str, Set[int]] = defaultdict(set)
    for hid in in_scope:
        sid = host_site.get(hid)
        seg_hosts[str(sid) if sid is not None else UNASSIGNED].add(hid)
    # Named sites first (by population desc), unassigned last.
    def _seg_sort(key: str):
        return (key == UNASSIGNED, -len(seg_hosts[key]))
    segment_keys = sorted(seg_hosts.keys(), key=_seg_sort)
    segments = [
        {
            "key": k,
            "label": site_label.get(int(k)) if k != UNASSIGNED else "Unassigned",
            "assessed": len(seg_hosts[k]),
        }
        for k in segment_keys
    ]
    # A named site whose label is missing (site_id with no metadata name) falls
    # back to a stable placeholder so a column never renders blank.
    for s in segments:
        if s["label"] is None:
            s["label"] = f"Site {s['key']}"

    # Rows: group the per-host condition affected-sets by family.
    family_conditions: Dict[str, List[str]] = defaultdict(list)
    family_hosts: Dict[str, Set[int]] = defaultdict(set)
    family_label: Dict[str, str] = {}
    for cond_key, hosts in affected.items():
        fam = family_for_condition(cond_key)
        if fam is None:
            continue
        family_conditions[fam.key].append(cond_key)
        family_hosts[fam.key] |= hosts
        family_label[fam.key] = fam.label

    rows: List[Dict[str, Any]] = []
    for fam_key, fam_hosts in family_hosts.items():
        if not fam_hosts:
            continue
        conds = sorted(family_conditions[fam_key])
        cells = []
        for seg in segments:
            seg_set = seg_hosts[seg["key"]]
            hit = len(fam_hosts & seg_set)
            cell = ratio_metric(
                hit, seg["assessed"],
                drilldown_filter={
                    "conditions": conds,
                    "site": seg["label"] if seg["key"] != UNASSIGNED else None,
                },
            ).model_dump()
            cell["segment"] = seg["key"]
            cells.append(cell)
        rows.append({
            "family": fam_key,
            "family_label": family_label[fam_key],
            "conditions": conds,
            "affected_total": len(fam_hosts),
            "cells": cells,
        })
    # Worst-first: families affecting the most hosts at the top.
    rows.sort(key=lambda r: -r["affected_total"])

    return {"segments": segments, "rows": rows}


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
