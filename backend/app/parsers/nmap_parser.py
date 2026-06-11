"""
Nmap XML Parser - With Host Deduplication

Uses the host deduplication service to eliminate duplicate host entries
and maintain scan history.
"""

from typing import Dict, Optional, Any
from datetime import datetime
from lxml import etree
from sqlalchemy.orm import Session
from app.db import models
from app.parsers.xml_stream_helpers import clear_element, iterparse_safe, strip_namespace
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.subnet_correlation import SubnetCorrelationService
import logging
import time

logger = logging.getLogger(__name__)


class NmapXMLParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)
        self.correlation_service = SubnetCorrelationService(db)

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        start_time = time.time()
        logger.info(f"Starting streamed parse of {filename}")

        try:
            scan = self._stream_parse(file_path, filename)
            elapsed_time = time.time() - start_time
            logger.info(f"Successfully parsed {filename} in {elapsed_time:.2f} seconds")
            return scan
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(f"Error parsing XML file {filename} after {elapsed_time:.2f} seconds: {str(e)}")
            raise

    def _stream_parse(self, file_path: str, filename: str) -> models.Scan:
        scan: Optional[models.Scan] = None
        scan_id: Optional[int] = None
        hosts_processed = 0
        parse_warnings: list = []

        try:
            # Hardening flags (resolve_entities/no_network/huge_tree)
            # live in xml_stream_helpers — see iterparse_safe for the
            # rationale on each.
            context = iterparse_safe(file_path)
        except etree.XMLSyntaxError as exc:
            raise ValueError(
                f"XML file is malformed or empty and could not be opened: {exc}"
            ) from exc

        try:
            for event, elem in context:
                tag = strip_namespace(elem.tag)

                if event == "start" and tag == "nmaprun":
                    scan = self._create_scan_record(elem, filename)
                    scan_id = scan.id
                    continue

                if scan_id is None:
                    continue

                if event == "end":
                    if tag == "scaninfo":
                        self._parse_scan_info_element(elem, scan_id)
                        clear_element(elem)
                    elif tag == "host":
                        if self._host_has_address(elem):
                            try:
                                self._process_host_with_deduplication(elem, scan_id)
                                hosts_processed += 1
                                if hosts_processed % 100 == 0:
                                    logger.info(f"Processed {hosts_processed} hosts so far")
                                    from app.services.ingestion_service import report_progress
                                    report_progress(f"{hosts_processed} hosts")
                                # Periodic commit so a 10k-host scan doesn't hold row locks
                                # on hosts/ports/host_scan_history for the full parse duration,
                                # which would block every other concurrent writer.
                                if hosts_processed % 500 == 0:
                                    self.db.commit()
                                    if scan is not None:
                                        self.db.refresh(scan)
                            except Exception as e:
                                logger.warning(f"Skipping malformed host element: {e}")
                                parse_warnings.append(str(e))
                            finally:
                                clear_element(elem)
                        else:
                            clear_element(elem)
                    elif tag == "finished" and scan is not None:
                        self._update_scan_end_time(scan, elem)
                        clear_element(elem)
                    elif tag in (
                        "taskbegin", "taskend", "taskprogress",
                        "hosthint", "verbose", "debugging",
                    ):
                        clear_element(elem)
        except etree.XMLSyntaxError as exc:
            # Truncated/incomplete XML - preserve whatever hosts were already parsed
            logger.warning(
                f"XML parsing halted for {filename} (likely truncated/incomplete scan): {exc}. "
                f"Recovered {hosts_processed} hosts before the error."
            )
            parse_warnings.append(f"Incomplete XML: {exc}")

        if scan is None or scan_id is None:
            raise ValueError("Unable to locate nmaprun element in XML")

        # Mark scan as partial if we hit XML errors
        if parse_warnings:
            existing_notes = scan.command_line or ''
            scan.command_line = existing_notes
            logger.warning(
                f"Scan {filename} completed with {len(parse_warnings)} warning(s): "
                f"{hosts_processed} hosts recovered"
            )

        # Commit parsed host data before attempting correlation so it
        # survives even if correlation fails.
        self.db.commit()

        # Correlate hosts to subnets
        try:
            logger.info(f"Starting subnet correlation for scan {scan_id}")
            hosts_correlated = self.correlation_service.batch_correlate_scan_hosts_to_subnets(scan_id)
            logger.info(f"Correlated {hosts_correlated} hosts to subnets")
        except Exception as e:  # pragma: no cover - defensive
            logger.error(f"Error in subnet correlation: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

        logger.info(f"Parsed {hosts_processed} host records from {filename}")
        return scan

    def _create_scan_record(self, nmaprun_elem: etree.Element, filename: str) -> models.Scan:
        scanner = (nmaprun_elem.get('scanner') or 'nmap').lower()
        tool_name = 'masscan' if scanner == 'masscan' else 'nmap'
        scan_type = 'port_scan' if tool_name == 'masscan' else 'nmap'

        start_time = self._parse_epoch_timestamp(nmaprun_elem.get('start'))
        if start_time is None:
            start_time = self._parse_timestr(nmaprun_elem.get('startstr'))

        scan = models.Scan(
            filename=filename,
            scan_type=scan_type,
            version=nmaprun_elem.get('version'),
            xml_output_version=nmaprun_elem.get('xmloutputversion'),
            command_line=nmaprun_elem.get('args', ''),
            tool_name=tool_name,
            start_time=start_time,
            project_id=self._project_id,
        )

        self.db.add(scan)
        self.db.flush()
        return scan

    def _parse_scan_info_element(self, scaninfo_elem: etree.Element, scan_id: int):
        scan_info = models.ScanInfo(
            scan_id=scan_id,
            type=scaninfo_elem.get('type'),
            protocol=scaninfo_elem.get('protocol'),
            numservices=int(scaninfo_elem.get('numservices', 0)),
            services=scaninfo_elem.get('services'),
        )
        self.db.add(scan_info)

    def _update_scan_end_time(self, scan: models.Scan, finished_elem: etree.Element) -> None:
        end_time = self._parse_epoch_timestamp(finished_elem.get('time'))
        if end_time is None:
            end_time = self._parse_timestr(finished_elem.get('timestr'))

        if end_time is not None:
            scan.end_time = end_time
        elif scan.end_time is None:
            scan.end_time = datetime.utcnow()

    def _parse_epoch_timestamp(self, raw: Optional[str]) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.utcfromtimestamp(int(raw))
        except (TypeError, ValueError):
            return None

    def _parse_timestr(self, raw: Optional[str]) -> Optional[datetime]:
        if not raw:
            return None
        for fmt in ('%a %b %d %H:%M:%S %Y', '%Y-%m-%d %H:%M:%S'):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        logger.warning(f"Could not parse timestamp string: {raw}")
        return None

    def _host_has_address(self, host_elem: etree.Element) -> bool:
        address_elem = host_elem.find('address[@addrtype="ipv4"]')
        if address_elem is None:
            address_elem = host_elem.find('address[@addrtype="ipv6"]')
        return address_elem is not None

    def _process_host_with_deduplication(self, host_elem: etree.Element, scan_id: int):
        """Process a single host using deduplication"""
        address_elem = self._find_primary_address(host_elem)
        if address_elem is None:
            logger.warning("Host without IP address, skipping")
            return

        ip_address = address_elem.get('addr')
        if not ip_address:
            return

        host_data = self._extract_host_data(host_elem)
        ports_elem = host_elem.find('ports')
        hostscript_elem = host_elem.find('hostscript')

        # Skip hosts that are down and have no actionable data (ports, OS, scripts)
        host_state = (host_data.get('state') or '').lower()
        has_ports = ports_elem is not None and ports_elem.find('port') is not None
        has_os = host_data.get('os_name') is not None
        has_scripts = hostscript_elem is not None and hostscript_elem.find('script') is not None
        if host_state == 'down' and not has_ports and not has_os and not has_scripts:
            logger.debug(
                "Nmap parser skipping down host %s with no actionable data",
                ip_address,
            )
            return

        # Find or create deduplicated host (scoped to project)
        host = self.dedup_service.find_or_create_host(ip_address, scan_id, host_data, project_id=self._project_id)

        # Process ports
        self._process_host_ports(ports_elem, host.id, scan_id)

        # Process host scripts
        self._process_host_scripts(hostscript_elem, host.id, scan_id)

        # Extract SMB message-signing posture to the queryable host column
        # (don't clobber a prior observation with None when this scan is silent).
        signing = self._detect_smb_signing(hostscript_elem)
        if signing:
            host.smb_signing = signing

    def _find_primary_address(self, host_elem: etree.Element) -> Optional[etree.Element]:
        address_elem = host_elem.find('address[@addrtype="ipv4"]')
        if address_elem is None:
            address_elem = host_elem.find('address[@addrtype="ipv6"]')
        return address_elem

    def _extract_host_data(self, host_elem: etree.Element) -> Dict[str, Any]:
        """Extract host information from XML element"""
        host_data = {}
        
        # Extract hostname
        hostnames = host_elem.find('hostnames')
        if hostnames is not None:
            hostname_elem = hostnames.find('hostname')
            if hostname_elem is not None:
                host_data['hostname'] = hostname_elem.get('name')
        
        # Extract status
        status = host_elem.find('status')
        if status is not None:
            host_data['state'] = status.get('state')
            host_data['state_reason'] = status.get('reason')
        
        # Extract OS information
        os_elem = host_elem.find('os')
        if os_elem is not None:
            osmatch = os_elem.find('osmatch')
            if osmatch is not None:
                host_data['os_name'] = osmatch.get('name')
                host_data['os_accuracy'] = int(osmatch.get('accuracy', 0))
                
                # Extract OS class info
                osclass = osmatch.find('osclass')
                if osclass is not None:
                    host_data['os_family'] = osclass.get('osfamily')
                    host_data['os_generation'] = osclass.get('osgen')
                    host_data['os_type'] = osclass.get('type')
                    host_data['os_vendor'] = osclass.get('vendor')
        
        return host_data

    def _process_host_ports(self, ports_elem: Optional[etree.Element], host_id: int, scan_id: int):
        """Process ports for a host with deduplication"""
        if ports_elem is None:
            return
        
        for port_elem in ports_elem.findall('port'):
            port_data = self._extract_port_data(port_elem)
            
            # Find or create deduplicated port
            port = self.dedup_service.find_or_create_port(host_id, scan_id, port_data)
            
            # Process port scripts
            self._process_port_scripts(port_elem, port.id, scan_id)

    def _extract_port_data(self, port_elem: etree.Element) -> Dict[str, Any]:
        """Extract port information from XML element"""
        raw_portid = port_elem.get('portid')
        if raw_portid is None:
            raise ValueError("Port element missing required 'portid' attribute")
        try:
            port_number = int(raw_portid)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid portid value '{raw_portid}': {e}") from e

        port_data = {
            'port_number': port_number,
            'protocol': port_elem.get('protocol', 'tcp')
        }
        
        # Extract state
        state_elem = port_elem.find('state')
        if state_elem is not None:
            port_data['state'] = state_elem.get('state')
            port_data['reason'] = state_elem.get('reason')
        
        # Extract service info
        service_elem = port_elem.find('service')
        if service_elem is not None:
            port_data.update({
                'service_name': service_elem.get('name'),
                'service_product': service_elem.get('product'),
                'service_version': service_elem.get('version'),
                'service_extrainfo': service_elem.get('extrainfo'),
                'service_method': service_elem.get('method'),
                'service_conf': int(service_elem.get('conf', 0))
            })
        
        return port_data

    def _process_port_scripts(self, port_elem: etree.Element, port_id: int, scan_id: int):
        """Process scripts for a port"""
        for script_elem in port_elem.findall('script'):
            script_data = {
                'script_id': script_elem.get('id'),
                'output': script_elem.get('output', '')
            }
            
            self.dedup_service.add_or_update_script(port_id, scan_id, script_data)

    @staticmethod
    def _detect_smb_signing(hostscript_elem: Optional[etree.Element]) -> Optional[str]:
        """Classify SMB message-signing from nmap smb(2)-security-mode output.

        Returns 'disabled' (relay-vulnerable), 'required', 'enabled' (on but not
        required), or None when no signing script ran.  Reads the ``output``
        text of the smb-security-mode / smb2-security-mode host scripts.
        """
        if hostscript_elem is None:
            return None
        text = " ".join(
            (s.get("output") or "")
            for s in hostscript_elem.findall("script")
            if (s.get("id") or "") in ("smb-security-mode", "smb2-security-mode")
        ).lower()
        if not text:
            return None
        # smb-security-mode: "message_signing: disabled|supported|required"
        # smb2-security-mode: "Message signing enabled but not required" / "... and required"
        if "signing" not in text and "message_signing" not in text:
            return None
        if "disabled" in text:
            return "disabled"
        if "required" in text and "not required" not in text:
            return "required"
        if "enabled" in text or "supported" in text:
            return "enabled"
        return None

    def _process_host_scripts(self, hostscript_elem: Optional[etree.Element], host_id: int, scan_id: int):
        """Process host scripts"""
        if hostscript_elem is None:
            return

        for script_elem in hostscript_elem.findall('script'):
            script_data = {
                'script_id': script_elem.get('id'),
                'output': script_elem.get('output', '')
            }
            self.dedup_service.add_or_update_host_script(host_id, scan_id, script_data)
