"""
dnsx (ProjectDiscovery) JSON / JSONL parser — v2.88.0.

Closes #44 — operators wanted "resolve this list of IPs against this
list of DNS servers, store the answers, see which resolver said
what."  Rather than build an in-app resolver (which would force
BlueStick's host to have outbound DNS, breaking restricted-network
deployments), we lean on dnsx the way the rest of the stack leans on
nmap / masscan / httpx / etc. — the operator runs the tool terminal-
side, BlueStick parses and persists the output.

Example operator invocation::

    dnsx -j -resp -l ips.txt -r resolvers.txt -ptr -a -aaaa \\
         -cname -mx -ns -txt -o dnsx-output.json

Output shape (one JSON object per line for ``-j`` mode)::

    {"host":"example.com","a":["93.184.216.34"],"resolver":["1.1.1.1:53"],
     "status_code":"NOERROR","ttl":86400,"timestamp":"..."}
    {"host":"10.0.0.5","ptr":["mail.internal"],"resolver":["8.8.8.8:53"]}
    {"host":"example.com","mx":["10 mail.example.com"],"resolver":["..."]}

For each record we walk every supported DNS record-type field present
and persist one ``DNSRecord`` row per (record_type, domain, value,
resolver_name) tuple.  PTR records additionally feed the host
inventory via ``persist_host_observation`` so a successful reverse
lookup auto-populates ``Host.hostname`` — mirroring the existing
``DNSParser._parse_csv_file`` behaviour for the PTR case.

v2.89.0 (#44.1) — resolver attribution is now first-class: the
``DNSRecord.resolver_name`` column stores the DNS server that
produced each row.  The same A record answered by 1.1.1.1 AND
8.8.8.8 now produces two rows (one per resolver) so the analytical
query "show me records resolver A returned that resolver B didn't"
is a one-line filter.  The per-ingest summary (resolver hit counts,
NXDOMAIN tally) still rides on ``last_parse_stats.warnings`` for the
IngestionJob row, but it's now derivable from the column data too.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import time
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, NamedTuple, Optional

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import VulnerabilitySource
from app.services.misconfig_checks import record_misconfig
from app.parsers.parser_utils import (
    ProgressBeat,
    correlate_scan,
    ensure_scan,
    ScanClock,
    parse_rfc3339,
)
from app.parsers.streaming_json import iter_json_records
from app.services.dns_name_service import (
    ObservationCache,
    apply_hostname_candidate,
    record_observation,
)
from app.services.host_deduplication_service import HostDeduplicationService

logger = logging.getLogger(__name__)


def _parse_timestamp(raw: Any) -> Optional[datetime]:
    """dnsx emits ``timestamp`` as RFC 3339 (``2026-09-08T10:11:12.123Z``).
    Returns an aware datetime or None — a bad stamp is not a bad record.

    v2.333.1 — parser_utils.parse_rfc3339 is the only rule.  This used to
    fall back to reading a value WITHOUT an offset as UTC; since 2.333.0 that
    invented instant also became the scan window, labelled tool_records and
    shown as absolute.  dnsx always writes an offset, so a zone-less value is
    foreign or hand-edited: it is dropped, the observation takes the ingest
    time (record_observation's default, as for every timeless parser), and
    it contributes nothing to the scan window."""
    return parse_rfc3339(raw)


# Record-type fields dnsx surfaces and the canonical record_type
# string we persist.  Keep this list and the detector in
# content_detection.looks_like_dnsx in sync.
_RECORD_TYPE_FIELDS = (
    ("a", "A"),
    ("aaaa", "AAAA"),
    ("cname", "CNAME"),
    ("mx", "MX"),
    ("ns", "NS"),
    ("txt", "TXT"),
    ("soa", "SOA"),
    # RV-7 — additional record types dnsx emits with the matching flags
    # (-srv, -caa, -any, and AXFR via -axfr).  Each is an array of plain
    # strings except SOA, so _stringify_value handles them uniformly.
    ("srv", "SRV"),
    ("caa", "CAA"),
    # NOT dnsx's own shapes (see `all` and the `axfr` object below, which are):
    # an array of strings under `any` / `axfr` is kept as read before
    # 2026-10-01 so a file some other producer wrote that way still imports.
    ("any", "ANY"),
    ("axfr", "AXFR"),
    # ptr handled separately so we can also update Host.hostname.
)

# RR types a dnsx row carries in a typed array of its own.  `all` repeats
# those answers in another spelling (`a: ["10.0.0.5"]` beside
# `all: ["www.example.com.\t300\tIN\tA\t10.0.0.5"]`), so on an ordinary row
# `all` is read only for the types that have no typed array (HINFO, DNSKEY,
# TLSA, …) — reading the rest would store every answer twice.
_TYPED_RR_TYPES = frozenset({"A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "SRV", "CAA", "PTR"})

_RR_TYPE = re.compile(r"^[A-Z][A-Z0-9-]*$")
_QUOTED_CHUNK = re.compile(r'"((?:[^"\\]|\\.)*)"')


class ResourceRecord(NamedTuple):
    owner: str
    ttl: Optional[int]
    rtype: str
    value: str


def parse_rr_text(raw: Any) -> Optional[ResourceRecord]:
    """One entry of dnsx's ``all`` array: a resource record as miekg/dns
    prints it — ``owner<TAB>ttl<TAB>class<TAB>TYPE<TAB>rdata`` (retryabledns
    ``DNSData.ParseFromRR``: ``AllRecords = append(…, record.String())``).
    Runs of spaces are accepted in place of the tabs.  None for anything
    else (an OPT pseudo-record starts with ``;``)."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text.startswith(";"):
        return None
    parts = text.split("\t", 4)
    if len(parts) != 5:
        parts = text.split(None, 4)
    if len(parts) != 5:
        return None
    owner, ttl, klass, rtype, rdata = (part.strip() for part in parts)
    rtype = rtype.upper()
    if not ttl.isdigit() or not klass.isalpha() or not _RR_TYPE.match(rtype):
        return None
    owner = owner.rstrip(".")
    value = _rr_value(rtype, rdata)
    if not owner or not value:
        return None
    return ResourceRecord(owner, int(ttl), rtype, value)


def _rr_value(rtype: str, rdata: str) -> str:
    """The record data the way the typed arrays spell it: names without the
    root dot, TXT without its presentation quotes (chunks joined, RFC 7208)."""
    rdata = " ".join(rdata.split())
    if rtype in ("TXT", "SPF"):
        chunks = _QUOTED_CHUNK.findall(rdata)
        if chunks:
            return "".join(chunk.replace('\\"', '"') for chunk in chunks)
        return rdata
    if rtype in ("CNAME", "NS", "PTR", "DNAME", "MX", "SRV"):
        return rdata.rstrip(".")
    return rdata


def _reverse_name_to_ip(owner: str) -> Optional[str]:
    """``5.0.0.10.in-addr.arpa`` -> ``10.0.0.5`` (and the ip6.arpa nibble
    form); None when the owner is not a complete reverse name."""
    lowered = owner.lower().rstrip(".")
    try:
        if lowered.endswith(".in-addr.arpa"):
            labels = lowered[: -len(".in-addr.arpa")].split(".")
            if len(labels) != 4:
                return None
            return str(ipaddress.IPv4Address(".".join(reversed(labels))))
        if lowered.endswith(".ip6.arpa"):
            nibbles = lowered[: -len(".ip6.arpa")].split(".")
            if len(nibbles) != 32 or any(len(n) != 1 for n in nibbles):
                return None
            digits = "".join(reversed(nibbles))
            return str(ipaddress.IPv6Address(":".join(digits[i:i + 4] for i in range(0, 32, 4))))
    except ValueError:
        return None
    return None


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _flatten_resolver(raw: Any) -> Optional[str]:
    """dnsx writes ``resolver`` as either a string ("1.1.1.1:53") or a
    list of strings; normalize to one displayable value (the first)."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list) and raw and isinstance(raw[0], str):
        return raw[0]
    return None


def _resolver_endpoint(server: Optional[str]) -> Optional[tuple[str, int]]:
    """``(address, port)`` of a dnsx resolver string — ``10.0.0.53:53``,
    ``[2001:db8::53]:53`` or a bare address (port 53) — or None when it is
    not an address (a resolver given by name is not attributed to a host)."""
    text_value = (server or "").strip()
    if not text_value:
        return None
    address, port = text_value, 53
    if text_value.startswith("["):
        address, _, rest = text_value[1:].partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
    elif text_value.count(":") == 1:
        address, _, raw_port = text_value.partition(":")
        if not raw_port.isdigit():
            return None
        port = int(raw_port)
    if not _is_valid_ip(address) or not 0 < port < 65536:
        return None
    return str(ipaddress.ip_address(address)), port


def _stringify_value(raw: Any) -> Optional[str]:
    """Most dnsx record fields are arrays of plain strings.  SOA is the
    odd one out — it's typically an object.  We render it as a single
    string for the ``DNSRecord.value`` column (no separate fields)."""
    if isinstance(raw, str):
        cleaned = raw.strip()
        return cleaned or None
    if isinstance(raw, dict):
        # SOA: {"name": "...", "ns": "...", "mbox": "...", ...}
        parts = [f"{k}={v}" for k, v in raw.items() if v not in (None, "")]
        return ", ".join(parts) if parts else None
    return None


class DnsxParser:
    """Parser for dnsx JSON / JSONL output."""

    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)
        self._project_id: Optional[int] = None
        # v2.322.0 — name rows + observation identity memo (see
        # dns_name_service).  Replaces the parser-local ``seen`` tuple set;
        # dedup is now (name, type, value, resolver, scan) and survives across
        # uploads because the DB is checked, not just this file.
        self._name_cache = ObservationCache()
        # Tracked for the parser-warning summary.
        self._resolvers_seen: Counter[str] = Counter()
        self._status_codes_seen: Counter[str] = Counter()
        # (zone, server, records) per name server that handed a zone over.
        self._zone_transfers: List[tuple] = []
        # last_parse_stats — surfaced by the ingestion service on the
        # IngestionJob row so the recon detail page can show "this
        # upload skipped N records" + warnings.
        self.last_parse_stats: Dict[str, Any] = {}

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        start = time.time()
        logger.info("Starting dnsx parse of %s", filename)

        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name="dnsx",
            scan_type="dns_resolution",
            project_id=self._project_id,
        )
        # Every dnsx -json record carries a timestamp; the scan window is
        # first..last record.
        clock = ScanClock()

        records_written = 0
        ptr_hosts_updated = 0
        a_hosts_created = 0
        skipped_records = 0
        # Observation identity is (name, record_type, value, resolver_name,
        # scan) — the same answer from two resolvers stays two rows (the
        # point of resolver_name), an exact repeat from one resolver folds.
        # Enforced by dns_name_service.record_observation + the DB index.

        # R6 — heartbeat between records, outside the record's savepoint.
        beat = ProgressBeat("records")
        for row in iter_json_records(file_path, tool_label="dnsx JSON"):
            beat.tick()
            if not isinstance(row, dict):
                skipped_records += 1
                continue
            status = row.get("status_code")
            if isinstance(status, str):
                self._status_codes_seen[status] += 1
                # NOERROR is the only one with answers to persist.
                # NXDOMAIN / SERVFAIL / REFUSED carry no payload, but we
                # count them so the operator can see resolution failures
                # in the parser-warning summary.
                if status != "NOERROR" and not self._has_any_record_field(row):
                    continue
            resolver = _flatten_resolver(row.get("resolver"))
            if resolver:
                self._resolvers_seen[resolver] += 1
            host = (row.get("host") or "").strip()
            if not host:
                skipped_records += 1
                continue

            ttl = row.get("ttl") if isinstance(row.get("ttl"), int) else None
            observed_at = _parse_timestamp(row.get("timestamp"))
            clock.observe(observed_at)

            # Per-record isolation: wrap each row's answer-persistence in a
            # SAVEPOINT so one malformed answer (a bad value, an unexpected
            # constraint violation) is skipped rather than poisoning the whole
            # upload's transaction — the same resilience
            # persist_host_observation(isolate=True) gives the other
            # per-record parsers (naabu/amass/dirbuster).  The cheap pre-checks
            # above touch no DB state, so they stay outside the savepoint to
            # avoid a SAVEPOINT round-trip per resolution failure.
            journal: List[tuple] = []
            transfers_before = len(self._zone_transfers)
            sp = self.db.begin_nested()
            try:
                row_records, row_ptr_hosts, row_a_hosts = self._persist_row_answers(
                    journal, row, host, ttl, resolver, scan.id, observed_at,
                )
                sp.commit()
                records_written += row_records
                ptr_hosts_updated += row_ptr_hosts
                a_hosts_created += row_a_hosts
            except Exception as exc:  # noqa: BLE001 — isolate one bad record
                sp.rollback()
                skipped_records += 1
                del self._zone_transfers[transfers_before:]
                # The dedup service's history caches point at rows the
                # savepoint just discarded.
                self.dedup_service.discard_rolled_back_state()
                # The rows rolled back with the savepoint; make the cache
                # forget them too so an identical answer in a LATER row still
                # persists instead of being deduped away.
                self._name_cache.forget(journal)
                logger.warning("dnsx: skipping malformed record host=%r: %s", host, exc)

        clock.apply(scan)

        if records_written == 0:
            raise ValueError(
                f"dnsx parser found 0 valid DNS records in {filename}; "
                f"file is empty, every record was a resolution failure, "
                f"or the file isn't dnsx -json output."
            )

        # Best-effort scope correlation for any PTR-created hosts.
        try:
            correlate_scan(self.db, scan.id)
        except Exception as exc:  # pragma: no cover — best effort
            logger.warning("dnsx: scope correlation failed for scan %s: %s", scan.id, exc)

        # Resolver / status summary lands as a parser-warning so the
        # operator can see per-resolver totals in the IngestionJob
        # row without a schema migration.
        warning_parts: List[str] = []
        if self._zone_transfers:
            warning_parts.append(
                "Zone transfer allowed: "
                + ", ".join(
                    f"{zone} by {server} ({count} record{'s' if count != 1 else ''})"
                    for zone, server, count in self._zone_transfers
                )
            )
        if self._resolvers_seen:
            warning_parts.append(
                "Resolvers: "
                + ", ".join(f"{nm}={ct}" for nm, ct in self._resolvers_seen.most_common())
            )
        non_noerror = {
            code: ct
            for code, ct in self._status_codes_seen.items()
            if code != "NOERROR"
        }
        if non_noerror:
            warning_parts.append(
                "Resolution failures: "
                + ", ".join(f"{code}={ct}" for code, ct in non_noerror.items())
            )
        if ptr_hosts_updated:
            warning_parts.append(f"PTR populated Host.hostname for {ptr_hosts_updated} host(s)")
        if a_hosts_created:
            warning_parts.append(f"A/AAAA discovered {a_hosts_created} host(s)")
        warnings = " | ".join(warning_parts) if warning_parts else None

        # Final one-line count summary for the ingestion job's progress
        # column.  The written count was previously only logged (below) and
        # never persisted, so a successful dnsx upload showed an empty
        # progress column and no record count anywhere in the UI.
        summary_parts = [
            f"{records_written} DNS record{'s' if records_written != 1 else ''}"
        ]
        if ptr_hosts_updated:
            summary_parts.append(
                f"{ptr_hosts_updated} host{'s' if ptr_hosts_updated != 1 else ''} named via PTR"
            )
        if a_hosts_created:
            summary_parts.append(
                f"{a_hosts_created} host{'s' if a_hosts_created != 1 else ''} discovered"
            )
        self.last_parse_stats = {
            "skipped": skipped_records,
            "warnings": warnings,
            "summary": ", ".join(summary_parts),
        }

        elapsed = time.time() - start
        logger.info(
            "dnsx parse complete - filename=%s records=%d ptr_hosts=%d skipped=%d elapsed=%.2fs",
            filename, records_written, ptr_hosts_updated, skipped_records, elapsed,
        )
        return scan

    # ------------------------------------------------------------------
    def _has_any_record_field(self, row: Dict[str, Any]) -> bool:
        for field_key, _ in _RECORD_TYPE_FIELDS:
            if isinstance(row.get(field_key), list) and row[field_key]:
                return True
        if isinstance(row.get("ptr"), list) and row["ptr"]:
            return True
        if isinstance(row.get("all"), list) and row["all"]:
            return True
        # A zone that refused the query itself (REFUSED / SERVFAIL from the
        # resolver) can still have handed over the zone to -axfr.
        axfr = row.get("axfr")
        if isinstance(axfr, dict) and isinstance(axfr.get("chain"), list) and axfr["chain"]:
            return True
        return False

    def _persist_all_records(
        self, journal: List[tuple], row: Dict[str, Any], resolver: Optional[str],
        scan_id: int, observed_at: Optional[datetime],
    ) -> int:
        """An ordinary row's ``all`` array: the record types that have no
        typed array.  Each is stored under its OWN owner name and type."""
        entries = row.get("all")
        if not isinstance(entries, list):
            return 0
        written = 0
        for raw in entries:
            record = parse_rr_text(raw)
            if record is None or record.rtype in _TYPED_RR_TYPES:
                continue
            if self._persist_record(
                journal, record.owner, record.rtype, record.value, record.ttl, resolver,
                scan_id=scan_id, observed_at=observed_at,
            ):
                written += 1
        return written

    def _persist_zone_transfer(
        self, journal: List[tuple], row: Dict[str, Any], host: str, resolver: Optional[str],
        scan_id: int, observed_at: Optional[datetime],
    ) -> tuple[int, int, int]:
        """dnsx ``-axfr``: ``axfr`` is an object, ``{"host": zone, "chain":
        [DNSData, …]}`` — one chain entry per name server that handed the zone
        over (retryabledns ``AXFRData``).  The entry's typed arrays carry the
        record data WITHOUT the owner names (every A in the zone under one
        ``a``), so the zone is read from the entry's ``all`` array, which has
        owner, TTL and type per record.  Returns
        ``(records_written, ptr_hosts_updated, a_hosts_created)``.

        A transferred zone leaves, per server that allowed it, one ``AXFR``
        observation on the zone name (the fact itself) plus every record."""
        axfr = row.get("axfr")
        if not isinstance(axfr, dict):
            return 0, 0, 0
        chain = axfr.get("chain")
        if not isinstance(chain, list):
            return 0, 0, 0
        zone = (axfr.get("host") or host or "").strip()
        records_written = ptr_hosts = a_hosts = 0
        for entry in chain:
            if not isinstance(entry, dict):
                continue
            server = _flatten_resolver(entry.get("resolver")) or resolver
            entry_time = _parse_timestamp(entry.get("timestamp")) or observed_at
            zone_name = (entry.get("host") or zone or "").strip()
            transferred = 0
            parsed = [r for r in (parse_rr_text(raw) for raw in entry.get("all") or []) if r is not None] \
                if isinstance(entry.get("all"), list) else []
            for record in parsed:
                transferred += 1
                written, ptr_named, a_named = self._persist_transferred_record(
                    journal, record, server, scan_id, entry_time,
                )
                records_written += written
                ptr_hosts += ptr_named
                a_hosts += a_named
            if not parsed:
                # No owner names to be had (an entry without `all`): the
                # values are kept as what the transfer returned, on the zone,
                # never attributed to a name the file does not give.
                for field_key, record_type in _RECORD_TYPE_FIELDS:
                    values = entry.get(field_key)
                    if record_type in ("ANY", "AXFR") or not isinstance(values, list):
                        continue
                    for raw in values:
                        value_str = _stringify_value(raw)
                        if not value_str or not zone_name:
                            continue
                        transferred += 1
                        if self._persist_record(
                            journal, zone_name, "AXFR", f"{record_type} {value_str}", None, server,
                            scan_id=scan_id, observed_at=entry_time,
                        ):
                            records_written += 1
            if transferred and zone_name:
                if self._persist_record(
                    journal, zone_name, "AXFR",
                    f"zone transfer allowed ({transferred} record{'s' if transferred != 1 else ''})",
                    None, server, scan_id=scan_id, observed_at=entry_time,
                ):
                    records_written += 1
                self._zone_transfers.append((zone_name, server or "unknown server", transferred))
                self._record_zone_transfer_check(zone_name, server, transferred, scan_id)
        return records_written, ptr_hosts, a_hosts

    def _record_zone_transfer_check(
        self, zone: str, server: Optional[str], transferred: int, scan_id: int,
    ) -> bool:
        """The catalog observation ``dns_zone_transfer_allowed`` on the NAME
        SERVER that handed the zone over — the chain entry's resolver, on the
        port it was asked on (AXFR is TCP).

        Only when that address is already a host of this project (known
        before, or discovered by this import — typically the zone's own A
        record for the server).  A resolver is never made a host: it is where
        the operator pointed dnsx, and nothing says it is in scope.  Without a
        host the fact stays what it already was — the ``AXFR`` observation on
        the zone and the line in the import's warnings.

        Runs inside the row's savepoint, so a row that fails takes it along.
        """
        endpoint = _resolver_endpoint(server)
        if endpoint is None:
            return False
        address, port = endpoint
        host_id = (
            self.db.query(models.Host.id)
            .filter(models.Host.project_id == self._project_id, models.Host.ip_address == address)
            .scalar()
        )
        if host_id is None:
            return False
        record_misconfig(
            self.db, check_id="dns_zone_transfer_allowed", host_id=host_id, scan_id=scan_id,
            source=VulnerabilitySource.DNSX, port_number=port,
            evidence=(
                f"Zone {zone}: transfer allowed by {server} "
                f"({transferred} record{'s' if transferred != 1 else ''})"
            ),
        )
        return True

    def _persist_transferred_record(
        self, journal: List[tuple], record: ResourceRecord, server: Optional[str],
        scan_id: int, observed_at: Optional[datetime],
    ) -> tuple[int, int, int]:
        """One record of a transferred zone, stored like the same answer from
        a query: PTR as (name, PTR, address) naming the host, A/AAAA as a
        discovered host named by its owner (never a wildcard owner)."""
        if record.rtype == "PTR":
            address = _reverse_name_to_ip(record.owner)
            if address:
                written = 1 if self._persist_record(
                    journal, record.value, "PTR", address, record.ttl, server,
                    scan_id=scan_id, observed_at=observed_at,
                ) else 0
                named = 1 if self._update_host_hostname(scan_id, address, record.value, source="ptr") else 0
                return written, named, 0
        written = 1 if self._persist_record(
            journal, record.owner, record.rtype, record.value, record.ttl, server,
            scan_id=scan_id, observed_at=observed_at,
        ) else 0
        a_named = 0
        if (
            record.rtype in ("A", "AAAA") and _is_valid_ip(record.value)
            and not record.owner.startswith("*")
        ):
            if self._update_host_hostname(scan_id, record.value, record.owner, source="forward"):
                a_named = 1
        return written, 0, a_named

    def _persist_row_answers(
        self,
        journal: List[tuple],
        row: Dict[str, Any],
        host: str,
        ttl: Optional[int],
        resolver: Optional[str],
        scan_id: int,
        observed_at: Optional[datetime],
    ) -> tuple[int, int, int]:
        """Persist every DNS answer carried by one dnsx row — the record-type
        fields plus PTR — updating the host inventory for forward A/AAAA and
        reverse PTR answers.  Returns this row's
        ``(records_written, ptr_hosts_updated, a_hosts_created)``.

        Raises on any persistence error so the caller's SAVEPOINT can isolate
        a single bad record; ``journal`` collects what this row added to the
        name cache so the caller can forget it on rollback.
        """
        records_written = 0
        ptr_hosts_updated = 0
        a_hosts_created = 0

        records_written += self._persist_all_records(journal, row, resolver, scan_id, observed_at)
        axfr_records, axfr_ptr_hosts, axfr_a_hosts = self._persist_zone_transfer(
            journal, row, host, resolver, scan_id, observed_at,
        )
        records_written += axfr_records
        ptr_hosts_updated += axfr_ptr_hosts
        a_hosts_created += axfr_a_hosts

        for field_key, record_type in _RECORD_TYPE_FIELDS:
            values = row.get(field_key)
            if not isinstance(values, list):
                continue
            for raw in values:
                value_str = _stringify_value(raw)
                if not value_str:
                    continue
                if self._persist_record(
                    journal, host, record_type, value_str, ttl, resolver,
                    scan_id=scan_id, observed_at=observed_at,
                ):
                    records_written += 1
                # RV-1 — a forward A/AAAA answer (domain -> IP) is a
                # discovered asset.  Create a host observation for the
                # resolved IP (hostname = the queried domain), mirroring
                # the PTR path, so a dnsx run that only resolves names
                # no longer produces a host-less "empty" scan.
                if record_type in ("A", "AAAA") and _is_valid_ip(value_str):
                    # 'forward' is the weakest display-name source: many
                    # vhosts share one IP, so it only ever fills an empty
                    # hostname or names a brand-new host.
                    if self._update_host_hostname(
                        scan_id, value_str, host, source="forward",
                    ):
                        a_hosts_created += 1

        ptr_values = row.get("ptr")
        if isinstance(ptr_values, list):
            # For PTR, ``host`` is the queried IP and the value is the
            # discovered hostname.  Persist the DNS record under the
            # *hostname* (matching the canonical "PTR maps in-addr.arpa ->
            # name" semantic the existing CSV parser uses for value=ip /
            # domain=hostname) AND update the host inventory so a successful
            # reverse lookup populates Host.hostname.
            if not _is_valid_ip(host):
                # Some dnsx flag combos emit ptr-of-hostname (forward lookup
                # style); just persist as a generic PTR record with host as
                # the domain.
                for raw in ptr_values:
                    value_str = _stringify_value(raw)
                    if not value_str:
                        continue
                    if self._persist_record(
                        journal, host, "PTR", value_str, ttl, resolver,
                        scan_id=scan_id, observed_at=observed_at,
                    ):
                        records_written += 1
            else:
                for raw in ptr_values:
                    hostname = _stringify_value(raw)
                    if not hostname:
                        continue
                    if self._persist_record(
                        journal, hostname, "PTR", host, ttl, resolver,
                        scan_id=scan_id, observed_at=observed_at,
                    ):
                        records_written += 1
                    if self._update_host_hostname(scan_id, host, hostname, source="ptr"):
                        ptr_hosts_updated += 1

        return records_written, ptr_hosts_updated, a_hosts_created

    def _persist_record(
        self,
        journal: List[tuple],
        domain: str,
        record_type: str,
        value: str,
        ttl: Optional[int],
        resolver_name: Optional[str],
        scan_id: Optional[int] = None,
        observed_at: Optional[datetime] = None,
    ) -> bool:
        row = record_observation(
            self.db,
            project_id=self._project_id,
            name=domain,
            record_type=record_type,
            value=value,
            scan_id=scan_id,
            resolver_name=resolver_name,
            ttl=ttl,
            observed_at=observed_at,
            cache=self._name_cache,
            journal=journal,
        )
        return row is not None

    def _update_host_hostname(
        self, scan_id: int, ip_address: str, hostname: str, *, source: str,
    ) -> bool:
        """A DNS answer names an address: apply it to the host's DISPLAY name
        through the one precedence rule (dns_name_service), creating the host
        with ``state='unknown'`` when it doesn't exist yet.  Returns True if
        the inventory was touched.

        ``source`` is 'ptr' (authoritative reverse DNS — outranks scanner and
        forward names) or 'forward' (weakest — fills an empty hostname or
        names a new host, never replaces).  The name→address relationship
        itself is already persisted by ``_persist_record``.
        """
        existing = (
            self.db.query(models.Host)
            .filter(
                models.Host.ip_address == ip_address,
                models.Host.project_id == self._project_id,
            )
            .first()
        )
        if existing:
            return apply_hostname_candidate(existing, hostname, source)
        # names_recorded: _persist_record already wrote this name→address
        # observation (with resolver + TTL); the dedup service must not add a
        # second, poorer row for the same fact.
        host_data = {
            "hostname": hostname,
            "hostname_source": source,
            "names_recorded": True,
            "state": "unknown",
        }
        self.dedup_service.find_or_create_host(
            ip_address, scan_id, host_data, project_id=self._project_id,
        )
        return True
