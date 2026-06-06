"""
Nessus Integration Service - Simplified Version

Integrates Nessus vulnerability data with BlueStick without risk assessment dependencies.
"""

import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session

from app.parsers.nessus_parser import NessusParser, NessusHost
from app.db.models import Host, Scan
from app.services.vulnerability_service import VulnerabilityService
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.subnet_correlation import SubnetCorrelationService
from app.core.config import settings

logger = logging.getLogger(__name__)


class NessusIntegrationService:
    """Service for integrating Nessus scan data with BlueStick"""

    def __init__(self, db: Session):
        self.db = db
        self.parser = NessusParser()
        self.vulnerability_service = VulnerabilityService(db)
        self.dedup_service = HostDeduplicationService(db)
        self.correlation_service = SubnetCorrelationService(db)
        self._commit_batch_size = max(1, settings.NESSUS_COMMIT_BATCH_SIZE)

    def process_nessus_file(self, file_path: str, scan_name: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """
        Process a Nessus file and integrate the data into BlueStick

        Args:
            file_path: Path to the Nessus XML file
            scan_name: Optional custom name for the scan

        Returns:
            Dictionary with processing results
        """
        project_id = kwargs.get("project_id")
        try:
            scan_info, hosts_iter = self.parser.iter_file(file_path)

            # Create scan record
            scan = self._create_scan_record(scan_info, scan_name, project_id=project_id)
            scan_id = scan.id
            scan_label = scan.filename

            # Process hosts and vulnerabilities
            hosts_processed = 0
            host_processing_failures = 0
            vulnerabilities_found = 0
            vuln_write_failures = 0
            severity_counts = {"info": 0, "low": 0, "medium": 0, "high": 0, "critical": 0}

            for nessus_host in hosts_iter:
                result = self._process_nessus_host(nessus_host, scan, project_id=project_id)
                if result:
                    host, vuln_stats = result
                    hosts_processed += 1
                    vulnerabilities_found += vuln_stats.get("total", 0)
                    vuln_write_failures += vuln_stats.get("write_failures", 0)
                    for severity_name, count in vuln_stats.items():
                        if severity_name in severity_counts:
                            severity_counts[severity_name] += count

                    if hosts_processed % self._commit_batch_size == 0:
                        from app.services.ingestion_service import report_progress
                        report_progress(f"{hosts_processed} hosts, {vulnerabilities_found} vulns")
                        self.db.commit()
                        self.db.expunge_all()
                        scan = self.db.get(Scan, scan_id)
                        if not scan:
                            raise RuntimeError("Scan record disappeared during Nessus ingestion")
                else:
                    # _process_nessus_host may have rolled back the session
                    # on error; re-fetch scan so subsequent hosts can proceed.
                    host_processing_failures += 1
                    scan = self.db.get(Scan, scan_id)
                    if not scan:
                        raise RuntimeError("Scan record disappeared during Nessus ingestion")
                # Free memory regardless of success
                if nessus_host.vulnerabilities:
                    nessus_host.vulnerabilities.clear()

            # Update scan record with metadata populated during iteration
            scan = self.db.get(Scan, scan_id)
            if scan and scan_info:
                if scan_info.get('scan_name') and not scan_name:
                    scan.filename = scan_info['scan_name']
                    scan_label = scan.filename
                if scan_info.get('scanner_version') and not scan.version:
                    scan.version = scan_info['scanner_version']
                if scan_info.get('start_time') and not scan.start_time:
                    scan.start_time = scan_info['start_time']
                if scan_info.get('end_time') and not scan.end_time:
                    scan.end_time = scan_info['end_time']

            self.db.commit()

            # Correlate hosts to subnets
            try:
                correlated = self.correlation_service.batch_correlate_scan_hosts_to_subnets(scan_id)
                logger.info("Nessus scan %s correlated %s hosts to subnets", scan_id, correlated)
            except Exception as exc:
                logger.warning("Nessus scan %s correlation failed: %s", scan_id, exc)
                try:
                    self.db.rollback()
                except Exception:
                    pass

            self.db.expunge_all()

            # v2.91.3 (code review #1) — surface partial-parse and per-
            # finding-write failures.  Pre-fix the scan returned success
            # whenever ANY host was ingested; a truncated XML that
            # produced 50 of 100 hosts (or a host whose vulns silently
            # failed to write) looked identical to a clean import.  For
            # a vulnerability inventory that's a false-negative path.
            parser_truncated = bool(scan_info.get('_parser_truncated'))
            parser_truncation_error = scan_info.get('_parser_truncation_error')
            warnings: list[str] = []
            if parser_truncated:
                warnings.append(
                    f"Nessus XML was truncated/incomplete: {parser_truncation_error}. "
                    f"Only {hosts_processed} hosts were parsed before the error; "
                    f"the remaining hosts in the file are missing."
                )
            if vuln_write_failures:
                warnings.append(
                    f"{vuln_write_failures} vulnerability finding(s) failed to write "
                    f"due to per-finding errors. Check backend logs for the exception detail."
                )
            if host_processing_failures:
                warnings.append(
                    f"{host_processing_failures} host(s) failed to process and were skipped. "
                    f"Check backend logs for the per-host error detail."
                )

            if hosts_processed == 0:
                logger.warning(
                    "Nessus scan %s (%s) completed but 0 hosts were successfully processed. "
                    "The file may be empty, corrupted, or every host may have failed individually.",
                    scan_id,
                    scan_label,
                )
                return {
                    'success': False,
                    'scan_id': scan_id,
                    'error': 'No hosts were successfully processed',
                    'warnings': warnings,
                    'message': (
                        f'Nessus scan parsed but 0 hosts were ingested. '
                        f'Check backend logs for per-host errors.'
                    ),
                }

            # Truncation is a hard data-loss event — the file is missing
            # hosts that should have been ingested.  Report it as a
            # failure even when some hosts landed, so the operator
            # re-uploads a clean export rather than treating the
            # half-import as authoritative.
            if parser_truncated:
                logger.error(
                    "Nessus scan %s (%s) truncated mid-parse: %d hosts ingested, "
                    "%d vulnerabilities, but the source XML was incomplete (%s)",
                    scan_id, scan_label, hosts_processed, vulnerabilities_found,
                    parser_truncation_error,
                )
                return {
                    'success': False,
                    'scan_id': scan_id,
                    'hosts_processed': hosts_processed,
                    'vulnerabilities_found': vulnerabilities_found,
                    'severity_counts': severity_counts,
                    'scan_name': scan_label,
                    'warnings': warnings,
                    'error': 'Nessus XML was truncated mid-parse',
                    'message': (
                        f'Nessus scan was truncated: only {hosts_processed} hosts '
                        f'were ingested before the parser hit the end of the file. '
                        f'Re-export from the scanner and re-upload to capture all hosts.'
                    ),
                }

            logger.info(
                "Nessus scan %s (%s): %d hosts, %d vulnerabilities, "
                "%d vuln-write-failures, %d host-processing-failures",
                scan_id, scan_label, hosts_processed, vulnerabilities_found,
                vuln_write_failures, host_processing_failures,
            )
            partial = bool(vuln_write_failures or host_processing_failures)
            return {
                'success': True,
                'partial': partial,
                'scan_id': scan_id,
                'hosts_processed': hosts_processed,
                'host_processing_failures': host_processing_failures,
                'vulnerabilities_found': vulnerabilities_found,
                'vuln_write_failures': vuln_write_failures,
                'severity_counts': severity_counts,
                'scan_name': scan_label,
                'warnings': warnings,
                'message': (
                    f'Successfully processed Nessus scan with '
                    f'{hosts_processed} hosts and {vulnerabilities_found} vulnerabilities'
                    + (f' ({vuln_write_failures} vuln write failures, '
                       f'{host_processing_failures} host failures)' if partial else '')
                )
            }

        except Exception as e:
            self.db.rollback()
            logger.error(f"Error processing Nessus file {file_path}: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to process Nessus file: {str(e)}'
            }

    def _create_scan_record(self, scan_info: Dict[str, Any], scan_name: Optional[str], project_id: Optional[int] = None) -> Scan:
        """Create a scan record from Nessus data"""

        # Use provided name or derive from metadata
        if scan_name:
            filename = scan_name
        else:
            filename = scan_info.get('scan_name', f"nessus_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

        scan = Scan(
            filename=filename,
            scan_type="nessus",
            tool_name="Nessus",
            start_time=scan_info.get('start_time'),
            end_time=scan_info.get('end_time'),
            version=scan_info.get('scanner_version'),
            created_at=datetime.utcnow(),
            project_id=project_id,
        )

        self.db.add(scan)
        self.db.flush()  # Get the scan ID

        return scan

    def _process_nessus_host(
        self,
        nessus_host: NessusHost,
        scan: Scan,
        project_id: Optional[int] = None,
    ) -> Optional[Tuple[Host, Dict[str, int]]]:
        """Process a single Nessus host using the dedup/history layer.

        Uses a SAVEPOINT so that a per-host failure (e.g. deadlock) only
        rolls back this host's work, not the entire scan transaction.
        """

        savepoint = self.db.begin_nested()
        try:
            # Build host data dict for the dedup service
            host_data: Dict[str, Any] = {
                'state': 'up',  # Nessus only scans live hosts
            }
            hostname = nessus_host.hostname or nessus_host.netbios_name
            if hostname:
                host_data['hostname'] = hostname
            if nessus_host.operating_system:
                host_data['os_name'] = nessus_host.operating_system

            # Use dedup service — creates HostScanHistory automatically
            host = self.dedup_service.find_or_create_host(
                nessus_host.ip_address, scan.id, host_data, project_id=project_id
            )

            # Process vulnerabilities using vulnerability service
            vuln_stats = self.vulnerability_service.process_nessus_vulnerabilities(host, nessus_host, scan)
            logger.debug("Processed %s vulnerabilities for host %s", vuln_stats['total'], host.ip_address)

            savepoint.commit()
            return host, vuln_stats

        except Exception as e:
            logger.error(f"Error processing Nessus host {nessus_host.ip_address}: {str(e)}")
            # Rollback only the savepoint — the outer transaction (and scan
            # record) remain intact so subsequent hosts can still proceed.
            savepoint.rollback()
            return None
