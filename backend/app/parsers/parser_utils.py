from __future__ import annotations

import ipaddress
import json
import logging
import math
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, NamedTuple, Optional, Tuple
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
    # v2.333.0 — no start_time here.  This used to stamp the upload time into
    # start_time, so /scans showed "Scanned: <upload time>" for every tool
    # whose output carries no run time, indistinguishable from a real scanner
    # timestamp.  A parser that finds a time in the file sets it (with its
    # source) via ScanClock; otherwise start_time stays NULL and the UI says
    # the file carries no scan time.  created_at is the upload time.
    scan = models.Scan(
        filename=filename,
        tool_name=tool_name,
        scan_type=scan_type,
        command_line=command_line,
        project_id=project_id,
    )
    db.add(scan)
    db.flush()
    return scan


# ---------------------------------------------------------------------------
# Scan time helpers (v2.333.0)
#
# Scan.start_time / end_time are naive columns; Scan.time_source says what a
# naive value means (see models.SCAN_TIME_SOURCES).  Every parser converts a
# tool timestamp through these so "naive = UTC" holds for tool_run /
# tool_records, and a zone-less wall clock is flagged tool_clock instead of
# being silently passed off as UTC.
# ---------------------------------------------------------------------------

_RFC3339_FRACTION = re.compile(r"(\.\d{6})\d+")


def to_utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    """Aware -> the same instant as naive UTC.  Naive values are assumed to
    already be UTC (callers only pass naive values they produced as UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def epoch_to_utc(raw: Any) -> Optional[datetime]:
    """Unix epoch seconds (int/float/str) -> naive UTC, or None.

    Never ``datetime.fromtimestamp`` — that returns the CONTAINER's local
    time, which is UTC only until someone sets TZ."""
    if raw is None or raw == "":
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    # float() accepts "NaN" / "inf"; timedelta then raises ValueError on NaN,
    # which escaped this function and rolled back a whole import over one
    # optional field (v2.333.1).
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    try:
        return datetime(1970, 1, 1) + timedelta(seconds=seconds)
    except (OverflowError, ValueError):
        return None


def parse_rfc3339(raw: Any) -> Optional[datetime]:
    """RFC 3339 / ISO 8601 with an offset or ``Z`` -> AWARE datetime.

    Go tools (httpx, naabu, dnsx) write nanosecond fractions
    (``2026-09-08T10:11:12.123456789Z``), which ``fromisoformat`` rejects;
    the fraction is trimmed to microseconds.  A value with no offset is
    ambiguous and returns None — callers must not guess a zone."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    s = _RFC3339_FRACTION.sub(r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return None
        # Reject instants that parse but can't be expressed in UTC
        # (0001-01-01T00:00:00+01:00) — converting them later overflows.
        dt.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None
    return dt


class ScanClock:
    """Accumulates the scan window from timestamps found in a tool's output.

    ``observe`` takes an aware datetime or a naive UTC one (e.g. from
    ``epoch_to_utc``); ``observe_clock`` takes a zone-less wall clock.  An
    absolute time always beats a wall clock.  ``apply`` writes the window and
    its source onto the Scan, and never overwrites a window the parser set
    from an explicit run start/finish.

    Contract (v2.333.1): observing NEVER raises.  A scan time is optional
    metadata; parsers call ``observe`` outside their per-record error
    handling, so a value that can't be used is dropped here rather than
    rolling back the observations it arrived with.
    """

    def __init__(self, source: str = models.SCAN_TIME_TOOL_RECORDS):
        self.source = source
        self.first: Optional[datetime] = None
        self.last: Optional[datetime] = None
        self._clock_first: Optional[datetime] = None
        self._clock_last: Optional[datetime] = None

    def observe(self, value: Optional[datetime]) -> None:
        try:
            value = to_utc_naive(value)
        except (OverflowError, ValueError, TypeError, AttributeError):
            return
        if value is None:
            return
        if self.first is None or value < self.first:
            self.first = value
        if self.last is None or value > self.last:
            self.last = value

    def observe_clock(self, value: Optional[datetime]) -> None:
        if not isinstance(value, datetime):
            return
        value = value.replace(tzinfo=None)
        if self._clock_first is None or value < self._clock_first:
            self._clock_first = value
        if self._clock_last is None or value > self._clock_last:
            self._clock_last = value

    def apply(self, scan: models.Scan) -> None:
        if scan.start_time is not None:
            return
        if self.first is not None:
            scan.start_time = self.first
            scan.end_time = self.last if self.last and self.last > self.first else None
            scan.time_source = self.source
        elif self._clock_first is not None:
            scan.start_time = self._clock_first
            last = self._clock_last
            scan.end_time = last if last and last > self._clock_first else None
            scan.time_source = models.SCAN_TIME_TOOL_CLOCK


def normalize_ip(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    candidate = value.strip().strip("[]")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


class ToolText(NamedTuple):
    """A tool's text output, decoded, with what reading it had to change."""
    text: str
    encoding: str
    nul_removed: int


def read_tool_text(file_path: str) -> ToolText:
    """Read a text report for a parser whose lines reach the database.

    v2.420.0 (production diagnostics 2026-09-26) — two NetExec captures (1.6
    and 4.5 MB) failed whole with PostgreSQL's "A string literal cannot
    contain NUL (0x00) characters": text columns cannot hold NUL, and the
    stored tool line carried it.  Two sources, handled here:

    * UTF-16 — PowerShell's ``>`` redirect writes UTF-16LE (with a BOM), which
      read as UTF-8 is a NUL after every character.  Decoded as UTF-16, the
      file is ordinary text.
    * stray NUL bytes in otherwise UTF-8 text (a terminal capture, a
      truncated write) — removed, and counted so the import can say so.
    """
    with open(file_path, "rb") as fh:
        raw = fh.read()
    encoding = "utf-8"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    else:
        head = raw[:4096]
        # UTF-16LE without a BOM: ASCII text with every odd byte NUL.
        if len(head) >= 16 and head[1::2].count(0) > 0.9 * (len(head) // 2):
            encoding = "utf-16-le"
    text = raw.decode(encoding, errors="ignore")
    if encoding.startswith("utf-16"):
        text = text.lstrip("﻿")
    nul_removed = text.count("\x00")
    if nul_removed:
        text = text.replace("\x00", "")
    return ToolText(text=text, encoding=encoding, nul_removed=nul_removed)


def extract_first_ip(text: Optional[str]) -> Optional[str]:
    """The address a value names.  A value that IS an address — IPv4 or IPv6,
    as a structured field (naabu `ip`, BloodHound `ipv4`, OpenVAS `<host>`)
    carries it — is taken whole; otherwise the first IPv4 in the text.

    v2.419.0 (review 2026-09-25 H9): only the dotted-IPv4 pattern was tried,
    so `2001:db8::1` in a field that holds nothing else was "no address" and
    IPv6 results were dropped by every parser using this helper."""
    if not text:
        return None
    whole = normalize_ip(str(text))
    if whole:
        return whole
    match = IP_PATTERN.search(text)
    if not match:
        return None
    return normalize_ip(match.group(0))


# "[2001:db8::1]:443" — how tools write an IPv6 endpoint.
_BRACKETED_ENDPOINT = re.compile(r"^\[([0-9A-Fa-f:.]+)\]:(\d{1,5})$")


def parse_host_port_token(token: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    cleaned = token.strip().strip(",")
    if not cleaned:
        return None, None, None

    if "://" in cleaned:
        parsed = urlparse(cleaned)
        host = parsed.hostname
        port = parsed.port
        return normalize_ip(host), port, parsed.scheme or None

    bracketed = _BRACKETED_ENDPOINT.match(cleaned)
    if bracketed:
        ip_address = normalize_ip(bracketed.group(1))
        if ip_address and int(bracketed.group(2)) <= 65535:
            return ip_address, int(bracketed.group(2)), None

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
            dedup_service.discard_rolled_back_state()
            logger.warning("Skipping host observation %s: %s", ip_address, exc)
            return None
    return _persist_host_observation_inner(
        dedup_service=dedup_service, scan_id=scan_id, ip_address=ip_address,
        hostname=hostname, state=state, ports=ports, host_data=host_data,
        project_id=project_id,
    )


@contextmanager
def record_savepoint(db: Session, observed: Optional["ScanHostObservations"] = None,
                     on_rollback: Optional[Callable[[], None]] = None):
    """One input record's writes as a unit (v2.419.0, review 2026-09-25 H3).

    A caller that catches a record's exception and carries on MUST have
    isolated that record: without a savepoint, the failed flush leaves the
    session unusable and the next query — or the final commit — fails the
    whole import that the log said skipped one record (httpx: a
    content_length past 2**31).  On failure the savepoint is rolled back,
    the scan's host notes are restored, ``on_rollback`` drops per-file caches
    that may hold rows from the rolled-back savepoint, and the exception is
    re-raised for the caller to count."""
    sp = db.begin_nested()
    checkpoint = observed.checkpoint() if observed is not None else None
    try:
        yield
        sp.commit()
    except Exception:
        sp.rollback()
        if observed is not None:
            observed.restore(checkpoint)
        if on_rollback is not None:
            on_rollback()
        raise


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

    def checkpoint(self) -> Dict[int, HostObservation]:
        """The notes so far, to ``restore`` if the record being written is
        rolled back (v2.419.0): a note on a host created inside the rolled-back
        savepoint would reach record_hosts_in_scan with an id that no longer
        exists.  Rows are immutable, so a shallow copy suffices."""
        return dict(self._rows)

    def restore(self, checkpoint: Dict[int, HostObservation]) -> None:
        self._rows = dict(checkpoint)

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
    # v2.413.0 — exploitability for the source-agnostic `has:exploit` /
    # `exploitport:` filters (nmap's vulners marks entries is_exploit; the
    # Nessus path sets it in VulnerabilityService).  None leaves it alone; a
    # re-observation only ever raises it.
    exploitable: Optional[bool] = None,
    # v2.415.0 — the misconfiguration-catalog check the row is (its issue key
    # and kind); set by record_misconfig and the scanner mappings.
    check_id: Optional[str] = None,
    # v2.387.0 — the id names a CHECK that reports several distinct results
    # (Nikto 2.6 gives every "Suggested security header missing: X" line the
    # id 013587): the title is part of the identity, or the five headers
    # collapse into one row that keeps the last one's name.
    key_on_title: bool = False,
    # v2.390.0 — per-host evidence (what was requested / seen on THIS host),
    # shown in the inspector as "Scanner output on this host".
    plugin_output: Optional[str] = None,
) -> Vulnerability:
    # v2.387.0 — values are clipped to their columns.  A Nikto message longer
    # than 200 characters (source_plugin_name is String(200)) raised
    # StringDataRightTruncation and failed the whole file.
    title = (title or "")[:500]
    plugin_id = plugin_id[:50] if plugin_id else plugin_id
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
        if key_on_title:
            query = query.filter(Vulnerability.title == title)
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
        # A re-observation without a score keeps the stored one, like the
        # fields below (it used to blank it).
        if cvss_score is not None:
            existing.cvss_score = cvss_score
        existing.description = description or existing.description
        existing.cve_id = cve_id or existing.cve_id
        existing.solution = solution or existing.solution
        if references:
            existing.references = json.dumps(references)
        if plugin_output:
            existing.plugin_output = plugin_output
        if exploitable:
            existing.exploitable = True
        if check_id:
            existing.check_id = check_id
        return existing

    vulnerability = Vulnerability(
        plugin_id=plugin_id,
        title=title,
        description=description,
        severity=severity,
        cvss_score=cvss_score,
        source=source,
        source_plugin_name=title[:200],
        host_id=host_id,
        port_id=port_id,
        name_id=name_id,
        scan_id=scan_id,
        cve_id=cve_id,
        solution=solution,
        references=json.dumps(references) if references else None,
        plugin_output=plugin_output,
        last_seen_scan_id=scan_id,
        exploitable=bool(exploitable),
        check_id=check_id,
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
    from app.services.dns_name_service import apply_hostname_candidate, display_name_candidate

    # A URL / ip:port / address is not a display name (v2.402.0).
    hostname = display_name_candidate(hostname)
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
