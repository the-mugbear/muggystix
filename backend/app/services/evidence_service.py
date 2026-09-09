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
     "note": "Hosts with at least one vulnerability finding from a scanner (Nessus / OpenVAS / Nikto)."},
    {"key": "web_tls", "label": "Web / TLS",
     "note": "Of hosts exposing a web port, how many have a fingerprinted web interface."},
    {"key": "auth_smb_ad", "label": "Authentication / SMB / AD",
     "note": "Of hosts exposing SMB/LDAP/Kerberos, how many have an SMB-signing or NetExec observation."},
    {"key": "validation", "label": "Validation / retest",
     "note": "Of hosts carrying a finding, how many have an executed test result confirming it."},
]
DOMAIN_LABELS: Dict[str, str] = {d["key"]: d["label"] for d in EVIDENCE_DOMAINS}


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
        "vuln_assessment": _host_ids(db, project_id, Vulnerability),
        "web_tls": with_web,
        "auth_smb_ad": with_auth,
        "validation": validated,
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
