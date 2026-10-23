from __future__ import annotations

import ipaddress
import json
import logging
import math
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, NamedTuple, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.db import models

logger = logging.getLogger(__name__)
from app.db.models_vulnerability import (
    Vulnerability,
    VulnerabilitySeverity,
    VulnerabilitySource,
)
from app.services import scan_sightings
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
    announce_scan(db, scan)
    return scan


def announce_scan(db: Session, scan: models.Scan) -> None:
    """Tell the ingestion job which scan this parse is writing (review
    2026-10-01 R1).  Every parser calls it once, right after flushing its
    Scan row and OUTSIDE any record savepoint: under a job the id is stamped
    on the job row and committed with the scan, so an import that is killed
    or fails part-way can be cleaned up.  Without a job it does nothing."""
    from app.services.ingestion_service import note_scan_created

    note_scan_created(db, scan.id)


class ProgressBeat:
    """Heartbeat for a parser's record loop (review 2026-10-01 R6).

    Twelve parsers never called ``report_progress``: one transaction for the
    whole file, row locks held to the end, cancel and timeout never checked,
    and a worker asked to stop was killed and its job reaped 45 minutes later.
    ``tick()`` once per record, OUTSIDE the record's savepoint — the heartbeat
    raises ``ParseFailure`` on cancel / timeout / shutdown, which must stop
    the parse rather than be counted as one bad record, and it COMMITS
    (``update_heartbeat``), which would end a savepoint it ran inside.

    The commit is the only form the heartbeat has.  It is safe for a parser
    whose ORM rows stay attached to the session: they are expired and reload
    on next use.  ``before`` runs just ahead of each heartbeat — a parser that
    writes its host history at the END of the file passes
    ``record_hosts_in_scan`` here, so what a commit makes durable always says
    which hosts this scan created (``delete_partial_scan`` reads that).

    With no active job (a parser driven directly) nothing is committed."""

    def __init__(self, label: str = "records", every: int = 500,
                 before: Optional[Callable[[], None]] = None):
        self.label = label
        self.every = max(1, every)
        self.before = before
        self.count = 0

    def tick(self, n: int = 1) -> None:
        previous = self.count
        self.count += n
        if self.count // self.every == previous // self.every:
            return
        from app.services.ingestion_service import report_progress

        if self.before is not None:
            self.before()
        report_progress(f"{self.count} {self.label}")


def beat_while_reading(items: Iterable[Any], label: str = "records read", every: int = 20000) -> Iterator[Any]:
    """``items``, with a heartbeat every ``every`` of them — for the READ
    phase of a parser that collects the whole file before it writes anything
    (naabu, testssl, dirbuster, masscan, smbmap, rustscan, rdap).

    Without it a large file was read with no heartbeat at all: a cancel or a
    worker shutdown was not noticed until the first write, and a read longer
    than the stale-job threshold got the job reaped while it was still
    working.  The heartbeat commits; that is safe HERE because a read phase
    holds nothing half-built in the session — the scan row is either not
    created yet or already committed by ``announce_scan``.  Never wrap a loop
    that writes inside a record savepoint with this: use ``ProgressBeat``
    between the records instead."""
    beat = ProgressBeat(label, every=every)
    for item in items:
        beat.tick()
        yield item


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
    encoding = tool_text_encoding(raw[:_ENCODING_HEAD_BYTES])
    text = raw.decode(encoding, errors="ignore")
    if encoding.startswith("utf-16"):
        text = text.lstrip("﻿")
    nul_removed = text.count("\x00")
    if nul_removed:
        text = text.replace("\x00", "")
    return ToolText(text=text, encoding=encoding, nul_removed=nul_removed)


#: How much of a file decides its encoding (``tool_text_encoding``).
_ENCODING_HEAD_BYTES = 4096


def tool_text_encoding(head: bytes) -> str:
    """The encoding of a tool's text output, from its first bytes — the ONE
    rule ``read_tool_text`` and ``open_tool_lines`` read a file by: a UTF-16
    byte-order mark, or UTF-16LE without one (ASCII text with every odd byte
    NUL — a PowerShell redirect), else UTF-8."""
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if len(head) >= 16 and head[1::2].count(0) > 0.9 * (len(head) // 2):
        return "utf-16-le"
    return "utf-8"


@contextmanager
def open_tool_lines(
    file_path: str, *, newline: Optional[str] = None, errors: str = "ignore",
) -> Iterator[Iterator[str]]:
    """The lines of a tool's text output, STREAMED (review 2026-10-23,
    R-ING2): decoded by ``tool_text_encoding`` (UTF-16 from PowerShell's
    ``>``), a byte-order mark taken off the first line (Excel's "CSV UTF-8"),
    NUL removed (``without_nul``).  For a parser whose reader takes lines, in
    place of ``read_tool_text``, which holds the whole file — and a copy of it
    for each split — in memory.  ``newline`` is ``open``'s (``''`` for a CSV
    reader)."""
    with open(file_path, "rb") as fh:
        encoding = tool_text_encoding(fh.read(_ENCODING_HEAD_BYTES))
    with open(file_path, "r", encoding=encoding, errors=errors, newline=newline) as handle:
        yield without_nul(_without_leading_bom(handle))


def _without_leading_bom(lines: Iterable[str]) -> Iterator[str]:
    first = True
    for line in lines:
        if first:
            first = False
            line = line.lstrip("﻿")
        yield line


def without_nul(lines: Iterable[str]) -> Iterable[str]:
    """The lines of an open text file with NUL removed, still streamed.

    Review 2026-10-01 R5 — ``read_tool_text`` is for a report read whole; a
    parser that streams a large file line by line (gnmap, masscan list,
    smbmap, nikto) opened it with ``errors="ignore"``, which drops undecodable
    bytes but keeps NUL, and a stored line then failed the import the way
    NetExec's did (PostgreSQL text cannot hold NUL).  Wrapping the handle
    keeps the read streaming."""
    for line in lines:
        yield line.replace("\x00", "") if "\x00" in line else line


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

    ``isolate=True`` writes it as one skippable record (``write_record``):
    on any error it is rolled back and skipped (logged, return None), so one
    malformed observation can't roll back the whole upload — the per-record
    isolation the lower-volume parsers (naabu/amass/dirbuster/rustscan/
    bloodhound) want.  Callers that pass it ignore the return.
    """
    def write():
        return _persist_host_observation_inner(
            dedup_service=dedup_service, scan_id=scan_id, ip_address=ip_address,
            hostname=hostname, state=state, ports=ports, host_data=host_data,
            project_id=project_id,
        )

    if not isolate:
        return write()
    outcome = write_record(dedup_service.db, write, on_rollback=dedup_service.discard_rolled_back_state)
    if outcome.error is not None:
        logger.warning("Skipping host observation %s: %s", ip_address, outcome.error)
        return None
    return outcome.value


@contextmanager
def record_savepoint(db: Session, observed: Optional["ScanHostObservations"] = None,
                     on_rollback: Optional[Callable[[], None]] = None):
    """One input record's writes as a unit (v2.419.0, review 2026-09-25 H3).
    The ONE savepoint a parser opens around a record (review 2026-10-22,
    B1-1); a parser that skips a failed record and carries on calls
    ``write_record``, which is built on it.

    A caller that catches a record's exception and carries on MUST have
    isolated that record: without a savepoint, the failed flush leaves the
    session unusable and the next query — or the final commit — fails the
    whole import that the log said skipped one record (httpx: a
    content_length past 2**31).  On failure the savepoint is rolled back,
    the scan's host notes are restored, ``on_rollback`` drops per-file caches
    that may hold rows from the rolled-back savepoint (the dedup service's
    ``discard_rolled_back_state`` among them, for a parser that writes
    through it), and the exception is re-raised for the caller to count.

    A savepoint an inner frame already rolled back cannot be rolled back
    again; that is not a second failure — the notes are still restored, the
    caches still dropped, and the record's own exception is the one raised
    (nmap and gnmap swallowed it by hand)."""
    sp = db.begin_nested()
    checkpoint = observed.checkpoint() if observed is not None else None
    try:
        yield
        sp.commit()
    except Exception:
        try:
            sp.rollback()
        except Exception as rollback_exc:  # noqa: BLE001 — the record's error is the one to raise
            logger.debug("Record savepoint was already rolled back: %s", rollback_exc)
        if observed is not None:
            observed.restore(checkpoint)
        if on_rollback is not None:
            on_rollback()
        raise


class RecordOutcome(NamedTuple):
    """``write_record``'s answer: what ``write`` returned, or the error the
    record was skipped for (``None`` when it was written)."""
    value: Any = None
    error: Optional[Exception] = None


def write_record(
    db: Session,
    write: Callable[[], Any],
    *,
    observed: Optional["ScanHostObservations"] = None,
    on_rollback: Optional[Callable[[], None]] = None,
    retry_races: bool = False,
) -> RecordOutcome:
    """Write one input record that may be skipped — THE per-record isolation
    of every parser that skips a failed record and carries on (review
    2026-10-22, B1-1: it was written out by hand eight times, and the copies
    had drifted).  ``write`` runs inside ``record_savepoint`` (which rolls the
    record back, restores ``observed`` and calls ``on_rollback``).

    * A cancel, a shutdown or a stall (``ParseFailure``) is never a bad
      record: it is raised, and the import stops (only gnmap and NetExec
      re-raised it; everywhere else it was counted as one skipped record).
    * ``retry_races``: an ``IntegrityError`` or a deadlock (40P01) is a lost
      race with another import of the project, not a bad record — the record
      is written once more, now finding the other import's row (nmap; review
      2026-10-01 R7 and 2026-10-10 S6).  Only for a ``write`` that may run
      twice: everything it wrote is rolled back and ``on_rollback`` must
      leave no per-record state behind.
    * Any other error skips the record: the outcome carries it, for the
      caller to count and say."""
    from sqlalchemy.exc import IntegrityError

    from app.db.session import is_deadlock
    from app.services.ingestion_service import ParseFailure

    attempts = 2 if retry_races else 1
    for attempt in range(1, attempts + 1):
        try:
            with record_savepoint(db, observed, on_rollback):
                value = write()
            return RecordOutcome(value)
        except ParseFailure:
            raise
        except Exception as exc:  # noqa: BLE001 — one record, the caller counts it
            if attempt < attempts and (isinstance(exc, IntegrityError) or is_deadlock(exc)):
                continue
            return RecordOutcome(error=exc)
    raise AssertionError("unreachable")  # pragma: no cover


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
    #: This parse changed the host's DISPLAY name (``Host.hostname``) — the
    #: answer of ``dns_name_service.apply_hostname_candidate``.  Becomes
    #: ``HostScanHistory.hostname_changed`` unless the scan created the host.
    renamed: bool = False


class ProjectHostsByAddress:
    """The project's hosts by address — the address → id map read ONCE per
    import (one statement of two columns), and a host loaded by its primary
    key only when an answer names it.  Shared by the DNS CSV and dnsx parsers
    (second review 2026-10-14, F3: one address query per row or answer,
    inside the import lock).

    Ids, not host objects (third review 2026-10-15, R2): 70,000 held ORM
    hosts were 123 MB, and every heartbeat commit expired all of them
    (~110 ms each — about 22 s per 100,000 DNS records).  The address map is
    about 10 MB, a host the session already holds costs no query, and the
    session holds only the hosts the import touched.

    A host the import CREATES is ``add``-ed as it is made (a later answer of
    the same record may name it — a zone's own A record for its name server)
    and ``forget``-ed when the record that made it rolls back: that host does
    not exist.  A caller that does not forget it is still answered right
    (review 2026-10-22, B1-2 — the web parsers): a host whose row a rollback
    discarded is no longer in the session, is read again by its key, is not
    found, and its address is dropped from the map.

    The one way an import finds a host by address outside the dedup service
    — the DNS parsers, and the web parsers through ``resolve_host_cached``."""

    def __init__(self, db: Session, project_id: int) -> None:
        self._db = db
        self._project_id = project_id
        self._ids: Optional[Dict[str, int]] = None
        # The hosts loaded since the last commit, held so the session (whose
        # identity map is weak) does not read one again for each answer that
        # names it.  A commit expires them all; the first expired one found
        # lets the window go, so this never grows to the project.
        self._loaded: Dict[int, models.Host] = {}

    @property
    def project_id(self) -> int:
        return self._project_id

    def get(self, address: str) -> Optional[models.Host]:
        if self._ids is None:
            self._ids = dict(
                self._db.query(models.Host.ip_address, models.Host.id)
                .filter(models.Host.project_id == self._project_id)
            )
        host_id = self._ids.get(address)
        if host_id is None:
            return None
        host = self._loaded.get(host_id)
        if host is not None:
            state = sa_inspect(host)
            # Expired: a commit (every one of them, so the window goes).
            # Not persistent: a rolled-back savepoint created it.
            if state.expired or not state.persistent:
                self._loaded.clear()
                host = None
        if host is None:
            host = self._db.get(models.Host, host_id)
            if host is None:
                # Its row was rolled back (or deleted): the address has none.
                self._ids.pop(address, None)
                return None
            self._loaded[host_id] = host
        return host

    def add(self, host: Optional[models.Host], address: Optional[str] = None) -> None:
        """Remember ``host`` under ``address`` (default: the spelling it is
        stored in)."""
        if host is not None and self._ids is not None:
            self._ids[address or host.ip_address] = host.id
            self._loaded[host.id] = host

    def forget(self, addresses: Iterable[str]) -> None:
        if self._ids is not None:
            for address in addresses:
                host_id = self._ids.pop(address, None)
                if host_id is not None:
                    self._loaded.pop(host_id, None)


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
    on later notes, a host it renamed stays ``renamed``, and a definite state
    is not replaced by ``None``.

    It also remembers which hosts have something ``record_hosts_in_scan`` has
    not written yet (review 2026-10-01 M2): the web parsers write their
    history ahead of every heartbeat, and re-reading every host of the file on
    each one made a long import quadratic.
    """

    def __init__(self) -> None:
        self._rows: Dict[int, HostObservation] = {}
        self._unwritten: set[int] = set()
        # What each change replaced, in order — (host id, the row before or
        # None, whether it was already unwritten) — so a rolled-back record
        # is undone by walking back to its checkpoint.  A checkpoint used to
        # copy every note so far, once per record: quadratic for the DNS
        # parsers, which take one per row (review 2026-10-14, R4).
        self._undo: List[Tuple[int, Optional[HostObservation], bool]] = []

    def note(
        self,
        host: Optional[models.Host],
        *,
        created: bool,
        state: Optional[str],
        hostname: Optional[str] = None,
        renamed: bool = False,
    ) -> None:
        """``renamed``: this record changed the host's display name — pass
        what ``apply_hostname_candidate`` (or ``resolve_host_cached``'s
        ``renamed``) answered, never a comparison of your own."""
        if host is None or host.id is None:
            return
        prev = self._rows.get(host.id)
        merged = HostObservation(
            host_id=host.id,
            created=created or (prev.created if prev else False),
            state=state if state is not None else (prev.state if prev else None),
            hostname=hostname or (prev.hostname if prev else None) or host.hostname,
            renamed=bool(renamed) or (prev.renamed if prev else False),
        )
        if merged != prev:
            self._undo.append((host.id, prev, host.id in self._unwritten))
            self._rows[host.id] = merged
            self._unwritten.add(host.id)

    def checkpoint(self) -> int:
        """Where the notes stand, to ``restore`` if the record being written
        is rolled back (v2.419.0): a note on a host created inside the
        rolled-back savepoint would reach record_hosts_in_scan with an id that
        no longer exists.  O(1); opaque to the caller: hand it back to
        ``restore`` unchanged.  A checkpoint spans one record — never a
        ``take_unwritten`` (a heartbeat is outside the record's savepoint)."""
        return len(self._undo)

    def restore(self, checkpoint: int) -> None:
        while len(self._undo) > checkpoint:
            host_id, prev, was_unwritten = self._undo.pop()
            if prev is None:
                self._rows.pop(host_id, None)
            else:
                self._rows[host_id] = prev
            if not was_unwritten:
                self._unwritten.discard(host_id)

    def take_unwritten(self) -> List[HostObservation]:
        """The observations noted or changed since the last call, which are
        then considered written."""
        rows = [self._rows[host_id] for host_id in sorted(self._unwritten) if host_id in self._rows]
        self._unwritten = set()
        # No checkpoint spans a write (a heartbeat is outside every record's
        # savepoint): the undo log starts again here, so it is never longer
        # than what one beat noted (second review 2026-10-14, nitpick).
        self._undo.clear()
        return rows

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows.values())

    @property
    def host_ids(self) -> set[int]:
        return set(self._rows)

    @property
    def renamed_host_ids(self) -> set[int]:
        """The noted hosts whose display name this parse changed and that it
        did not itself create here — the hosts ``record_hosts_in_scan`` marks
        ``hostname_changed``.  A parser whose NEW hosts go through the dedup
        service (dnsx, the DNS CSV) subtracts the ids it created, as it does
        for its result sentence: one set, so the sentence and the stored flag
        cannot disagree."""
        return {host_id for host_id, row in self._rows.items() if row.renamed and not row.created}


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

    Safe to call repeatedly (the web parsers call it ahead of every
    heartbeat, so each commit says which hosts the scan created).  Review
    2026-10-01 M2: each call handles only the hosts noted or changed since
    the previous one, and a row that already exists is UPDATED with what the
    file has said about the host since — it used to be skipped, so
    ``state_at_scan`` / ``hostname_at_scan`` froze at the first heartbeat.
    """
    pending = observations.take_unwritten()
    if not pending:
        return
    existing_rows = {
        row.host_id: row
        for row in db.query(models.HostScanHistory).filter(
            models.HostScanHistory.scan_id == scan_id,
            models.HostScanHistory.host_id.in_([obs.host_id for obs in pending]),
        )
    }
    for obs in pending:
        existing = existing_rows.get(obs.host_id)
        if existing is not None:
            # Never downgrade created→updated (same rule as the dedup path),
            # and never replace a recorded value with "not said".
            if obs.created:
                existing.host_created = True
            if obs.state is not None:
                existing.state_at_scan = obs.state
            if obs.hostname:
                existing.hostname_at_scan = obs.hostname
            # Display name changed by this scan: never on the row of the scan
            # that created the host (the dedup service may have written that
            # row — dnsx, the DNS CSV), and never turned back to false.
            if obs.renamed and not existing.host_created:
                existing.hostname_changed = True
            continue
        db.add(
            models.HostScanHistory(
                host_id=obs.host_id,
                scan_id=scan_id,
                host_created=obs.created,
                state_at_scan=obs.state,
                hostname_at_scan=obs.hostname,
                hostname_changed=obs.renamed and not obs.created,
            )
        )


def record_addressed_hosts(
    db: Session, scan_id: int, observations: ScanHostObservations
) -> None:
    """``record_hosts_in_scan`` for a parser whose NEW hosts go through the
    dedup service (dnsx, the DNS CSV): the hosts that already existed and that
    the file addressed get their ``host_scan_history`` row here; a host the
    import created has the one ``find_or_create_host`` wrote.

    Two writers, one row per (host, scan) — ``uq_host_scan``.  The session
    runs ``autoflush=False``, so each writer finds the other's row only once
    it is in the database: the flush before lets ``record_hosts_in_scan``
    find (and update, never downgrading ``host_created``) the row of a host
    this import created and an answer then addressed again; the flush after
    lets the dedup service's lookup find what was written here.

    Call it where ``record_hosts_in_scan`` is called: ahead of each heartbeat
    (``ProgressBeat(before=…)``, outside the record savepoint) and after the
    last record."""
    db.flush()
    record_hosts_in_scan(db, scan_id, observations)
    db.flush()


def dns_import_summary(records: int, addressed: int, created: int, renamed: int) -> str:
    """The one-line result of a DNS import (dnsx, the DNS CSV) for the job's
    progress column: records stored, hosts the answers addressed, and of
    those how many the import created and on how many that already existed
    it changed the display name."""
    parts = [f"{records} DNS record{'s' if records != 1 else ''}"]
    if not addressed:
        parts.append("no hosts addressed")
        return ", ".join(parts)
    parts.append(f"{addressed} host{'s' if addressed != 1 else ''} addressed")
    parts.append(f"{created} created")
    parts.append(f"{renamed} display name{'s' if renamed != 1 else ''} changed")
    return ", ".join(parts)


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


def refresh_reobserved_vulnerability(
    existing: Vulnerability,
    *,
    scan_id: int,
    severity: VulnerabilitySeverity,
    title: Optional[str] = None,
    description: Optional[str] = None,
    solution: Optional[str] = None,
    references: Optional[str] = None,
    cvss_score: Optional[float] = None,
    cvss_vector: Optional[str] = None,
    cve_id: Optional[str] = None,
    plugin_output: Optional[str] = None,
) -> None:
    """Bring a stored scanner row up to date from a later scan's report of
    it.  The one rule for every source (``upsert_vulnerability`` and the
    Nessus path): the severity is always the latest scan's — the scanner
    re-rates its checks, and the queue tiers and critical counts read this
    column — and every other field keeps its stored value when the new
    report carries none.  ``references`` is the stored (JSON) text.

    ``scan_id`` ("first recorded by") never moves; the re-observation lands
    on ``last_seen_scan_id``.  The caller records the sighting
    (``scan_sightings.see`` / ``see_many``) — this helper has no session, and
    the Nessus path writes a batch's sightings in one statement."""
    # Aware UTC; UTCDateTime stores it naive.
    existing.last_seen = datetime.now(timezone.utc)
    # A report with no scan to name (a backfill from a row whose scan is
    # gone) keeps the stored "last seen by".
    if scan_id is not None:
        existing.last_seen_scan_id = scan_id
    existing.severity = severity
    # Title can change across scans for the same plugin_id — keep latest.
    if title:
        existing.title = title
    if cvss_score is not None:
        existing.cvss_score = cvss_score
    if cvss_vector:
        existing.cvss_vector = cvss_vector
    existing.description = description or existing.description
    existing.cve_id = cve_id or existing.cve_id
    existing.solution = solution or existing.solution
    if references:
        existing.references = references
    if plugin_output:
        existing.plugin_output = plugin_output


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
    # Review 2026-10-01 B12 — the CVSS vector the scanner printed (OpenVAS).
    # None leaves a stored vector alone.
    cvss_vector: Optional[str] = None,
) -> Vulnerability:
    cvss_vector = cvss_vector.strip()[:200] if cvss_vector and cvss_vector.strip() else None
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
        refresh_reobserved_vulnerability(
            existing, scan_id=scan_id, severity=severity, title=title,
            description=description, solution=solution,
            references=json.dumps(references) if references else None,
            cvss_score=cvss_score, cvss_vector=cvss_vector, cve_id=cve_id,
            plugin_output=plugin_output,
        )
        if exploitable:
            existing.exploitable = True
        if check_id:
            existing.check_id = check_id
        scan_sightings.see(db, scan_sightings.VULNERABILITY, existing.id, scan_id)
        return existing

    vulnerability = Vulnerability(
        plugin_id=plugin_id,
        title=title,
        description=description,
        severity=severity,
        cvss_score=cvss_score,
        cvss_vector=cvss_vector,
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
    scan_sightings.see(db, scan_sightings.VULNERABILITY, vulnerability.id, scan_id)
    return vulnerability


# --- Shared web-parser host/port resolution (cached) ---------------------
#
# The web parsers (eyewitness / httpx / whatweb / testssl) each resolve a
# record's Host and Port before writing a WebInterface row.  A scan file holds
# many records that share the same host (and port), so a query per record is
# an N+1.  Hosts come from the import's ``ProjectHostsByAddress`` (ids, not
# ORM rows held for the whole file — review 2026-10-22, B1-2); ports are
# memoized in a cache the parser owns for one file parse.


class ResolvedHost(NamedTuple):
    """``resolve_host_cached``'s answer: the row, and whether THIS call created
    it.  ``created`` is the ground truth for "what this scan introduced" and is
    reported once, on the creating call; cache hits answer ``False``.
    ``renamed``: THIS call changed the display name of a host that already
    existed (the answer of ``apply_hostname_candidate``) — hand it to
    ``ScanHostObservations.note(renamed=…)``."""
    host: Optional[models.Host]
    created: bool
    renamed: bool = False


def resolve_host_cached(
    db: Session,
    hosts: ProjectHostsByAddress,
    ip: str,
    *,
    hostname: Optional[str] = None,
) -> ResolvedHost:
    """Find (or create) the project's ``Host`` for ``ip``: from ``hosts``, the
    import's address map, else by one query — an address the map does not
    hold may be stored in another spelling (``HostDeduplicationService
    .host_address``, as ``find_or_create_host`` finds it) or have been added
    by another import since the map was read — else a new host is inserted
    and flushed (the web tool observed it, so it is real) under
    ``normalize_ip``'s spelling.  A value that is no address raises
    (``ValueError``), and the record is skipped.

    On a host that already existed, a newly learned ``hostname`` is offered
    through the one precedence rule.  A record that is rolled back needs no
    eviction: ``hosts`` finds that a host it created is gone."""
    # Display-name writes go through the one precedence rule; a web tool's
    # name for the address is a 'scanner' source (fills an empty hostname or
    # replaces a weaker forward-resolved vhost, never a PTR or operator name).
    from app.services.dns_name_service import apply_hostname_candidate, display_name_candidate

    # A URL / ip:port / address is not a display name (v2.402.0).
    hostname = display_name_candidate(hostname)
    address, written = HostDeduplicationService.host_address(ip)
    host = hosts.get(address)
    if host is None:
        where, first = HostDeduplicationService.address_is(address, written)
        host = (
            db.query(models.Host)
            .filter(models.Host.project_id == hosts.project_id, where)
            .order_by(first)
            .first()
        )
        if host is None:
            host = models.Host(
                ip_address=address, hostname=hostname, state="up", project_id=hosts.project_id,
                hostname_source="scanner" if hostname else None,
            )
            db.add(host)
            db.flush()
            hosts.add(host, address)
            return ResolvedHost(host, True, False)
        hosts.add(host, address)
    renamed = bool(hostname and apply_hostname_candidate(host, hostname, "scanner"))
    return ResolvedHost(host, False, renamed)


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
