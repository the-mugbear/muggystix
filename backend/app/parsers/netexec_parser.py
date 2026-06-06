"""
NetExec Output Parser

Parses netexec console output and log files to extract host enumeration data
with confidence scoring based on the reliability of different enumeration methods.
"""

import re
import json
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_confidence import NetexecResult, HostConfidence, PortConfidence, ConflictHistory
from app.services.confidence_service import (
    ConfidenceService, ScanType, DataSource, ConfidenceScore
)
from app.services.host_deduplication_service import HostDeduplicationService
import logging

logger = logging.getLogger(__name__)


class NetexecParser:
    """Parser for NetExec output with confidence-based conflict resolution"""

    def __init__(self, db: Session):
        self.db = db
        self.confidence_service = ConfidenceService()
        self.dedup_service = HostDeduplicationService(db)

        # Regex patterns for different netexec output formats
        self.patterns = {
            # Basic host discovery
            'host_basic': re.compile(
                r'(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\w+)\s+\[([^\]]+)\]\s+(.*)'
            ),

            # SMB enumeration patterns
            'smb_enum': re.compile(
                r'SMB\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\w+)\s+\[([^\]]+)\]\s+'
                r'Windows\s+([^(]+)\s*\(name:([^)]+)\)\s*\(domain:([^)]+)\)'
            ),

            # Share enumeration
            'smb_shares': re.compile(
                r'SMB\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\w+\s+\[([^\]]+)\]\s+'
                r'Enumerated shares.*?'
            ),

            # Authentication success
            'auth_success': re.compile(
                r'(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\w+\s+\[\+\]\s+(.*)'
            ),

            # LDAP enumeration
            'ldap_enum': re.compile(
                r'LDAP\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\w+)\s+\[([^\]]+)\]'
            ),

            # Service banners
            'service_banner': re.compile(
                r'(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\w+\s+\[([^\]]+)\]\s+'
                r'(?:Name:|Banner:|Version:)\s*(.*)'
            )
        }

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        """Parse netexec output file"""
        self._project_id = kwargs.get("project_id")
        logger.info(f"Starting netexec parse of {filename}")

        # Create scan record
        scan = models.Scan(
            filename=filename,
            scan_type='netexec',
            tool_name='netexec'
        )
        self.db.add(scan)
        self.db.flush()

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Determine if this is JSON output or console output
            if self._is_json_content(content):
                self._parse_json_output(content, scan.id)
            else:
                self._parse_console_output(content, scan.id)

            logger.info(f"Successfully parsed netexec output: {filename}")
            return scan

        except Exception as e:
            logger.error(f"Error parsing netexec file {filename}: {e}")
            raise

    def _is_json_content(self, content: str) -> bool:
        """Check if content is JSON format"""
        content = content.strip()
        return content.startswith('{') or content.startswith('[')

    def _parse_json_output(self, content: str, scan_id: int):
        """Parse JSON output from netexec spider_plus or similar modules"""
        try:
            data = json.loads(content)

            # Handle different JSON structures
            if isinstance(data, dict):
                for key, value in data.items():
                    if self._looks_like_ip(key):
                        # Key is IP address
                        self._process_json_host_data(key, value, scan_id)
                    elif isinstance(value, dict):
                        # Nested structure with shares/folders
                        for ip_or_share, share_data in value.items():
                            if self._looks_like_ip(ip_or_share):
                                self._process_json_host_data(ip_or_share, share_data, scan_id)

        except json.JSONDecodeError as e:
            logger.warning(
                f"Failed to parse JSON content (file may be truncated/incomplete): {e}. "
                f"Attempting fallback to console-style line parsing."
            )
            # Fall back to console parsing - but warn that results may be incomplete
            self._parse_console_output(content, scan_id)

    def _parse_console_output(self, content: str, scan_id: int):
        """Parse console output from netexec"""
        lines = content.strip().split('\n')
        processed_hosts = set()

        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Try different patterns
            host_data = None

            # Try SMB enumeration pattern first (most detailed)
            match = self.patterns['smb_enum'].match(line)
            if match:
                host_data = self._parse_smb_enum_line(match, line)

            # Try authentication success pattern
            if not host_data:
                match = self.patterns['auth_success'].match(line)
                if match:
                    host_data = self._parse_auth_success_line(match, line)

            # Try basic host pattern
            if not host_data:
                match = self.patterns['host_basic'].match(line)
                if match:
                    host_data = self._parse_basic_host_line(match, line)

            # Process host data if found
            if host_data and host_data['ip_address'] not in processed_hosts:
                self._process_host_with_confidence(host_data, scan_id, line)
                processed_hosts.add(host_data['ip_address'])

    def _parse_smb_enum_line(self, match, full_line: str) -> Dict[str, Any]:
        """Parse SMB enumeration line"""
        ip, port, hostname, status, os_info, name, domain = match.groups()

        return {
            'ip_address': ip,
            'port': int(port),
            'hostname': name.strip(),
            'domain': domain.strip(),
            'os_name': os_info.strip(),
            'protocol': 'smb',
            'confidence_factors': {
                'enumeration_success': True,
                'detailed_info': True,
                'multiple_data_points': 3
            },
            'raw_line': full_line
        }

    def _parse_auth_success_line(self, match, full_line: str) -> Dict[str, Any]:
        """Parse authentication success line"""
        protocol, ip, port, details = match.groups()

        return {
            'ip_address': ip,
            'port': int(port),
            'protocol': protocol.lower(),
            'auth_success': True,
            'details': details.strip(),
            'confidence_factors': {
                'authentication_verified': True,
                'connection_confirmed': True
            },
            'raw_line': full_line
        }

    def _parse_basic_host_line(self, match, full_line: str) -> Dict[str, Any]:
        """Parse basic host discovery line"""
        protocol, ip, port, hostname, status, details = match.groups()

        return {
            'ip_address': ip,
            'port': int(port),
            'hostname': hostname if hostname != ip else None,
            'protocol': protocol.lower(),
            'state': 'up' if 'up' in status.lower() else 'unknown',
            'confidence_factors': {
                'basic_connectivity': True
            },
            'raw_line': full_line
        }

    def _process_json_host_data(self, ip_address: str, data: Any, scan_id: int):
        """Process host data from JSON output"""
        if not self._looks_like_ip(ip_address):
            return

        host_data = {
            'ip_address': ip_address,
            'shares': data if isinstance(data, dict) else {},
            'confidence_factors': {
                'file_enumeration': True,
                'detailed_shares': len(data) if isinstance(data, dict) else 0
            }
        }

        self._process_host_with_confidence(host_data, scan_id, f"JSON: {json.dumps(data)}")

    def _process_host_with_confidence(self, host_data: Dict[str, Any], scan_id: int, raw_output: str):
        """Process host data with confidence scoring"""
        ip_address = host_data['ip_address']

        # Determine scan method and data source
        scan_type, data_source, method = self.confidence_service.detect_netexec_scan_type(raw_output)

        # Calculate confidence based on available data
        additional_factors = host_data.get('confidence_factors', {})
        confidence = self.confidence_service.create_confidence_score(
            scan_type=scan_type,
            data_source=data_source,
            method=method,
            timestamp=datetime.now(),
            additional_factors=additional_factors
        )

        # Create or update host using deduplication service
        extracted_host_data = {
            'hostname': host_data.get('hostname'),
            'state': host_data.get('state', 'up'),
            'os_name': host_data.get('os_name'),
            'domain': host_data.get('domain')
        }

        # Find or create host with confidence tracking
        host = self._find_or_create_host_with_confidence(
            ip_address, scan_id, extracted_host_data, confidence
        )

        # Store netexec-specific results
        self._store_netexec_result(host.id, scan_id, host_data, raw_output)

        # Process port information if available
        if 'port' in host_data:
            self._process_port_with_confidence(
                host.id, scan_id, host_data, confidence
            )

    def _find_or_create_host_with_confidence(
        self,
        ip_address: str,
        scan_id: int,
        host_data: Dict[str, Any],
        confidence: ConfidenceScore
    ) -> models.Host:
        """Find or create host with confidence-based conflict resolution"""

        # Use existing deduplication service
        host = self.dedup_service.find_or_create_host(ip_address, scan_id, host_data, project_id=self._project_id)

        # Track confidence for each field
        for field_name, value in host_data.items():
            if value is not None:
                self._track_field_confidence(
                    'host', host.id, field_name, value, confidence, scan_id
                )

        return host

    def _process_port_with_confidence(
        self,
        host_id: int,
        scan_id: int,
        host_data: Dict[str, Any],
        confidence: ConfidenceScore
    ):
        """Process port information with confidence"""
        port_number = host_data.get('port')
        protocol = host_data.get('protocol', 'tcp')

        if not port_number:
            return

        port_data = {
            'port_number': port_number,
            'protocol': protocol,
            'state': 'open',  # netexec only reports open/accessible ports
            'service_name': host_data.get('protocol'),  # SMB, LDAP, etc.
        }

        # Find or create port
        port = self.dedup_service.find_or_create_port(host_id, scan_id, port_data)

        # Track port field confidence
        for field_name, value in port_data.items():
            if value is not None:
                self._track_field_confidence(
                    'port', port.id, field_name, value, confidence, scan_id
                )

    def _track_field_confidence(
        self,
        object_type: str,
        object_id: int,
        field_name: str,
        current_value: Any,
        confidence: ConfidenceScore,
        scan_id: int
    ):
        """Track confidence for a specific field"""

        # Choose the appropriate confidence table
        if object_type == 'host':
            ConfidenceModel = HostConfidence
        elif object_type == 'port':
            ConfidenceModel = PortConfidence
        else:
            return

        # Check for existing confidence record
        existing = self.db.query(ConfidenceModel).filter(
            ConfidenceModel.host_id == object_id if object_type == 'host' else ConfidenceModel.port_id == object_id,
            ConfidenceModel.field_name == field_name
        ).first()

        if existing:
            # Check if we should update based on confidence
            if confidence.score > existing.confidence_score:
                # Log conflict
                conflict = ConflictHistory(
                    object_type=object_type,
                    object_id=object_id,
                    field_name=field_name,
                    previous_value=str(getattr(existing, 'current_value', 'unknown')),
                    previous_confidence=existing.confidence_score,
                    previous_scan_id=existing.scan_id,
                    previous_method=existing.method,
                    new_value=str(current_value),
                    new_confidence=confidence.score,
                    new_scan_id=scan_id,
                    new_method=confidence.method
                )
                self.db.add(conflict)

                # Update confidence record
                existing.confidence_score = confidence.score
                existing.scan_type = confidence.scan_type.value
                existing.data_source = confidence.source.value
                existing.method = confidence.method
                existing.scan_id = scan_id
                existing.additional_factors = confidence.additional_info
        else:
            # Create new confidence record
            confidence_data = {
                'field_name': field_name,
                'confidence_score': confidence.score,
                'scan_type': confidence.scan_type.value,
                'data_source': confidence.source.value,
                'method': confidence.method,
                'scan_id': scan_id,
                'additional_factors': confidence.additional_info
            }

            if object_type == 'host':
                confidence_data['host_id'] = object_id
            else:
                confidence_data['port_id'] = object_id

            new_confidence = ConfidenceModel(**confidence_data)
            self.db.add(new_confidence)

    def _store_netexec_result(self, host_id: int, scan_id: int, host_data: Dict[str, Any], raw_output: str):
        """Store netexec-specific enumeration results"""

        result = NetexecResult(
            scan_id=scan_id,
            host_id=host_id,
            protocol=host_data.get('protocol', 'unknown'),
            port=host_data.get('port'),
            hostname=host_data.get('hostname'),
            domain_name=host_data.get('domain'),
            auth_success=host_data.get('auth_success', False),
            shares=host_data.get('shares'),
            raw_output=raw_output[:10000],  # Limit size
            connection_stable=True,
            multiple_confirmations=len(host_data.get('confidence_factors', {})) > 2
        )

        self.db.add(result)

    def _looks_like_ip(self, text: str) -> bool:
        """Check if text looks like an IP address"""
        if not text:
            return False

        parts = text.split('.')
        if len(parts) != 4:
            return False

        try:
            for part in parts:
                num = int(part)
                if not 0 <= num <= 255:
                    return False
            return True
        except ValueError:
            return False