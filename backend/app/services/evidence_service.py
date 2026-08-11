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

from typing import Any, Dict, List, Optional

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


def _distinct_host_count(db: Session, project_id: int, model, *filters) -> int:
    """Distinct hosts in the project matching a related model + filters."""
    q = (
        db.query(func.count(func.distinct(model.host_id)))
        .join(models.Host, model.host_id == models.Host.id)
        .filter(models.Host.project_id == project_id)
    )
    for f in filters:
        q = q.filter(f)
    return int(q.scalar() or 0)


def _domain(key: str, label: str, note: str, assessed: int, eligible: int) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "note": note,
        "coverage": ratio_metric(assessed, eligible).model_dump(),
    }


def compute_evidence_coverage(db: Session, project_id: int) -> Dict[str, Any]:
    """Per-domain evidence coverage for a project, plus contributing tools and
    data-quality signals."""
    total_hosts = int(
        db.query(func.count(models.Host.id))
        .filter(models.Host.project_id == project_id)
        .scalar()
        or 0
    )

    # --- Port discovery & service/version -------------------------------------
    hosts_with_ports = _distinct_host_count(db, project_id, models.Port)
    hosts_with_service = _distinct_host_count(
        db, project_id, models.Port, models.Port.service_name.isnot(None),
    )

    # --- Vulnerability assessment ---------------------------------------------
    hosts_with_vuln = _distinct_host_count(db, project_id, Vulnerability)

    # --- Web / TLS ------------------------------------------------------------
    web_port_filter = or_(
        models.Port.port_number.in_(_WEB_PORTS),
        models.Port.service_name.ilike("http%"),
    )
    hosts_web_eligible = _distinct_host_count(db, project_id, models.Port, web_port_filter)
    hosts_with_web = int(
        db.query(func.count(func.distinct(models.WebInterface.host_id)))
        .filter(models.WebInterface.project_id == project_id,
                models.WebInterface.host_id.isnot(None))
        .scalar()
        or 0
    )

    # --- Authentication / SMB / AD --------------------------------------------
    hosts_auth_eligible = _distinct_host_count(
        db, project_id, models.Port, models.Port.port_number.in_(_AUTH_PORTS),
    )
    # Assessed = a host with an SMB-signing observation OR any NetExec result.
    netexec_host_ids = db.query(NetexecResult.host_id)
    hosts_with_auth = int(
        db.query(func.count(models.Host.id))
        .filter(
            models.Host.project_id == project_id,
            or_(
                models.Host.smb_signing.isnot(None),
                models.Host.id.in_(netexec_host_ids),
            ),
        )
        .scalar()
        or 0
    )

    # --- Validation / retest ---------------------------------------------------
    hosts_with_finding = int(
        db.query(func.count(func.distinct(FindingHost.host_id)))
        .join(Finding, FindingHost.finding_id == Finding.id)
        .filter(Finding.project_id == project_id)
        .scalar()
        or 0
    )
    hosts_validated = int(
        db.query(func.count(func.distinct(TestPlanEntry.host_id)))
        .join(TestExecutionResult, TestExecutionResult.entry_id == TestPlanEntry.id)
        .join(models.Host, TestPlanEntry.host_id == models.Host.id)
        .filter(
            models.Host.project_id == project_id,
            TestExecutionResult.status == TestExecutionStatus.EXECUTED.value,
        )
        .scalar()
        or 0
    )

    domains: List[Dict[str, Any]] = [
        _domain("port_discovery", "Port discovery",
                "Hosts with at least one port recorded — a port scan reached them, not just an IP listing.",
                hosts_with_ports, total_hosts),
        _domain("service_detection", "Service / version detection",
                "Of hosts with ports, how many carry an identified service or version (vs. bare open/closed).",
                hosts_with_service, hosts_with_ports),
        _domain("vuln_assessment", "Vulnerability assessment",
                "Hosts with at least one vulnerability finding from a scanner (Nessus / OpenVAS / Nikto).",
                hosts_with_vuln, total_hosts),
        _domain("web_tls", "Web / TLS",
                "Of hosts exposing a web port, how many have a fingerprinted web interface.",
                hosts_with_web, hosts_web_eligible),
        _domain("auth_smb_ad", "Authentication / SMB / AD",
                "Of hosts exposing SMB/LDAP/Kerberos, how many have an SMB-signing or NetExec observation.",
                hosts_with_auth, hosts_auth_eligible),
        _domain("validation", "Validation / retest",
                "Of hosts carrying a finding, how many have an executed test result confirming it.",
                hosts_validated, hosts_with_finding),
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
