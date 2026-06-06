from typing import Dict, List, Optional, Any
from datetime import datetime
from sqlalchemy.orm import Session
from app.db import models
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.subnet_correlation import SubnetCorrelationService
import logging
import time
import re

logger = logging.getLogger(__name__)

class GnmapParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)
        self.correlation_service = SubnetCorrelationService(db)

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        start_time = time.time()
        logger.info(f"Starting parse of .gnmap file {filename}")

        try:
            result = self._stream_parse(file_path, filename)
            elapsed_time = time.time() - start_time
            logger.info(f"Successfully parsed {filename} in {elapsed_time:.2f} seconds")
            return result
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(f"Error parsing .gnmap file {filename} after {elapsed_time:.2f} seconds: {str(e)}")
            raise

    def _stream_parse(self, file_path: str, filename: str) -> models.Scan:
        logger.info(f"Creating scan record for {filename}")

        # Create scan record
        scan = models.Scan(
            filename=filename,
            tool_name='nmap',
            scan_type='nmap_gnmap',
        )

        # Save scan to get ID
        logger.info(f"Saving initial scan record to database")
        self.db.add(scan)
        self.db.flush()
        scan_id = scan.id

        hosts_processed = 0
        host_lines_seen = 0

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue

                self._apply_scan_metadata(scan, line)

                if not line.startswith('Host:'):
                    continue

                host_lines_seen += 1
                host_data = self._parse_host_line(line)
                if not host_data:
                    continue

                # Wrap each host in its own savepoint.  If the dedup
                # service raises (or leaks a savepoint), rolling back
                # this outer savepoint discards only the bad host's
                # writes — the scan record and every previously-
                # processed host stay intact.  A plain `self.db.rollback()`
                # here would wipe the scan record (added + flushed
                # before the loop), turning a one-row failure into a
                # full-scan loss.
                host_sp = self.db.begin_nested()
                try:
                    self._process_host_with_deduplication(host_data, scan_id)
                    host_sp.commit()
                    hosts_processed += 1

                    if hosts_processed % 100 == 0:
                        logger.info(f"Processed {hosts_processed} host observations")
                        from app.services.ingestion_service import report_progress
                        report_progress(f"{hosts_processed} hosts")

                except Exception as e:
                    logger.error(f"Error processing host {host_data.get('ip_address', 'unknown')}: {e}")
                    try:
                        host_sp.rollback()
                    except Exception:  # noqa: BLE001
                        # Savepoint was already rolled back by an inner
                        # frame — fine, parent transaction is clean.
                        pass
                    continue

        # Commit parsed host data before correlation so it survives
        # even if correlation fails.
        self.db.commit()

        # Correlate hosts to subnets
        try:
            logger.info(f"Starting subnet correlation for scan {scan_id}")
            hosts_correlated = self.correlation_service.batch_correlate_scan_hosts_to_subnets(scan_id)
            logger.info(f"Correlated {hosts_correlated} hosts to subnets")
        except Exception as e:
            logger.error(f"Error in subnet correlation: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

        logger.info(
            "Completed parsing %s: processed %s host observations from %s host lines",
            filename,
            hosts_processed,
            host_lines_seen,
        )
        return scan

    def _apply_scan_metadata(self, scan: models.Scan, line: str) -> None:
        """Update scan metadata incrementally while streaming the file."""
        if not line:
            return

        header_match = re.search(r'^(?:#\s*)?(Nmap|[Mm]asscan)\s+(.+?)\s+scan initiated', line)
        if header_match:
            tool_label = header_match.group(1).lower()
            scan.tool_name = 'masscan' if 'masscan' in tool_label else 'nmap'
            scan.scan_type = f"{scan.tool_name}_gnmap"
            if not scan.version:
                scan.version = header_match.group(2).split()[0]
            if not scan.command_line and ' as: ' in line:
                scan.command_line = line.split(' as: ', 1)[1]

        if line.startswith('#') and not scan.command_line and ' done at ' not in line:
            cmd_match = re.search(r'# (.+)', line)
            if cmd_match:
                scan.command_line = cmd_match.group(1)

        if 'scan initiated' in line and scan.start_time is None:
            time_match = re.search(r'scan initiated (.+)$', line)
            if time_match:
                try:
                    scan.start_time = datetime.strptime(time_match.group(1), '%a %b %d %H:%M:%S %Y')
                except ValueError:
                    pass

        if 'done at' in line:
            time_match = re.search(r'done at (.+?)(?:;|$)', line)
            if time_match:
                try:
                    scan.end_time = datetime.strptime(time_match.group(1).strip(), '%a %b %d %H:%M:%S %Y')
                except ValueError:
                    pass

    def _parse_host_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parse a single Host: line from .gnmap format"""
        try:
            # .gnmap format: Host: <ip> (<hostname>)	Status: <state>	Ports: <port_info>
            parts = line.split('\t')
            
            if len(parts) < 2:
                return None
            
            # Parse host info (first part)
            host_part = parts[0]  # "Host: 192.168.1.1 (hostname)"
            host_match = re.match(r'Host:\s+([^\s]+)(?:\s+\(([^)]+)\))?', host_part)
            
            if not host_match:
                return None
                
            ip_address = host_match.group(1)
            hostname = host_match.group(2) if host_match.group(2) else None
            
            # Parse status
            state = 'unknown'
            state_reason = ''
            for part in parts:
                if part.startswith('Status:'):
                    status_match = re.search(r'Status:\s+(\w+)', part)
                    if status_match:
                        state = status_match.group(1).lower()  # Normalize to lowercase (up/down)
            
            # Parse ports
            ports_data = []
            for part in parts:
                if part.startswith('Ports:'):
                    ports_info = part[6:].strip()  # Remove "Ports:"
                    if ports_info:
                        ports_data = self._parse_ports_info(ports_info)
            
            # Don't skip here - let the merge logic handle filtering
            if state != 'up' and not ports_data:
                return None
            
            return {
                'ip_address': ip_address,
                'hostname': hostname,
                'state': state,
                'state_reason': state_reason,
                'ports': ports_data
            }
            
        except Exception as e:
            logger.warning(f"Failed to parse host line: {line[:100]}... Error: {str(e)}")
            return None

    def _parse_ports_info(self, ports_info: str) -> List[Dict[str, Any]]:
        """Parse port information from .gnmap format"""
        ports_data = []
        
        # .gnmap ports format: port/state/protocol/owner/service/rpc/version, port/state/...
        if not ports_info or ports_info.strip() == '':
            return ports_data
            
        port_entries = ports_info.split(', ')
        
        for entry in port_entries:
            try:
                # Split by / - format: port/state/protocol/owner/service/rpc/version
                fields = entry.split('/')
                if len(fields) < 3:
                    continue
                    
                port_number = int(fields[0])
                state = fields[1]
                protocol = fields[2]
                
                # Extract additional service info if available
                service_name = fields[4] if len(fields) > 4 and fields[4] else None
                service_version = fields[6] if len(fields) > 6 and fields[6] else None
                
                # Only include meaningful ports (open, closed with some data)
                if state in ['open'] or (state in ['closed', 'filtered'] and service_name):
                    port_data = {
                        'port_number': port_number,
                        'protocol': protocol,
                        'state': state,
                        'service_name': service_name,
                        'service_version': service_version
                    }
                    ports_data.append(port_data)
                    
            except (ValueError, IndexError) as e:
                logger.debug(f"Failed to parse port entry: {entry} - {str(e)}")
                continue
        
        return ports_data

    def _process_host_with_deduplication(self, host_data: Dict[str, Any], scan_id: int):
        """Process a single host using deduplication service"""
        ip_address = host_data.get('ip_address')
        if not ip_address:
            logger.warning("Host without IP address, skipping")
            return

        # Extract host metadata for deduplication service
        host_metadata = {
            'hostname': host_data.get('hostname'),
            'state': host_data.get('state'),
            'state_reason': host_data.get('state_reason')
        }

        # Find or create deduplicated host
        host = self.dedup_service.find_or_create_host(ip_address, scan_id, host_metadata, project_id=self._project_id)

        # Process ports
        for port_data in host_data.get('ports', []):
            # Extract port information
            port_info = {
                'port_number': port_data.get('port_number'),
                'protocol': port_data.get('protocol', 'tcp'),
                'state': port_data.get('state'),
                'service_name': port_data.get('service_name'),
                'service_version': port_data.get('service_version')
            }
            
            # Find or create deduplicated port
            self.dedup_service.find_or_create_port(host.id, scan_id, port_info)
