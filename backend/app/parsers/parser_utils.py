from __future__ import annotations

import ipaddress
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Iterable, NamedTuple, Optional, Tuple
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


class HostObservation(NamedTuple):
    """What one parse observed about one host — the inputs
    ``HostScanHistory`` needs, stated by the parser rather than read back from
    the mutable inventory row."""
    host_id: int
    created: bool
    state: Optional[str]
    hostname: Optional[str]


class ScanHostObservations:
    """Accumulates per-host observations over one file parse for
    ``record_hosts_in_scan`` (v2.332.1).

    Explicit on purpose.  The first cut of this (v2.332.0) had the writer read
    ``Host.state`` off the cached ORM row and a hidden created-marker attribute
    off the same object — so a known-DOWN host that answered an HTTP probe was
    recorded as observed *down*, and creation attribution depended on an
    instance attribute surviving until another helper read it.  Here the parser
    says what it saw; the inventory row is never consulted.

    ``note`` never downgrades: a host created by this parse stays ``created``
    on later notes, and a definite state is not replaced by ``None``.
    """

    def __init__(self) -> None:
        self._rows: Dict[int, HostObservation] = {}

    def note(
        self,
        host: Optional[models.Host],
        *,
        created: bool,
        state: Optional[str],
        hostname: Optional[str] = None,
    ) -> None:
        if host is None or host.id is None:
            return
        prev = self._rows.get(host.id)
        self._rows[host.id] = HostObservation(
            host_id=host.id,
            created=created or (prev.created if prev else False),
            state=state if state is not None else (prev.state if prev else None),
            hostname=hostname or (prev.hostname if prev else None) or host.hostname,
        )

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows.values())

    @property
    def host_ids(self) -> set[int]:
        return set(self._rows)


def record_hosts_in_scan(
    db: Session, scan_id: int, observations: ScanHostObservations
) -> None:
    """Record HostScanHistory rows for what this scan observed.

    v2.12.2 — extracted from httpx parser after recon session #3 surfaced
    that web-fingerprint parsers (httpx, eyewitness, future nikto) skip
    this step, breaking ``/agent/recon/summary`` host counts (the query
    joins through host_scan_history).  Idempotent: existing pairs are
    skipped silently.

    v2.332.0 — writes the same snapshot fields the dedup path writes
    (``host_created``, ``state_at_scan``, ``hostname_at_scan``).  Before this
    the web parsers wrote bare membership rows, so every httpx / whatweb /
    eyewitness / testssl scan reported 0 new hosts and 0 up hosts even on a
    first-ever import, and the notification path filed every host as
    "changed".  v2.332.1 — inputs are the parser's explicit observations
    (see ``ScanHostObservations``), not the inventory row.

    Call after the parser has flushed its primary rows so the host ids are
    stable.  An empty accumulator is a no-op (file with no resolved hosts).
    """
    if not observations:
        return
    host_ids = observations.host_ids
    existing_rows = {
        row.host_id: row
        for row in db.query(models.HostScanHistory).filter(
            models.HostScanHistory.scan_id == scan_id,
            models.HostScanHistory.host_id.in_(host_ids),
        )
    }
    for obs in observations:
        existing = existing_rows.get(obs.host_id)
        if existing is not None:
            # Never downgrade created→updated (same rule as the dedup path).
            if obs.created:
                existing.host_created = True
            continue
        db.add(
            models.HostScanHistory(
                host_id=obs.host_id,
                scan_id=scan_id,
                host_created=obs.created,
                state_at_scan=obs.state,
                hostname_at_scan=obs.hostname,
            )
        )


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
    # v2.323.0 — the named endpoint the finding was observed at (web scanners
    # test a name, not a bare address).  Stamped on create; filled on update
    # when the existing row has none.
    name_id: Optional[int] = None,
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
    # v2.324.0 — the named endpoint is part of the finding's identity: the
    # same nikto check on a.example.com and b.example.com behind one address
    # is two findings.  A named finding never absorbs into an unnamed
    # (host/service-level) row, and vice versa.
    if name_id is None:
        query = query.filter(Vulnerability.name_id.is_(None))
    else:
        query = query.filter(Vulnerability.name_id == name_id)

    existing = query.first()
    if existing:
        existing.last_seen = datetime.utcnow()
        # v2.332.0 — scan_id is "first recorded by" and never moves; the
        # re-observation lands on last_seen_scan_id.
        existing.last_seen_scan_id = scan_id
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
        name_id=name_id,
        scan_id=scan_id,
        cve_id=cve_id,
        solution=solution,
        references=json.dumps(references) if references else None,
        last_seen_scan_id=scan_id,
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


class ResolvedHost(NamedTuple):
    """``resolve_host_cached``'s answer: the row, and whether THIS call created
    it.  ``created`` is the ground truth for "what this scan introduced" and is
    reported once, on the creating call; cache hits answer ``False``."""
    host: Optional[models.Host]
    created: bool


def resolve_host_cached(
    db: Session,
    project_id: Optional[int],
    ip: str,
    host_cache: Dict[str, Any],
    *,
    hostname: Optional[str] = None,
    create: bool = True,
) -> ResolvedHost:
    """Look up (or create) a ``Host`` by ``(ip, project)``, memoized in
    ``host_cache`` (``ip -> Host``).  On a cache hit, still enrich a missing
    ``hostname`` if one was newly learned — matches the per-record behaviour the
    web parsers relied on.  ``create=True`` inserts + flushes a missing host
    (the web tool observed it, so it's real)."""
    # Display-name writes go through the one precedence rule; a web tool's
    # name for the address is a 'scanner' source (fills an empty hostname or
    # replaces a weaker forward-resolved vhost, never a PTR or operator name).
    from app.services.dns_name_service import apply_hostname_candidate

    if ip in host_cache:
        host = host_cache[ip]
        if host is not None and hostname:
            apply_hostname_candidate(host, hostname, "scanner")
        return ResolvedHost(host, False)

    host = (
        db.query(models.Host)
        .filter(models.Host.ip_address == ip, models.Host.project_id == project_id)
        .first()
    )
    created = False
    if host is None and create:
        host = models.Host(
            ip_address=ip, hostname=hostname, state="up", project_id=project_id,
            hostname_source="scanner" if hostname else None,
        )
        db.add(host)
        db.flush()
        created = True
    elif host is not None and hostname:
        apply_hostname_candidate(host, hostname, "scanner")

    host_cache[ip] = host
    return ResolvedHost(host, created)


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
