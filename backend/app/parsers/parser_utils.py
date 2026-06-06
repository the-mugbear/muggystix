from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import (
    Vulnerability,
    VulnerabilitySeverity,
    VulnerabilitySource,
)
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.subnet_correlation import SubnetCorrelationService


IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def ensure_scan(
    db: Session,
    *,
    filename: str,
    tool_name: str,
    scan_type: str,
    command_line: Optional[str] = None,
    project_id: Optional[int] = None,
) -> models.Scan:
    scan = models.Scan(
        filename=filename,
        tool_name=tool_name,
        scan_type=scan_type,
        command_line=command_line,
        start_time=datetime.utcnow(),
        project_id=project_id,
    )
    db.add(scan)
    db.flush()
    return scan


def normalize_ip(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    candidate = value.strip().strip("[]")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def extract_first_ip(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = IP_PATTERN.search(text)
    if not match:
        return None
    return normalize_ip(match.group(0))


def parse_host_port_token(token: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    cleaned = token.strip().strip(",")
    if not cleaned:
        return None, None, None

    if "://" in cleaned:
        parsed = urlparse(cleaned)
        host = parsed.hostname
        port = parsed.port
        return normalize_ip(host), port, parsed.scheme or None

    if cleaned.count(":") == 1:
        host_part, port_part = cleaned.rsplit(":", 1)
        ip_address = normalize_ip(host_part)
        if ip_address and port_part.isdigit():
            return ip_address, int(port_part), None

    return extract_first_ip(cleaned), None, None


def persist_host_observation(
    *,
    dedup_service: HostDeduplicationService,
    scan_id: int,
    ip_address: str,
    hostname: Optional[str] = None,
    state: str = "up",
    ports: Optional[Iterable[Dict[str, Any]]] = None,
    host_data: Optional[Dict[str, Any]] = None,
    project_id: Optional[int] = None,
) -> Tuple[models.Host, Dict[Tuple[int, str], models.Port]]:
    payload = {
        "hostname": hostname,
        "state": state,
    }
    if host_data:
        payload.update(host_data)

    host = dedup_service.find_or_create_host(ip_address, scan_id, payload, project_id=project_id)
    port_map: Dict[Tuple[int, str], models.Port] = {}
    for port in ports or []:
        port_number = port.get("port_number")
        if not port_number:
            continue
        protocol = str(port.get("protocol", "tcp")).lower()
        persisted = dedup_service.find_or_create_port(
            host.id,
            scan_id,
            {
                "port_number": int(port_number),
                "protocol": protocol,
                "state": port.get("state", "open"),
                "service_name": port.get("service_name"),
                "service_product": port.get("service_product"),
                "service_version": port.get("service_version"),
                "service_extrainfo": port.get("service_extrainfo"),
            },
        )
        port_map[(persisted.port_number, persisted.protocol)] = persisted

    return host, port_map


def correlate_scan(db: Session, scan_id: int) -> None:
    SubnetCorrelationService(db).batch_correlate_scan_hosts_to_subnets(scan_id)


def record_hosts_in_scan(db: Session, scan_id: int, host_ids: set[int]) -> None:
    """Record HostScanHistory rows for the given (scan, host) pairs.

    v2.12.2 — extracted from httpx parser after recon session #3 surfaced
    that web-fingerprint parsers (httpx, eyewitness, future nikto) skip
    this step, breaking ``/agent/recon/summary`` host counts (the query
    joins through host_scan_history).  Idempotent: existing pairs are
    skipped silently.

    Call after the parser has flushed its primary rows so the host_ids
    are stable.  Pass an empty set for a no-op (file with no resolved
    hosts).
    """
    if not host_ids:
        return
    existing_pairs = {
        (row.host_id, row.scan_id)
        for row in db.query(models.HostScanHistory).filter(
            models.HostScanHistory.scan_id == scan_id,
            models.HostScanHistory.host_id.in_(host_ids),
        )
    }
    for host_id in host_ids:
        if (host_id, scan_id) in existing_pairs:
            continue
        db.add(models.HostScanHistory(host_id=host_id, scan_id=scan_id))


def map_numeric_severity(score: Optional[float]) -> VulnerabilitySeverity:
    if score is None:
        return VulnerabilitySeverity.UNKNOWN
    if score >= 9.0:
        return VulnerabilitySeverity.CRITICAL
    if score >= 7.0:
        return VulnerabilitySeverity.HIGH
    if score >= 4.0:
        return VulnerabilitySeverity.MEDIUM
    if score > 0:
        return VulnerabilitySeverity.LOW
    return VulnerabilitySeverity.INFO


def map_text_severity(value: Optional[str]) -> VulnerabilitySeverity:
    if not value:
        return VulnerabilitySeverity.UNKNOWN
    lowered = value.strip().lower()
    mapping = {
        "critical": VulnerabilitySeverity.CRITICAL,
        "high": VulnerabilitySeverity.HIGH,
        "medium": VulnerabilitySeverity.MEDIUM,
        "moderate": VulnerabilitySeverity.MEDIUM,
        "low": VulnerabilitySeverity.LOW,
        "info": VulnerabilitySeverity.INFO,
        "informational": VulnerabilitySeverity.INFO,
        "log": VulnerabilitySeverity.INFO,
    }
    if lowered in mapping:
        return mapping[lowered]
    try:
        return map_numeric_severity(float(lowered))
    except ValueError:
        return VulnerabilitySeverity.UNKNOWN


def upsert_vulnerability(
    *,
    db: Session,
    host_id: int,
    scan_id: int,
    source: VulnerabilitySource,
    title: str,
    severity: VulnerabilitySeverity,
    plugin_id: Optional[str] = None,
    port_id: Optional[int] = None,
    description: Optional[str] = None,
    cvss_score: Optional[float] = None,
    cve_id: Optional[str] = None,
    solution: Optional[str] = None,
    references: Optional[list[str]] = None,
) -> Vulnerability:
    query = db.query(Vulnerability).filter(
        Vulnerability.host_id == host_id,
        Vulnerability.source == source,
        Vulnerability.title == title,
    )
    if plugin_id:
        query = query.filter(Vulnerability.plugin_id == plugin_id)
    if port_id is None:
        query = query.filter(Vulnerability.port_id.is_(None))
    else:
        query = query.filter(Vulnerability.port_id == port_id)

    existing = query.first()
    if existing:
        existing.last_seen = datetime.utcnow()
        existing.scan_id = scan_id
        existing.severity = severity
        existing.cvss_score = cvss_score
        existing.description = description or existing.description
        existing.cve_id = cve_id or existing.cve_id
        existing.solution = solution or existing.solution
        if references:
            existing.references = json.dumps(references)
        return existing

    vulnerability = Vulnerability(
        plugin_id=plugin_id,
        title=title,
        description=description,
        severity=severity,
        cvss_score=cvss_score,
        source=source,
        source_plugin_name=title,
        host_id=host_id,
        port_id=port_id,
        scan_id=scan_id,
        cve_id=cve_id,
        solution=solution,
        references=json.dumps(references) if references else None,
    )
    db.add(vulnerability)
    db.flush()
    return vulnerability
