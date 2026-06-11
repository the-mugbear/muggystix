import csv
import re
from typing import Dict, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from app.db import models
from app.parsers.parser_utils import correlate_scan
from app.services.host_deduplication_service import HostDeduplicationService
import logging
import time

logger = logging.getLogger(__name__)


class DNSParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)

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
            created_at=datetime.utcnow(),
            project_id=self._project_id,
        )
        self.db.add(scan)
        self.db.flush()

        hosts_created = 0
        hosts_updated = 0
        dns_records_processed = 0

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
                        continue

                    if not self._is_valid_ip(ip_address):
                        logger.warning(f"Skipping row {i}: invalid IP address format: {ip_address}")
                        continue

                    # Store the DNS record regardless of type.  project_id +
                    # scan_id mirror the PTR-host creation below (and the
                    # dnsx parser): without them the row is orphaned —
                    # invisible to project-scoped DNS reads and uncounted in
                    # the producing scan's dns_record_count.
                    dns_record = models.DNSRecord(
                        domain=dns_name,
                        record_type=record_type,
                        value=ip_address,
                        project_id=self._project_id,
                        scan_id=scan.id,
                    )
                    self.db.add(dns_record)
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
                            if not existing_host.hostname or existing_host.hostname != dns_name:
                                existing_host.hostname = dns_name
                                hosts_updated += 1
                        else:
                            host_data = {
                                'hostname': dns_name,
                                'state': 'unknown',
                            }
                            self.dedup_service.find_or_create_host(
                                ip_address, scan.id, host_data, project_id=self._project_id
                            )
                            hosts_created += 1

                except Exception as e:
                    logger.warning(f"Error processing DNS record row {i}: {str(e)}")
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

    def _is_valid_ip(self, ip_address: str) -> bool:
        """Validate IP address format (supports both IPv4 and IPv6)"""
        ipv4_pattern = r'^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
        ipv6_pattern = r'^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$|^::1$|^::$'
        return bool(re.match(ipv4_pattern, ip_address) or re.match(ipv6_pattern, ip_address))
