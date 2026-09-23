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
from app.parsers.parser_utils import correlate_scan
from app.services import smb_signing as smb_signing_states
import logging

logger = logging.getLogger(__name__)

# Real nxc output rarely starts at the protocol token: a terminal capture
# (`tee`, `script`) carries ANSI colour codes around it and the `--log` file
# prefixes every line with a timestamp and level.  Every line pattern below is
# anchored, so the line is normalised to start at "PROTO IP PORT" first.
_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
_LINE_HEAD = re.compile(r'(?<![\w.-])[A-Za-z][A-Za-z0-9]*\s+\d+\.\d+\.\d+\.\d+\s+\d+\s+\S+\s+\[')
# v2.387.0 — a row of a table nxc prints under a "[*]" line (the --shares
# table) has no "[" after the hostname, so it needs its own anchor.
_TABLE_HEAD = re.compile(r'(?<![\w.-])[A-Z][A-Za-z0-9]*\s+\d+\.\d+\.\d+\.\d+\s+\d+\s+\S+\s+')
# "SMB  172.30.77.10  445  LABSMB  public   READ   Parser lab read-only share"
_TABLE_ROW = re.compile(r'^(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\S+\s+(?!\[)(.*)$')


def _normalise_line(line: str) -> str:
    line = _ANSI_ESCAPE.sub('', line).strip()
    head = _LINE_HEAD.search(line) or _TABLE_HEAD.search(line)
    return line[head.start():] if head else line


class NetexecParser:
    """Parser for NetExec output with confidence-based conflict resolution"""

    def __init__(self, db: Session):
        self.db = db
        self.confidence_service = ConfidenceService()
        self.dedup_service = HostDeduplicationService(db)

        # Regex patterns for different netexec output formats.  The hostname
        # column is `\S+`, not `\w+`: Windows' default names are hyphenated
        # (WIN-…, DESKTOP-…) and LDAP prints an FQDN, and `\w+` dropped every
        # such line — a whole file imported as "no hosts".
        self.patterns = {
            # Basic host discovery
            'host_basic': re.compile(
                r'(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\S+)\s+\[([^\]]+)\]\s+(.*)'
            ),

            # SMB enumeration patterns
            'smb_enum': re.compile(
                r'SMB\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\S+)\s+\[([^\]]+)\]\s+'
                r'(?:Windows\s+)?([^(]+)\s*\(name:([^)]+)\)\s*\(domain:([^)]+)\)'
            ),

            # Share enumeration
            'smb_shares': re.compile(
                r'SMB\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\S+\s+\[([^\]]+)\]\s+'
                r'Enumerated shares.*?'
            ),

            # Authentication success
            'auth_success': re.compile(
                r'(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\S+\s+\[\+\]\s+(.*)'
            ),

            # LDAP enumeration
            'ldap_enum': re.compile(
                r'LDAP\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\S+)\s+\[([^\]]+)\]'
            ),

            # Service banners
            'service_banner': re.compile(
                r'(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\S+\s+\[([^\]]+)\]\s+'
                r'(?:Name:|Banner:|Version:)\s*(.*)'
            )
        }

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        """Parse netexec output file"""
        self._project_id = kwargs.get("project_id")
        self._hosts_recorded = 0
        self._filename = filename
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

            # Fail closed on 0 hosts (same rule as naabu/smbmap): a completed
            # `tool_name='netexec'` scan with no hosts hid a parser that had
            # matched nothing behind a "successful" import.
            if not self._hosts_recorded:
                raise ValueError(
                    f"NetExec parser found no host lines in {filename}; "
                    f"file is empty or not NetExec output."
                )

            logger.info(f"Successfully parsed netexec output: {filename}")

            # v2.342.0 — this was the one host-creating parser that never ran
            # scope correlation, so a host first seen by NetExec stayed "out
            # of scope" until some other upload or a scope edit re-correlated
            # the project.  Same best-effort shape as the other parsers: the
            # host data is committed first so a correlation failure cannot
            # lose it.
            self.db.commit()
            try:
                correlate_scan(self.db, scan.id)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("NetExec scan %s correlation failed: %s", scan.id, exc)
                try:
                    self.db.rollback()
                except Exception:  # pragma: no cover
                    pass
            return scan

        except Exception as e:
            logger.error(f"Error parsing netexec file {filename}: {e}")
            raise

    def _is_json_content(self, content: str) -> bool:
        """Check if content is JSON format.  A console capture can start with
        "[*] First time use detected": that is a status marker, not an array
        (it used to log "Failed to parse JSON" before falling back)."""
        content = content.strip()
        return content.startswith('{') or (content.startswith('[') and not re.match(r'\[[*+!-]\]', content))

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
                # v2.387.0 — spider_plus writes one file per host,
                # "<ip>.json", holding {share: {path: {size, mtime…}}} with the
                # address ONLY in the file name.  Nothing inside is an IP, so
                # the file failed with "no host lines".
                if not self._hosts_recorded:
                    ip_match = re.search(r'(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?!\.?\d)', self._filename or '')
                    if ip_match and self._looks_like_ip(ip_match.group(1)) and all(
                        isinstance(v, dict) for v in data.values()
                    ):
                        self._process_json_host_data(ip_match.group(1), data, scan_id)

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
        # Every parsed line, grouped by IP in file order.  The host is written
        # ONCE per IP (from its most detailed line), but each further line is
        # still evidence: keeping only the first line per IP dropped every
        # auth success (the banner always comes first) and every service after
        # the first (SMB 445 recorded, WinRM 5985 lost).
        observations: Dict[str, List[Dict[str, Any]]] = {}
        # v2.387.0 — the --shares table: "[*] Enumerated shares", a header
        # ("Share  Permissions  Remark"), a rule, then one row per share, all
        # prefixed "SMB ip port host".  Only the heading line matched a
        # pattern, so the shares were dropped.  The columns are cut at the
        # header's offsets: an empty Permissions cell is just spaces.
        shares: Dict[str, List[Dict[str, Any]]] = {}
        share_columns: Dict[str, Tuple[int, int]] = {}

        for line in lines:
            line = _normalise_line(line)
            if not line or line.startswith('#'):
                continue

            row = _TABLE_ROW.match(line)
            if row:
                ip, rest = row.group(2), row.group(4)
                if rest.startswith('Share') and 'Permissions' in rest:
                    share_columns[ip] = (rest.index('Permissions'), rest.index('Remark') if 'Remark' in rest else len(rest))
                    shares.setdefault(ip, [])
                    continue
                if ip in share_columns:
                    if set(rest.replace(' ', '')) <= {'-'}:
                        continue
                    perm_at, remark_at = share_columns[ip]
                    name = rest[:perm_at].strip()
                    if name:
                        shares[ip].append({
                            'name': name,
                            'permissions': rest[perm_at:remark_at].strip() or None,
                            'remark': rest[remark_at:].strip() or None,
                        })
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

            if host_data:
                observations.setdefault(host_data['ip_address'], []).append(host_data)

        for ip_observations in observations.values():
            # The SMB banner names the host, its OS, domain and signing
            # posture; an auth line carries none of that.  Order in the file
            # must not decide which one describes the host.
            primary = next(
                (o for o in ip_observations if o.get('os_name')), ip_observations[0]
            )
            if shares.get(primary['ip_address']):
                primary['shares'] = shares[primary['ip_address']]
            host = self._process_host_with_confidence(primary, scan_id, primary['raw_line'])
            seen_ports = {primary.get('port')}
            for observation in ip_observations:
                if observation is primary:
                    continue
                self._process_additional_observation(host, observation, scan_id, seen_ports)

    def _process_additional_observation(
        self, host: models.Host, host_data: Dict[str, Any], scan_id: int, seen_ports: set
    ):
        """A further line about a host already written in this scan: its
        auth/enumeration result and, when the service is new, its port.  The
        host row itself is not rewritten, so one file never logs a confidence
        "conflict" against itself."""
        raw_line = host_data['raw_line']
        if not host.hostname and host_data.get('hostname'):
            host.hostname = host_data['hostname']
        # `_store_netexec_result` is keyed (scan, host, protocol, port,
        # username), so a repeated line is still one row.
        self._store_netexec_result(host.id, scan_id, host_data, raw_line)

        port = host_data.get('port')
        if port and port not in seen_ports:
            seen_ports.add(port)
            scan_type, data_source, method = self.confidence_service.detect_netexec_scan_type(raw_line)
            confidence = self.confidence_service.create_confidence_score(
                scan_type=scan_type,
                data_source=data_source,
                method=method,
                timestamp=datetime.now(),
                additional_factors=host_data.get('confidence_factors', {}),
            )
            self._process_port_with_confidence(host.id, scan_id, host_data, confidence)

    def _parse_smb_enum_line(self, match, full_line: str) -> Dict[str, Any]:
        """Parse SMB enumeration line"""
        ip, port, hostname, status, os_info, name, domain = match.groups()

        # netexec reports the SMB signing posture inline, e.g.
        # "(signing:False)" / "(signing:True)".  Extract to the queryable
        # host column rather than leaving it in the raw line.
        # v2.387.0 — (signing:True) is "required", (signing:False) "not
        # required" (see app.services.smb_signing); they were written as
        # "enabled" / "disabled", the opposite of nmap's "enabled".
        smb_signing = None
        low = full_line.lower()
        if "signing:false" in low:
            smb_signing = smb_signing_states.NOT_REQUIRED
        elif "signing:true" in low:
            smb_signing = smb_signing_states.REQUIRED

        return {
            'ip_address': ip,
            'port': int(port),
            'hostname': name.strip(),
            'domain': domain.strip(),
            'os_name': os_info.strip(),
            'smb_signing': smb_signing,
            'protocol': 'smb',
            'confidence_factors': {
                'enumeration_success': True,
                'detailed_info': True,
                'multiple_data_points': 3
            },
            'raw_line': full_line
        }

    @staticmethod
    def _parse_credential(details: str):
        """Pull (domain, username) out of a NetExec auth-success detail string.

        NetExec prints ``[+] DOMAIN\\username:password (Pwn3d!)`` (the trailing
        ``(...)`` flag is optional, and similar for LDAP/WinRM).  Returns
        ``(domain, username)`` with either possibly None.  A NULL/guest session
        prints a blank username (``DOMAIN\\:`` → username ``''``), which is the
        signal the weak-auth hygiene lens keys on — so we preserve the empty
        string rather than collapsing it to None.
        """
        if not details:
            return None, None
        identity = details.strip().split(':', 1)[0]  # drop password (+flags)
        if '\\' in identity:
            domain, username = identity.split('\\', 1)
        else:
            domain, username = None, identity
        return (domain or None), username

    def _parse_auth_success_line(self, match, full_line: str) -> Dict[str, Any]:
        """Parse authentication success line"""
        protocol, ip, port, details = match.groups()
        domain, username = self._parse_credential(details)

        return {
            'ip_address': ip,
            'port': int(port),
            'protocol': protocol.lower(),
            'auth_success': True,
            'username': username,
            'domain': domain,
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

        data = {
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
        # "[-] DOMAIN\user:pass STATUS_LOGON_FAILURE" is a failed login: a
        # login result, so it can clear an older guest success (v2.388.1).
        if status.strip() == '-':
            # The credential's domain is what was TRIED, not the host's own:
            # it is not written to the host.
            _domain, username = self._parse_credential(details)
            data.update(auth_success=False, username=username, details=details.strip())
        return data

    def _process_json_host_data(self, ip_address: str, data: Any, scan_id: int):
        """Process host data from JSON output"""
        if not self._looks_like_ip(ip_address):
            return

        host_data = {
            'ip_address': ip_address,
            # spider_plus is an SMB module: the result is the host's SMB service.
            'protocol': 'smb',
            'port': 445,
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
        self._hosts_recorded = getattr(self, '_hosts_recorded', 0) + 1

        # SMB signing posture → queryable host column (don't clobber a prior
        # observation with None when this line didn't report it).
        if host_data.get('smb_signing'):
            host.smb_signing = host_data['smb_signing']

        # Store netexec-specific results
        self._store_netexec_result(host.id, scan_id, host_data, raw_output)

        # Process port information if available
        if 'port' in host_data:
            self._process_port_with_confidence(
                host.id, scan_id, host_data, confidence
            )

        return host

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

        if not port_number:
            return

        # NetExec's "protocol" (smb/ldap/winrm/mssql/…) is the SERVICE, not the
        # IP transport — every one of them runs over TCP. Storing that service
        # string in the transport `protocol` column made a physical port (e.g.
        # 445) collide in the dedup key (host, port_number, protocol) with the
        # SAME port from an nmap/masscan TCP scan, producing a duplicate open
        # row that inflated open_port_count (assist + the /hosts page both count
        # port rows). Transport is tcp; the NXC protocol is the service name.
        nxc_service = host_data.get('protocol')  # SMB, LDAP, WinRM, MSSQL, …
        port_data = {
            'port_number': port_number,
            'protocol': 'tcp',
            'state': 'open',  # netexec only reports open/accessible ports
            'service_name': nxc_service,
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
                    host_id=object_id if object_type == 'host' else None,
                    port_id=object_id if object_type == 'port' else None,
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
            # Flush so a repeat of the same (subject, field_name) later in
            # the same scan — e.g. one IP appearing on both an SMB and an
            # LDAP netexec line — is found by the existence check above
            # instead of inserting a second row.  Required now that
            # host_confidence/port_confidence carry a UNIQUE(subject,
            # field_name) constraint (the session runs autoflush=False, so
            # without this the duplicate insert only surfaces as an
            # IntegrityError at commit).  Same v2.72.0 pattern as the
            # vulnerability + host-attribute write paths.
            self.db.flush()

    def _store_netexec_result(self, host_id: int, scan_id: int, host_data: Dict[str, Any], raw_output: str):
        """Store netexec-specific enumeration results.

        One row per (scan, host, protocol, port, username): a scan is one
        observation, so a later upload of the same file is a new scan and a
        new row BY DESIGN (that is the per-scan evidence trail).  Within one
        scan, the same line appearing twice used to insert twice (v2.332.0).
        """
        protocol = host_data.get('protocol', 'unknown')
        port = host_data.get('port')
        username = host_data.get('username')
        duplicate = (
            self.db.query(NetexecResult.id)
            .filter(
                NetexecResult.scan_id == scan_id,
                NetexecResult.host_id == host_id,
                NetexecResult.protocol == protocol,
                NetexecResult.port == port,
                NetexecResult.username == username,
            )
            .first()
        )
        if duplicate is not None:
            return

        result = NetexecResult(
            scan_id=scan_id,
            host_id=host_id,
            protocol=host_data.get('protocol', 'unknown'),
            port=host_data.get('port'),
            hostname=host_data.get('hostname'),
            domain_name=host_data.get('domain'),
            # v2.388.1 — None unless the line IS a login result: the SMB
            # banner and a spider_plus listing were stored False and shown as
            # "Auth failed", and (latest row per host/port) could hide a real
            # guest login from the weak-auth condition.
            auth_success=host_data.get('auth_success'),
            username=host_data.get('username'),
            shares=host_data.get('shares'),
            raw_output=raw_output[:10000],  # Limit size
            connection_stable=True,
            multiple_confirmations=len(host_data.get('confidence_factors', {})) > 2
        )

        self.db.add(result)
        # autoflush is off: flush so the existence check above sees this row
        # for a repeat later in the same file.
        self.db.flush()

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