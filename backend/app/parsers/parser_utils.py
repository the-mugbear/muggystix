from __future__ import annotations

import ipaddress
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.db import models

logger = logging.getLogger(__name__)
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
    isolate: bool = False,
) -> Optional[Tuple[models.Host, Dict[Tuple[int, str], models.Port]]]:
    """Persist one host observation (+ its ports).

    ``isolate=True`` wraps the write in a SAVEPOINT and skips (rollback +
    return None) on any error, so one malformed observation can't roll back
    the whole upload — the per-record isolation the lower-volume parsers
    (naabu/amass/dirbuster) want.  Callers that pass it ignore the return.
    """
    if isolate:
        sp = dedup_service.db.begin_nested()
        try:
            result = _persist_host_observation_inner(
                dedup_service=dedup_service, scan_id=scan_id, ip_address=ip_address,
                hostname=hostname, state=state, ports=ports, host_data=host_data,
                project_id=project_id,
            )
            sp.commit()
            return result
        except Exception as exc:  # noqa: BLE001 — isolate one bad observation
            sp.rollback()
            logger.warning("Skipping host observation %s: %s", ip_address, exc)
            return None
    return _persist_host_observation_inner(
        dedup_service=dedup_service, scan_id=scan_id, ip_address=ip_address,
        hostname=hostname, state=state, ports=ports, host_data=host_data,
        project_id=project_id,
    )


def _persist_host_observation_inner(
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
    # Onboarding seam for exploitability: this shared helper does NOT set
    # `Vulnerability.exploitable` today — only the Nessus path
    # (VulnerabilityService) does.  To let another scanner (e.g. Qualys) feed the
    # source-agnostic `has:exploit` / `exploitport:` filters, add an
    # `exploitable: bool = False` param here, set it on the row below, and have
    # that parser compute it (à la nessus_parser._is_exploitable).
) -> Vulnerability:
    query = db.query(Vulnerability).filter(
        Vulnerability.host_id == host_id,
        Vulnerability.source == source,
    )
    if plugin_id:
        # A plugin/NVT id is a stable identifier — dedup on it and treat the
        # title as an UPDATABLE attribute.  Keying on title too (the old
        # behaviour) wrote a duplicate when the same NVT reported a slightly
        # different name across rows (e.g. OpenVAS name vs the "OpenVAS
        # finding" fallback).  Matches the Nessus path, which keys on plugin_id.
        query = query.filter(Vulnerability.plugin_id == plugin_id)
    else:
        # No stable id — fall back to the title as the discriminator.
        query = query.filter(Vulnerability.title == title)
    if port_id is None:
        query = query.filter(Vulnerability.port_id.is_(None))
    else:
        query = query.filter(Vulnerability.port_id == port_id)

    existing = query.first()
    if existing:
        existing.last_seen = datetime.utcnow()
        existing.scan_id = scan_id
        existing.severity = severity
        # Title can change across scans for the same plugin_id — keep latest.
        if title:
            existing.title = title
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


# --- Shared web-parser host/port resolution (cached) ---------------------
#
# The web parsers (eyewitness / httpx / whatweb) each resolve a record's Host
# and Port by query before writing a WebInterface row.  A scan file holds many
# records that share the same host (and port), so doing that per-record is an
# N+1.  These helpers memoize the lookups in caches the parser owns for the
# duration of one file parse, collapsing repeats to a dict hit.  eyewitness had
# its own copy of this; these are the single shared version.


def resolve_host_cached(
    db: Session,
    project_id: Optional[int],
    ip: str,
    host_cache: Dict[str, Any],
    *,
    hostname: Optional[str] = None,
    create: bool = True,
) -> Optional[models.Host]:
    """Look up (or create) a ``Host`` by ``(ip, project)``, memoized in
    ``host_cache`` (``ip -> Host``).  On a cache hit, still enrich a missing
    ``hostname`` if one was newly learned — matches the per-record behaviour the
    web parsers relied on.  ``create=True`` inserts + flushes a missing host
    (the web tool observed it, so it's real)."""
    if ip in host_cache:
        host = host_cache[ip]
        if host is not None and hostname and not host.hostname:
            host.hostname = hostname
        return host

    host = (
        db.query(models.Host)
        .filter(models.Host.ip_address == ip, models.Host.project_id == project_id)
        .first()
    )
    if host is None and create:
        host = models.Host(
            ip_address=ip, hostname=hostname, state="up", project_id=project_id,
        )
        db.add(host)
        db.flush()
    elif host is not None and hostname and not host.hostname:
        host.hostname = hostname

    host_cache[ip] = host
    return host


def resolve_port_cached(
    db: Session,
    host: Optional[models.Host],
    port: Optional[int],
    port_cache: Dict[Tuple[int, int], Any],
    *,
    protocol: str = "tcp",
) -> Optional[models.Port]:
    """Look up a ``Port`` by ``(host_id, port_number, protocol)``, memoized in
    ``port_cache`` (``(host_id, port) -> Port|None``).  Misses are cached too —
    the web parsers never create Port rows, so an absent port stays absent for
    the file."""
    if not (host and port):
        return None
    key = (host.id, port)
    if key in port_cache:
        return port_cache[key]
    port_row = (
        db.query(models.Port)
        .filter(
            models.Port.host_id == host.id,
            models.Port.port_number == port,
            models.Port.protocol == protocol,
        )
        .first()
    )
    port_cache[key] = port_row
    return port_row
