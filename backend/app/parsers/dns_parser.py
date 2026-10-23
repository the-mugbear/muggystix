import csv
import ipaddress
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from app.db import models
from app.parsers.parser_utils import (
    ProgressBeat,
    ProjectHostsByAddress,
    ScanHostObservations,
    announce_scan,
    correlate_scan,
    dns_import_summary,
    open_tool_lines,
    record_addressed_hosts,
    write_record,
)
from app.services.dns_name_service import (
    ObservationCache,
    apply_hostname_candidate,
    record_observation,
)
from app.services.host_deduplication_service import HostDeduplicationService
import logging
import time

logger = logging.getLogger(__name__)

# The record types a DNS inventory CSV may carry (v2.416.0).  A row naming
# anything else is rejected, not stored as an unknown kind.
_RR_TYPES = frozenset({
    "A", "AAAA", "PTR", "CNAME", "MX", "NS", "TXT", "SRV", "SOA", "CAA",
    "DNAME", "HINFO", "NAPTR", "SPF", "DS", "DNSKEY", "TLSA", "SSHFP",
    "HTTPS", "SVCB", "LOC", "RP", "AFSDB",
})


class DNSParser:
    def __init__(self, db: Session):
        self.db = db
        self.last_parse_stats: Optional[dict] = None
        self.dedup_service = HostDeduplicationService(db)
        self._name_cache = ObservationCache()
        # The hosts that ALREADY existed and that a PTR row of this file
        # addressed — their host_scan_history rows (parser_utils
        # .record_addressed_hosts).  A host this import creates gets its row
        # from the dedup service.
        self._observed = ScanHostObservations()
        # The project's hosts by address, read once per parse and shared with
        # dnsx (`ProjectHostsByAddress`; review 2026-10-14, R15 and F3); set
        # in parse_file, when the project is known.
        self._hosts: Optional[ProjectHostsByAddress] = None
        self._row_created_address: Optional[str] = None

    def _known_host(self, ip_address: str) -> Optional[models.Host]:
        """The project's host at this address, or None."""
        if self._hosts is None:
            self._hosts = ProjectHostsByAddress(self.db, self._project_id)
        return self._hosts.get(ip_address)

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        """Parse DNS records CSV file and create/update host records with DNS names"""
        self._project_id = kwargs.get("project_id")
        start_time = time.time()
        logger.info(f"Starting DNS parse of {filename}")

        try:
            result = self._parse_csv_file(file_path, filename)
            elapsed_time = time.time() - start_time
            logger.info(f"Successfully parsed DNS {filename} in {elapsed_time:.2f} seconds")
            return result
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(f"Error parsing DNS file {filename} after {elapsed_time:.2f} seconds: {str(e)}")
            raise

    @staticmethod
    def _delimiter(file_path: str) -> str:
        """Tab, semicolon or comma, from the file's first 1024 characters as
        the reader decodes them — read from its first lines, never the whole
        file."""
        sample = ''
        with open_tool_lines(file_path, newline='') as lines:
            for line in lines:
                sample += line
                if len(sample) >= 1024:
                    break
        sample = sample[:1024]
        if '\t' in sample:
            return '\t'
        if ';' in sample:
            return ';'
        return ','

    def _parse_csv_file(self, file_path: str, filename: str) -> models.Scan:
        """Parse DNS CSV file with columns: record_type, name, address"""
        scan = models.Scan(
            filename=filename,
            scan_type='dns_records',
            tool_name='dns',
            project_id=self._project_id,
        )
        self.db.add(scan)
        self.db.flush()
        announce_scan(self.db, scan)

        # Host ids created by this import.  The hosts addressed are these and
        # the noted ones together; the existing hosts whose DISPLAY name a PTR
        # row changed are the notes' own ``renamed_host_ids`` — the set that
        # becomes ``host_scan_history.hostname_changed``.
        created_host_ids: set[int] = set()
        dns_records_processed = 0
        self._rejected = 0
        self._reject_examples: List[str] = []
        # The addressed hosts' history is written ahead of each heartbeat's
        # commit (outside the row's savepoint) and after the last row.
        scan_id = scan.id
        beat = ProgressBeat(
            "DNS records", before=lambda: record_addressed_hosts(self.db, scan_id, self._observed),
        )

        # Review 2026-10-01 R5 — read like every tool text that reaches a Text
        # column (UTF-16 decoded, NUL removed).  The strict utf-8 open failed
        # the whole file on a UTF-16 CSV, which the upload validator admits
        # (Excel's "Unicode text", a PowerShell redirect).
        # A UTF-8 byte-order mark (Excel's "CSV UTF-8") would otherwise become
        # part of the first header name.  STREAMED (review 2026-10-23,
        # R-ING2): a zone export of several hundred MB was read whole, twice
        # over, into the worker's memory.
        delimiter = self._delimiter(file_path)
        with open_tool_lines(file_path, newline='') as lines:
            reader = csv.DictReader(lines, delimiter=delimiter)
            if not reader.fieldnames:
                raise ValueError("DNS CSV file is empty or has no header row (possibly truncated)")

            i = 0
            for row in reader:
                i += 1
                if i % 100 == 0 or i == 1:
                    logger.info(f"Processing DNS record {i}")

                try:
                    normalized_row = self._normalize_row_keys(row)

                    record_type = normalized_row.get('record_type', '').strip().upper()
                    dns_name = normalized_row.get('name', '').strip()
                    ip_address = normalized_row.get('address', '').strip()

                    if not all([record_type, dns_name, ip_address]):
                        logger.warning(f"Skipping row {i}: missing required fields")
                        self._reject(i, "missing type, name or value")
                        continue

                    # v2.416.0 — validated by record type.  Every value used to
                    # be checked as an IP, so CNAME / MX / NS / TXT / SRV rows
                    # (most of a zone) were dropped, and so was compressed
                    # IPv6 such as 2001:db8::1.
                    problem = self._value_problem(record_type, ip_address)
                    if problem:
                        logger.warning(f"Skipping row {i}: {problem}: {ip_address}")
                        self._reject(i, f"{record_type} {problem}")
                        continue

                    # Store the observation regardless of type, through the
                    # shared name service so the row binds to a DNSName and a
                    # repeated CSV line is a no-op instead of a duplicate row.
                    # project_id + scan_id mirror the PTR-host creation below:
                    # without them the row is orphaned — invisible to
                    # project-scoped DNS reads and uncounted in the producing
                    # scan's dns_record_count.
                    ttl_raw = (normalized_row.get('ttl') or '').strip()
                except Exception as e:
                    logger.warning(f"Error processing DNS record row {i}: {str(e)}")
                    self._reject(i, str(e)[:80])
                    continue

                # Review 2026-10-01 R5 — the row's writes are one unit.  The
                # handler "rejected" a row whose statement had failed and
                # carried on in a session that could no longer flush, so every
                # later row was rejected too and the import failed (the
                # record-isolation rule, v2.419.0; ``write_record``, B1-1).
                self._row_created_address = None
                outcome = write_record(
                    self.db,
                    lambda: self._write_row(scan, record_type, dns_name, ip_address, ttl_raw),
                    observed=self._observed, on_rollback=self._reset_caches,
                )
                if outcome.error is not None:
                    logger.warning(f"Error processing DNS record row {i}: {outcome.error}")
                    self._reject(i, str(outcome.error)[:80])
                    continue
                # Counted only once the row's savepoint is released.
                dns_records_processed += 1
                if outcome.value is not None:
                    created_host_ids.add(outcome.value)
                # Heartbeat OUTSIDE the row's savepoint and its handler: a
                # cancel or timeout stops the import, it is not a bad row.
                beat.tick()

        logger.info(f"Processed {i} DNS records from CSV")
        record_addressed_hosts(self.db, scan.id, self._observed)

        # Fail closed when no rows validated — pre-v2.55.0 this path
        # committed a `tool_name='dns'` scan with zero records, which
        # paired with the dispatcher's unconditional dns_csv fallback
        # (removed in v2.54.0) to turn any arbitrary CSV with arbitrary
        # headers into a silent completed DNS scan.  Heuristic-gating
        # at the dispatcher closes the common case; this guard closes
        # the residual case where a DNS-shaped header is present but
        # every row fails validation.
        if dns_records_processed == 0:
            raise ValueError(
                f"DNS CSV produced 0 valid records from {filename}; "
                f"file may have a DNS-shaped header but no valid "
                f"(record_type, name, address) rows."
            )

        # Persist parsed DNS rows + hosts BEFORE correlation.  Correlation
        # can poison the session on a transient error; committing first means
        # a correlation hiccup can't roll back an otherwise-good import, and
        # the swallowed-exception path no longer leaves a pending rollback
        # that turns the trailing commit into PendingRollbackError.  Matches
        # the masscan/nmap ordering.
        self.db.commit()

        hosts_created = len(created_host_ids)
        hosts_addressed = len(self._observed.host_ids | created_host_ids)
        # A host this import created and then named again is a created host.
        # The set that set ``hostname_changed`` — one source for the sentence
        # and the Scans figure.
        hosts_renamed = len(self._observed.renamed_host_ids - created_host_ids)

        # Correlate hosts to subnets
        if hosts_created > 0:
            try:
                correlate_scan(self.db, scan.id)
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                logger.warning(f"Failed to correlate hosts to subnets for scan {scan.id}: {str(e)}")

        # A mixed file's rejected rows are reported on the import, not only
        # logged: a clean-looking job must not hide half a zone.
        self.last_parse_stats = {
            "skipped": self._rejected,
            "warnings": (
                f"{self._rejected} row(s) not imported: " + "; ".join(self._reject_examples)
                + (" …" if self._rejected > len(self._reject_examples) else "")
                if self._rejected else None
            ),
            "summary": dns_import_summary(
                dns_records_processed, hosts_addressed, hosts_created, hosts_renamed,
            ),
            "partial": bool(self._rejected),
        }

        logger.info(
            f"DNS parsing complete - Addressed: {hosts_addressed} hosts, "
            f"Created: {hosts_created} hosts, "
            f"Display names changed: {hosts_renamed} hosts, "
            f"Processed: {dns_records_processed} DNS records"
        )
        return scan

    def _write_row(
        self, scan: models.Scan, record_type: str, dns_name: str, ip_address: str, ttl_raw: str,
    ) -> Optional[int]:
        """One CSV row's writes (inside its record savepoint).  Returns the
        id of the host it created, if any."""
        record_observation(
            self.db,
            project_id=self._project_id,
            name=dns_name,
            record_type=record_type,
            value=ip_address,
            scan_id=scan.id,
            ttl=int(ttl_raw) if ttl_raw.isdigit() else None,
            cache=self._name_cache,
        )

        # For PTR records, also create/update the host.  Filter by project_id
        # so a DNS upload in project A cannot rewrite hostnames on a host
        # owned by project B (Host is unique by ``(project_id, ip_address)``
        # — without the filter, ``.first()`` returns an arbitrary
        # cross-project row).
        if record_type == 'PTR':
            existing_host = self._known_host(ip_address)

            if existing_host:
                # PTR outranks scanner/forward names but never an operator's
                # correction (was: unconditional overwrite).
                renamed = apply_hostname_candidate(existing_host, dns_name, 'ptr')
                # Addressed by this scan whether or not the name changed.  No
                # state: a PTR row does not say the host is up.
                self._observed.note(
                    existing_host, created=False, state=None, hostname=dns_name,
                    renamed=renamed,
                )
                return None
            host_data = {
                'hostname': dns_name,
                'hostname_source': 'ptr',
                'names_recorded': True,  # record_observation above wrote it
                'state': 'unknown',
            }
            created = self.dedup_service.find_or_create_host(
                ip_address, scan.id, host_data, project_id=self._project_id
            )
            # In the address map at once; out again if the row rolls back
            # (`_reset_caches`).
            self._hosts.add(created)
            self._row_created_address = ip_address
            return created.id
        if record_type in ('A', 'AAAA'):
            # A forward row whose value is a KNOWN host's address: the scan
            # addressed that host, as dnsx notes it for the same answer
            # (owner decision 2026-10-11).  Only the note: this row applies no
            # display name and creates no host.
            addressed_host = self._known_host(ip_address)
            if addressed_host:
                self._observed.note(
                    addressed_host, created=False, state=None, hostname=dns_name,
                )
        return None

    def _reset_caches(self) -> None:
        """After a row's savepoint rolled back (see record_savepoint): the
        name memo and the dedup caches may hold rows that no longer exist."""
        self._name_cache = ObservationCache()
        self.dedup_service.discard_rolled_back_state()
        if self._hosts is not None and self._row_created_address:
            self._hosts.forget([self._row_created_address])

    def _normalize_row_keys(self, row: Dict[str, str]) -> Dict[str, str]:
        """Normalize CSV column names to handle different formats"""
        normalized = {}
        for key, value in row.items():
            key_lower = key.lower().strip()
            if key_lower in ['record_type', 'recordtype', 'type', 'record type']:
                normalized['record_type'] = value
            elif key_lower in ['name', 'domain', 'dns_name', 'hostname', 'host']:
                normalized['name'] = value
            elif key_lower in ['address', 'ip_address', 'ip', 'value', 'target']:
                normalized['address'] = value
            elif key_lower in ['ttl', 'time_to_live']:
                normalized['ttl'] = value
            else:
                normalized[key] = value
        return normalized

    def _reject(self, row: int, why: str) -> None:
        self._rejected += 1
        if len(self._reject_examples) < 10:
            self._reject_examples.append(f"row {row}: {why}")

    @staticmethod
    def _value_problem(record_type: str, value: str) -> Optional[str]:
        """Why this value cannot be a ``record_type`` record, or None.

        A / AAAA carry an address of that family; the CSV's PTR rows carry the
        address being named (``name`` is the PTR target).  Every other type's
        value is the record's data — a name, a mail exchanger, text — and is
        kept as written."""
        if record_type not in _RR_TYPES:
            return "is not a DNS record type"
        if record_type in ("A", "AAAA", "PTR"):
            try:
                addr = ipaddress.ip_address(value)
            except ValueError:
                return "value is not an IP address"
            if record_type == "A" and addr.version != 4:
                return "A value is not IPv4"
            if record_type == "AAAA" and addr.version != 6:
                return "AAAA value is not IPv6"
        return None
