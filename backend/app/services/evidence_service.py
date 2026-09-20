"""Evidence coverage — "can we trust the posture conclusions?"

The posture surface reports what's wrong (findings, systemic patterns) and where.
This service answers a different, load-bearing question: **how much of the
estate has actually been assessed, per assessment domain?** A confident-looking
posture built on a discovery-only scan is not trustworthy — you can't conclude
"no SMB signing problems" from a scan that never touched SMB.

For each domain we compute an *eligibility* denominator (hosts where the domain
is applicable — e.g. only hosts with an SMB/LDAP port are eligible for AD/auth
assessment) and an *assessed* numerator (hosts that actually have evidence in
that domain). The ratio is the coverage; the gap is the blind spot in the
evidence, distinct from a blind spot in the estate.

Eligibility is the Phase-1 work that was deferred to here, its consuming phase:
building it earlier would have been infrastructure with no reader.

Universe = all hosts in the project (not just scoped-subnet hosts) — evidence
coverage matters even before subnets are scoped, and this page must work when
the systemic surface can't run.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import Vulnerability
from app.db.models_findings import Finding, FindingHost
from app.db.models_confidence import NetexecResult
from app.db.models_agent import TestPlanEntry, TestExecutionResult, TestExecutionStatus
from app.schemas.metric import ratio_metric

# Ports that make a host *eligible* for a domain's assessment.
_WEB_PORTS = {80, 443, 8080, 8443, 8000, 8888, 8081, 4443, 9443, 8008}
_AUTH_PORTS = {139, 445, 389, 636, 3268, 3269, 88}  # SMB / LDAP / GC / Kerberos


def _domain(key: str, label: str, note: str, assessed: int, eligible: int) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "note": note,
        "coverage": ratio_metric(assessed, eligible).model_dump(),
        # The step that closes this domain's gap (GAP_ACTIONS, defined below —
        # resolved at call time).
        "action": GAP_ACTIONS[key],
    }


# Assessment domains, in display order.  ``label`` is what the Evidence page and
# the heatmap's "no <label> evidence" state show; ``note`` explains the ratio.
EVIDENCE_DOMAINS: List[Dict[str, str]] = [
    {"key": "port_discovery", "label": "Port discovery",
     "note": "Hosts with at least one port recorded — a port scan reached them, not just an IP listing."},
    {"key": "service_detection", "label": "Service / version detection",
     "note": "Of hosts with ports, how many carry an identified service or version (vs. bare open/closed)."},
    {"key": "os_detection", "label": "OS identification",
     "note": "Of hosts with ports, how many have a fingerprinted operating system — end-of-life is judged from this name."},
    {"key": "vuln_assessment", "label": "Vulnerability assessment",
     "note": "Hosts a vulnerability scanner (Nessus / OpenVAS) covered — including those it found clean — or that carry any scanner observation."},
    {"key": "web_tls", "label": "Web / TLS",
     "note": "Of hosts exposing a web port, how many have a fingerprinted web interface."},
    {"key": "auth_smb_ad", "label": "Authentication / SMB / AD",
     "note": "Of hosts exposing SMB/LDAP/Kerberos, how many have an SMB-signing or NetExec observation."},
    {"key": "validation", "label": "Validation / retest",
     "note": "Of hosts carrying a finding, how many have an executed test result confirming it."},
]
DOMAIN_LABELS: Dict[str, str] = {d["key"]: d["label"] for d in EVIDENCE_DOMAINS}


# Tools whose run against a host IS a vulnerability assessment of it, whatever
# they reported (v2.372.0).  Matched case-insensitively on ``scans.tool_name``
# ("Nessus" is stored capitalised).  Nikto is deliberately absent: it assesses
# one web server, not the host.
VULN_SCANNER_TOOLS = ("nessus", "openvas")


def vuln_scanned_filter():
    """``scans`` predicate: this scan came from a host vulnerability scanner."""
    return func.lower(models.Scan.tool_name).in_(VULN_SCANNER_TOOLS)


def _vuln_scanned_host_ids(db: Session, project_id: int) -> Set[int]:
    """Hosts a vulnerability scanner observed — assessed even with no rows.

    Before this, "assessed" meant "has a vulnerability row", so a host the
    scanner covered and found clean read as never assessed — and with
    severity-0 plugins skipped at ingest, a clean host has no rows at all."""
    return {
        hid for (hid,) in (
            db.query(models.HostScanHistory.host_id)
            .join(models.Scan, models.HostScanHistory.scan_id == models.Scan.id)
            .filter(models.Scan.project_id == project_id, vuln_scanned_filter())
            .distinct().all()
        )
    }


def _host_ids(db: Session, project_id: int, model, *filters) -> Set[int]:
    """Distinct host ids in the project matching a related model + filters."""
    q = (
        db.query(model.host_id)
        .join(models.Host, model.host_id == models.Host.id)
        .filter(models.Host.project_id == project_id, model.host_id.isnot(None))
    )
    for f in filters:
        q = q.filter(f)
    return {hid for (hid,) in q.distinct().all()}


def eligible_host_ids(db: Session, project_id: int) -> Dict[str, Set[int]]:
    """Per assessment domain: the hosts the domain is *applicable* to (the
    coverage denominator).  Shared by ``compute_evidence_coverage`` and the
    condition heatmap so both read the same population."""
    all_hosts = {
        hid for (hid,) in db.query(models.Host.id).filter(models.Host.project_id == project_id).all()
    }
    with_ports = _host_ids(db, project_id, models.Port)
    web_port_filter = or_(
        models.Port.port_number.in_(_WEB_PORTS),
        models.Port.service_name.ilike("http%"),
    )
    with_finding = {
        hid for (hid,) in (
            db.query(FindingHost.host_id)
            .join(Finding, FindingHost.finding_id == Finding.id)
            .filter(Finding.project_id == project_id, FindingHost.host_id.isnot(None))
            .distinct().all()
        )
    }
    return {
        "port_discovery": all_hosts,
        "service_detection": with_ports,
        "os_detection": with_ports,
        "vuln_assessment": all_hosts,
        "web_tls": _host_ids(db, project_id, models.Port, web_port_filter),
        "auth_smb_ad": _host_ids(db, project_id, models.Port, models.Port.port_number.in_(_AUTH_PORTS)),
        "validation": with_finding,
    }


def assessed_host_ids(db: Session, project_id: int) -> Dict[str, Set[int]]:
    """Per assessment domain: the hosts that actually carry evidence in that
    domain (the coverage numerator).  A host in this set has been *checked*
    for the domain — its absence from a weakness set then means "checked and
    clean", not "never looked".  The heatmap's assessed denominator is built
    from these sets, restricted to each site's in-scope hosts."""
    netexec_host_ids = db.query(NetexecResult.host_id)
    with_auth = {
        hid for (hid,) in (
            db.query(models.Host.id)
            .filter(
                models.Host.project_id == project_id,
                or_(models.Host.smb_signing.isnot(None), models.Host.id.in_(netexec_host_ids)),
            ).all()
        )
    }
    with_os = {
        hid for (hid,) in (
            db.query(models.Host.id)
            .filter(models.Host.project_id == project_id,
                    models.Host.os_name.isnot(None), models.Host.os_name != "")
            .all()
        )
    }
    with_web = {
        hid for (hid,) in (
            db.query(models.WebInterface.host_id)
            .filter(models.WebInterface.project_id == project_id,
                    models.WebInterface.host_id.isnot(None))
            .distinct().all()
        )
    }
    validated = {
        hid for (hid,) in (
            db.query(TestPlanEntry.host_id)
            .join(TestExecutionResult, TestExecutionResult.entry_id == TestPlanEntry.id)
            .join(models.Host, TestPlanEntry.host_id == models.Host.id)
            .filter(models.Host.project_id == project_id,
                    TestExecutionResult.status == TestExecutionStatus.EXECUTED.value)
            .distinct().all()
        )
    }
    return {
        "port_discovery": _host_ids(db, project_id, models.Port),
        "service_detection": _host_ids(db, project_id, models.Port, models.Port.service_name.isnot(None)),
        "os_detection": with_os,
        # A scanner's run over the host counts; so does any vulnerability row
        # (nikto / testssl / a manual import carry them without such a scan).
        "vuln_assessment": _host_ids(db, project_id, Vulnerability) | _vuln_scanned_host_ids(db, project_id),
        "web_tls": with_web,
        "auth_smb_ad": with_auth,
        "validation": validated,
    }


# What closes each domain's gap: a collection step (run a tool against the
# hosts, upload the output) or a planning step (the evidence exists; the
# finding has not been validated).  Shown beside the gap list on the
# Evidence page (v2.348.0; design review item 4).
GAP_ACTIONS: Dict[str, Dict[str, str]] = {
    "port_discovery": {"kind": "collect", "text": "Port-scan these hosts (e.g. nmap -sV) and upload the result."},
    "service_detection": {"kind": "collect", "text": "Re-scan these hosts with version detection (nmap -sV) so services are identified, not guessed from the port."},
    "os_detection": {"kind": "collect", "text": "Re-scan these hosts with OS detection (nmap -O) so end-of-life can be judged."},
    "vuln_assessment": {"kind": "collect", "text": "Run a vulnerability scan (Nessus / OpenVAS) against these hosts and upload the export."},
    "web_tls": {"kind": "collect", "text": "Probe these hosts' web ports with httpx and testssl.sh and upload the JSON."},
    "auth_smb_ad": {"kind": "collect", "text": "Enumerate these hosts with netexec (SMB signing, shares) and upload the output."},
    "validation": {"kind": "plan", "text": "These hosts carry findings nobody has tested — put them on a test plan."},
}


# The Evidence matrix's extra column: hosts outside every scoped subnet.  The
# Overview grid cannot show them (it is about scoped segments); Evidence covers
# every host in the project, so here they are a column, never a silent omission.
UNMAPPED_SEGMENT = "unmapped"


def evidence_segments(db: Session, project_id: int) -> Dict[str, Any]:
    """The Evidence matrix's columns: the SAME disjoint segments as the Overview
    grid (``subnet_insight_service.group_hosts_into_segments`` — sites, or
    most-specific subnets when the project defines no site) plus ``unmapped``.
    Returns ``group_by``, ``keys`` (display order), ``labels``, ``hosts``."""
    from app.services.subnet_insight_service import (
        group_hosts_into_segments, resolve_host_locations,
    )

    all_hosts = {
        hid for (hid,) in db.query(models.Host.id).filter(models.Host.project_id == project_id).all()
    }
    locations = resolve_host_locations(db, project_id)
    grouping = group_hosts_into_segments(locations)
    hosts: Dict[str, Set[int]] = {k: set(v) for k, v in grouping["hosts"].items()}
    labels: Dict[str, str] = dict(grouping["labels"])
    keys: List[str] = list(grouping["keys"])
    unmapped = all_hosts - set(locations)
    if unmapped:
        hosts[UNMAPPED_SEGMENT] = unmapped
        labels[UNMAPPED_SEGMENT] = "Outside scoped subnets"
        keys.append(UNMAPPED_SEGMENT)
    return {"group_by": grouping["group_by"], "keys": keys, "labels": labels, "hosts": hosts}


def _evidence_matrix(
    segments: Dict[str, Any], eligible: Dict[str, Set[int]], assessed: Dict[str, Set[int]],
) -> Dict[str, Any]:
    """Domain × segment: per cell, the hosts the domain applies to, how many
    carry its evidence, and the ``gap`` between them.  Three states and no
    more — assessed, not assessed, not applicable (``eligible == 0``).  A project
    is one assessment window: evidence does not go "stale" inside it."""
    rows = []
    for d in EVIDENCE_DOMAINS:
        el, done = eligible[d["key"]], assessed[d["key"]]
        cells = []
        for key in segments["keys"]:
            seg_el = el & segments["hosts"][key]
            seg_done = len(seg_el & done)
            cells.append({
                "segment": key, "eligible": len(seg_el),
                "assessed": seg_done, "gap": len(seg_el) - seg_done,
            })
        rows.append({"domain": d["key"], "label": d["label"], "cells": cells})
    return {
        "group_by": segments["group_by"],
        "segments": [
            {"key": k, "label": segments["labels"][k], "hosts": len(segments["hosts"][k])}
            for k in segments["keys"]
        ],
        "rows": rows,
    }


def evidence_gap_hosts(
    db: Session, project_id: int, domain: str, limit: int = 200, segment: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """The hosts a domain applies to that carry no evidence in it — the
    coverage gap as a list the operator can act on, not just a ratio.

    Returns None for an unknown domain OR an unknown ``segment``.  ``segment``
    (a key from the Evidence matrix) narrows the list to one cell, so a cell's
    ``gap`` count opens exactly its hosts.  ``ports`` carries the open ports
    that made the host eligible (the web or auth ports), so the list reads
    as endpoints, not addresses.
    """
    if domain not in DOMAIN_LABELS:
        return None
    eligible = eligible_host_ids(db, project_id)[domain]
    assessed = assessed_host_ids(db, project_id)[domain]
    segment_label: Optional[str] = None
    if segment is not None:
        segments = evidence_segments(db, project_id)
        if segment not in segments["hosts"]:
            return None
        eligible = eligible & segments["hosts"][segment]
        segment_label = segments["labels"][segment]
    gap_ids = sorted(eligible - assessed)
    total = len(gap_ids)
    chosen = gap_ids[:limit]
    hosts = (
        db.query(models.Host.id, models.Host.ip_address, models.Host.hostname)
        .filter(models.Host.id.in_(chosen))
        .all()
        if chosen else []
    )
    port_filter = None
    if domain == "web_tls":
        port_filter = or_(models.Port.port_number.in_(_WEB_PORTS), models.Port.service_name.ilike("http%"))
    elif domain == "auth_smb_ad":
        port_filter = models.Port.port_number.in_(_AUTH_PORTS)
    ports_by_host: Dict[int, List[int]] = {}
    if port_filter is not None and chosen:
        for hid, port in (
            db.query(models.Port.host_id, models.Port.port_number)
            .filter(models.Port.host_id.in_(chosen), models.Port.state == "open", port_filter)
            .distinct()
            .all()
        ):
            ports_by_host.setdefault(hid, []).append(port)
    by_ip = sorted(hosts, key=lambda h: [int(x) if x.isdigit() else 0 for x in h.ip_address.split(".")] if "." in h.ip_address else [0])
    return {
        "domain": domain,
        "label": DOMAIN_LABELS[domain],
        "segment": segment,
        "segment_label": segment_label,
        "total": total,
        "items": [
            {
                "host_id": h.id,
                "ip_address": h.ip_address,
                "hostname": h.hostname,
                "ports": sorted(ports_by_host.get(h.id, [])),
            }
            for h in by_ip
        ],
        "action": GAP_ACTIONS[domain],
    }


def compute_evidence_coverage(db: Session, project_id: int) -> Dict[str, Any]:
    """Per-domain evidence coverage for a project, plus contributing tools and
    data-quality signals.  Counts are the sizes of the shared per-host sets
    (``eligible_host_ids`` / ``assessed_host_ids``) so this page and the
    condition heatmap can never disagree about who was assessed."""
    eligible = eligible_host_ids(db, project_id)
    assessed = assessed_host_ids(db, project_id)
    total_hosts = len(eligible["port_discovery"])

    domains: List[Dict[str, Any]] = [
        _domain(d["key"], d["label"], d["note"],
                len(assessed[d["key"]] & eligible[d["key"]]), len(eligible[d["key"]]))
        for d in EVIDENCE_DOMAINS
    ]

    # --- Contributing tools (project-wide) ------------------------------------
    tool_rows = (
        db.query(models.Scan.tool_name, func.count(models.Scan.id))
        .filter(models.Scan.project_id == project_id, models.Scan.tool_name.isnot(None))
        .group_by(models.Scan.tool_name)
        .order_by(func.count(models.Scan.id).desc())
        .all()
    )
    contributing_tools = [{"tool": t, "scans": int(c)} for t, c in tool_rows]

    # --- Data quality ---------------------------------------------------------
    scan_count = int(
        db.query(func.count(models.Scan.id))
        .filter(models.Scan.project_id == project_id)
        .scalar()
        or 0
    )
    parse_errors_unresolved = int(
        db.query(func.count(models.ParseError.id))
        .filter(models.ParseError.project_id == project_id,
                models.ParseError.status == "unresolved")
        .scalar()
        or 0
    )

    return {
        "total_hosts": total_hosts,
        "domains": domains,
        # Where the gaps ARE (v2.374.0): the project totals above say a domain
        # is 60% covered; this says which segment holds the other 40%.
        "matrix": _evidence_matrix(evidence_segments(db, project_id), eligible, assessed),
        "contributing_tools": contributing_tools,
        "data_quality": {
            "scans": scan_count,
            "parse_errors_unresolved": parse_errors_unresolved,
        },
    }


def has_minimum_assessment(db: Session, project_id: int) -> bool:
    """Cheap gate for the posture label: does the estate have *any* meaningful
    assessment beyond bare host discovery? True when at least one host carries a
    detected service or a vulnerability finding. A discovery-only estate (masscan
    found ports, nothing characterised them) returns False, so posture reads
    'insufficient_evidence' rather than a reassuring 'no urgent signals'.
    """
    has_service = (
        db.query(models.Port.id)
        .join(models.Host, models.Port.host_id == models.Host.id)
        .filter(models.Host.project_id == project_id, models.Port.service_name.isnot(None))
        .first()
    )
    if has_service is not None:
        return True
    has_vuln = (
        db.query(Vulnerability.id)
        .join(models.Host, Vulnerability.host_id == models.Host.id)
        .filter(models.Host.project_id == project_id)
        .first()
    )
    return has_vuln is not None
