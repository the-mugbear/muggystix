import csv
import ipaddress
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from app.db import models
from app.parsers.parser_utils import correlate_scan
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

        hosts_created = 0
        hosts_updated = 0
        dns_records_processed = 0
        self._rejected = 0
        self._reject_examples: List[str] = []

        with open(file_path, 'r', encoding='utf-8') as csvfile:
            sample = csvfile.read(1024)
            csvfile.seek(0)

            delimiter = ','
            if '\t' in sample:
                delimiter = '\t'
            elif ';' in sample:
                delimiter = ';'

            reader = csv.DictReader(csvfile, delimiter=delimiter)
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
                    dns_records_processed += 1

                    # For PTR records, also create/update the host.
                    # Filter by project_id so a DNS upload in project A
                    # cannot rewrite hostnames on a host owned by project
                    # B (Host is unique by ``(project_id, ip_address)`` —
                    # without the filter, ``.first()`` returns an
                    # arbitrary cross-project row).
                    if record_type == 'PTR':
                        existing_host = self.db.query(models.Host).filter(
                            models.Host.ip_address == ip_address,
                            models.Host.project_id == self._project_id,
                        ).first()

                        if existing_host:
                            # PTR outranks scanner/forward names but never an
                            # operator's correction (was: unconditional overwrite).
                            if apply_hostname_candidate(existing_host, dns_name, 'ptr'):
                                hosts_updated += 1
                        else:
                            host_data = {
                                'hostname': dns_name,
                                'hostname_source': 'ptr',
                                'names_recorded': True,  # record_observation above wrote it
                                'state': 'unknown',
                            }
                            self.dedup_service.find_or_create_host(
                                ip_address, scan.id, host_data, project_id=self._project_id
                            )
                            hosts_created += 1

                except Exception as e:
                    logger.warning(f"Error processing DNS record row {i}: {str(e)}")
                    self._reject(i, str(e)[:80])
                    continue

        logger.info(f"Processed {i} DNS records from CSV")

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
            "summary": f"{dns_records_processed} DNS record{'s' if dns_records_processed != 1 else ''}",
            "partial": bool(self._rejected),
        }

        logger.info(
            f"DNS parsing complete - Created: {hosts_created} hosts, "
            f"Updated: {hosts_updated} hosts, "
            f"Processed: {dns_records_processed} DNS records"
        )
        return scan

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
