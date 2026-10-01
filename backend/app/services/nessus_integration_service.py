"""
Nessus Integration Service - Simplified Version

Integrates Nessus vulnerability data with BlueStick without risk assessment dependencies.
"""

import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session

from app.parsers.nessus_parser import NessusParser, NessusHost
from app.db.models import Host, HostScanHistory, Scan
from app.services.vulnerability_service import VulnerabilityService
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.subnet_correlation import SubnetCorrelationService
from app.core.config import settings
from app.parsers.parser_utils import ScanClock, epoch_to_utc


def _observe_nessus_host_times(clock: ScanClock, props: Dict[str, str]) -> None:
    """Nessus records when it scanned each HOST, not when the scan ran.

    ``HOST_START_TIMESTAMP`` / ``HOST_END_TIMESTAMP`` (epoch, recent exports)
    are absolute; ``HOST_START`` / ``HOST_END`` are the scanner's local ctime
    with no zone and are used only when no epoch is present.  The scan window
    is first host start .. last host end (v2.333.0 — before this every Nessus
    scan had a NULL window: the parser never read these tags).
    """
    start = epoch_to_utc(props.get("HOST_START_TIMESTAMP"))
    end = epoch_to_utc(props.get("HOST_END_TIMESTAMP"))
    if start is not None or end is not None:
        clock.observe(start)
        clock.observe(end)
        return
    for key in ("HOST_START", "HOST_END"):
        raw = props.get(key)
        if not raw:
            continue
        try:
            clock.observe_clock(datetime.strptime(raw.strip(), "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            continue

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
        # See NmapXMLParser — id of the incrementally-committed Scan, so the
        # dispatcher can delete a partial scan when the import does not
        # finish (review 2026-10-01 C1: Nessus never said which one it was).
        self._created_scan_id: Optional[int] = None
        # How many created-port ids are already on the job row (R2).
        self._ports_recorded = 0

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
        # v2.341.0 — drop severity-0 report items (ports still derived from
        # them).  Resolved by the upload route from the form field / project
        # setting / deployment default; this layer only honours it.
        skip_informational = bool(kwargs.get("skip_informational", False))
        from app.services.ingestion_service import ParseFailure, note_scan_created, report_progress
        try:
            scan_info, hosts_iter = self.parser.iter_file(file_path)

            # Create scan record
            scan = self._create_scan_record(scan_info, scan_name, project_id=project_id)
            scan_id = scan.id
            scan_label = scan.filename
            self._created_scan_id = scan_id
            # R1 — on the job row with the scan's first commit.
            note_scan_created(self.db, scan_id)

            # Process hosts and vulnerabilities
            hosts_processed = 0
            host_processing_failures = 0
            vulnerabilities_found = 0
            vuln_write_failures = 0
            informational_skipped = 0
            report_items_unreadable = 0
            # B12 — hosts whose scan says it did / did not authenticate.
            hosts_credentialed = 0
            hosts_uncredentialed = 0
            severity_counts = {"info": 0, "low": 0, "medium": 0, "high": 0, "critical": 0}
            clock = ScanClock()

            for nessus_host in hosts_iter:
                _observe_nessus_host_times(clock, nessus_host.host_properties or {})
                report_items_unreadable += nessus_host.unreadable_items
                result = self._process_nessus_host(
                    nessus_host, scan, project_id=project_id,
                    skip_informational=skip_informational,
                )
                if result:
                    host, vuln_stats = result
                    hosts_processed += 1
                    if nessus_host.credentialed is True:
                        hosts_credentialed += 1
                    elif nessus_host.credentialed is False:
                        hosts_uncredentialed += 1
                    vulnerabilities_found += vuln_stats.get("total", 0)
                    vuln_write_failures += vuln_stats.get("write_failures", 0)
                    informational_skipped += vuln_stats.get("info_skipped", 0)
                    for severity_name, count in vuln_stats.items():
                        if severity_name in severity_counts:
                            severity_counts[severity_name] += count

                    if hosts_processed % self._commit_batch_size == 0:
                        # R2 — in the transaction the heartbeat commits.
                        self._record_created_ports()
                        report_progress(f"{hosts_processed} hosts, {vulnerabilities_found} vulns")
                        self.db.commit()
                        self.db.expunge_all()
                        # R3 — the dedup caches held the rows just detached.
                        self.dedup_service.forget_session_rows()
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
            if scan:
                clock.apply(scan)

            # R2 — the last, partial batch's ports, in the commit that makes
            # them durable.
            self._record_created_ports()
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
            # Review 2026-10-01 N9 — report items the parser could not read
            # were logged and dropped; the import said nothing.
            if report_items_unreadable:
                warnings.append(
                    f"{report_items_unreadable} report item(s) could not be read and were skipped. "
                    f"Check backend logs for the per-item error detail."
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
                        'Nessus scan parsed but 0 hosts were ingested. '
                        'Check backend logs for per-host errors.'
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
                    'informational_skipped': informational_skipped,
                    'severity_counts': severity_counts,
                    'scan_name': scan_label,
                    'warnings': warnings,
                    'error': 'Nessus XML was truncated mid-parse',
                    # The hosts that did land were committed with the switch
                    # applied, so their skipped count is reported the same way
                    # a clean import reports it.
                    'message': (
                        f'Nessus scan was truncated: only {hosts_processed} hosts '
                        f'were ingested before the parser hit the end of the file'
                        + (f' ({informational_skipped} informational skipped)'
                           if skip_informational else '')
                        + '. Re-export from the scanner and re-upload to capture all hosts.'
                    ),
                }

            logger.info(
                "Nessus scan %s (%s): %d hosts, %d vulnerabilities, "
                "%d vuln-write-failures, %d host-processing-failures",
                scan_id, scan_label, hosts_processed, vulnerabilities_found,
                vuln_write_failures, host_processing_failures,
            )
            partial = bool(vuln_write_failures or host_processing_failures or report_items_unreadable)
            return {
                'success': True,
                'partial': partial,
                'scan_id': scan_id,
                'hosts_processed': hosts_processed,
                'host_processing_failures': host_processing_failures,
                'vulnerabilities_found': vulnerabilities_found,
                'vuln_write_failures': vuln_write_failures,
                'report_items_unreadable': report_items_unreadable,
                'informational_skipped': informational_skipped,
                'hosts_credentialed': hosts_credentialed,
                'hosts_uncredentialed': hosts_uncredentialed,
                'severity_counts': severity_counts,
                'scan_name': scan_label,
                'warnings': warnings,
                # The skipped count is part of the message on purpose: an
                # analyst comparing against the Nessus UI must see that two
                # thirds of the items were dropped by choice, not lost.
                'message': (
                    f'Successfully processed Nessus scan with '
                    f'{hosts_processed} hosts and {vulnerabilities_found} vulnerabilities'
                    + (f', {informational_skipped} informational skipped'
                       if skip_informational else '')
                    # Said only when the file says it: an unauthenticated
                    # scan that found nothing looked at the host from outside.
                    + (f'; credentialed checks ran on {hosts_credentialed} host(s), '
                       f'not on {hosts_uncredentialed}'
                       if (hosts_credentialed or hosts_uncredentialed) else '')
                    + (f' ({vuln_write_failures} vuln write failures, '
                       f'{host_processing_failures} host failures, '
                       f'{report_items_unreadable} unreadable report items)' if partial else '')
                )
            }

        except ParseFailure:
            # Review 2026-10-01 C1 — cancel, timeout, a superseded attempt and
            # a worker shutdown arrive from report_progress as ParseFailure /
            # ShutdownRequested (both RuntimeError).  The blanket handler
            # below turned them into ``success: False``, so a restart FAILED
            # the job instead of handing it back, and the batches already
            # committed stayed in a scan nothing pointed at.  They are the
            # dispatcher's to handle: it deletes the partial scan
            # (``_created_scan_id``) and re-queues or fails the job.
            self.db.rollback()
            raise
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
            project_id=project_id,
        )

        self.db.add(scan)
        self.db.flush()  # Get the scan ID

        return scan

    def _record_created_ports(self) -> None:
        """Put the ids of the ports this import has created on its job row,
        in the CURRENT transaction (review 2026-10-01 R2) — called just before
        each batch commit, so a committed port is always one the job names.

        Nessus writes no ``PortScanHistory`` (it would change the port counts
        on the Scans page, the dashboard and the scan diff), so without this a
        failed attempt's ports on hosts that already existed could not be
        found again.  Cleanup only.  The whole list is rewritten when it grew
        — one row write per batch (50 hosts by default); a JSON array of
        100k ids is about 1 MB."""
        from app.services.ingestion_service import note_ports_created

        ids = self.vulnerability_service.created_port_ids
        if len(ids) == self._ports_recorded:
            return
        note_ports_created(self.db, ids)
        self._ports_recorded = len(ids)

    def _record_credentialed(self, host_id: int, scan_id: int, credentialed: Optional[bool]) -> None:
        """Whether this scan authenticated to the host, on the host's row in
        the scan (``host_scan_history.credentialed``).  Nothing is written
        when the file does not say.  The history row was added by the dedup
        service a moment ago and may still be pending, so it is flushed
        before the one-statement update."""
        if credentialed is None:
            return
        self.db.flush()
        self.db.query(HostScanHistory).filter(
            HostScanHistory.host_id == host_id,
            HostScanHistory.scan_id == scan_id,
        ).update({HostScanHistory.credentialed: credentialed}, synchronize_session=False)

    def _process_nessus_host(
        self,
        nessus_host: NessusHost,
        scan: Scan,
        project_id: Optional[int] = None,
        skip_informational: bool = False,
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
            self._record_credentialed(host.id, scan.id, nessus_host.credentialed)

            # Process vulnerabilities using vulnerability service
            vuln_stats = self.vulnerability_service.process_nessus_vulnerabilities(
                host, nessus_host, scan, skip_informational=skip_informational,
            )
            logger.debug("Processed %s vulnerabilities for host %s", vuln_stats['total'], host.ip_address)

            savepoint.commit()
            return host, vuln_stats

        except Exception as e:
            logger.error(f"Error processing Nessus host {nessus_host.ip_address}: {str(e)}")
            # Rollback only the savepoint — the outer transaction (and scan
            # record) remain intact so subsequent hosts can still proceed.
            savepoint.rollback()
            # The history / name rows the savepoint added are gone (record
            # isolation rule, v2.419.0).
            self.dedup_service.discard_rolled_back_state()
            return None
