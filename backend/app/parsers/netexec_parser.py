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
from app.db.models_confidence import (
    NETEXEC_RAW_OUTPUT_LIMIT, NetexecResult, HostConfidence, PortConfidence, ConflictHistory,
)
from app.services.confidence_service import (
    ConfidenceService, ScanType, DataSource, ConfidenceScore
)
from app.services.host_deduplication_service import HostDeduplicationService
from app.db.models_vulnerability import VulnerabilitySource
from app.parsers.parser_utils import correlate_scan, read_tool_text
from app.services import smb_signing as smb_signing_states
from app.services.misconfig_checks import record_misconfig
from app.services.line_shapes import ShapeTally, nxc_line_shape
import logging

logger = logging.getLogger(__name__)

RAW_OUTPUT_LIMIT = NETEXEC_RAW_OUTPUT_LIMIT

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
# ``(?![\s\[])``, not ``(?!\[)``: with the bare form ``\s+`` backtracked one
# space so the lookahead saw a space, and every status line ("[+] Brute
# forcing RIDs") matched as a table row too.
_TABLE_ROW = re.compile(r'^(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\S+\s+(?![\s\[])(.*)$')
# A status line ("[*] …", "[+] …") for an IP: it ends that IP's share table.
_STATUS_ROW = re.compile(r'^(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\S+\s+\[')


def _normalise_line(line: str) -> str:
    line = _ANSI_ESCAPE.sub('', line).strip()
    head = _LINE_HEAD.search(line) or _TABLE_HEAD.search(line)
    return line[head.start():] if head else line


# Protocols whose login is a password alone ("VNC … [+] badpassword"): the
# token is the credential, not a username.  The stored line keeps it — an
# analyst reads the credentials that worked from there (v2.411.0).
_PASSWORD_ONLY_PROTOCOLS = {'vnc'}
# "[+]" lines that report what nxc DID after logging in, not a login.  From
# nxc's protocols/ftp.py ("Uploaded: …", "Downloaded: …") and ssh.py
# ("Executed command", 'Created file "…" on "…"', 'File "…" was downloaded to
# "…"'), checked against NetExec main 2026-09-25.
_ACTION_RESULTS = ('executed command', 'uploaded:', 'downloaded:', 'created file ', 'file "')


def netexec_line_checks(
    protocol: Optional[str], line: str, *, username: Optional[str] = None,
    auth_success: Optional[bool] = None, smbv1: Optional[bool] = None,
) -> List[str]:
    """The catalog checks one NetExec result reports (v2.412.0; a pure
    function since v2.414.0 so the backfill reads stored rows by the same
    rule).  The flags are read from the line itself, as nxc prints them
    (nxc/protocols/{smb,vnc,ftp}.py)."""
    protocol = (protocol or '').lower()
    low = (line or '').lower()
    found: List[str] = []
    if protocol == 'smb':
        if 'signing:false' in low:
            found.append('smb_signing_not_required')
        if smbv1 is True or 'smbv1:true' in low:
            found.append('smbv1_enabled')
        # "(Null Auth:True)" / "(Guest Auth:True)" on the banner; a login
        # marked "(Guest)" was accepted as the guest.
        if '(null auth:true)' in low or '(guest auth:true)' in low or (
            auth_success is True and (
                (username is not None and username.strip().lower() in ('', 'guest'))
                or '(guest)' in low
            )
        ):
            found.append('smb_null_session')
    elif protocol == 'vnc' and '(no auth:true)' in low:
        found.append('vnc_no_auth')
    elif protocol == 'ftp' and auth_success is True and (
        # nxc prints an anonymous login as "[+] : - Anonymous Login!"
        (username is not None and username.strip().lower() in ('', 'anonymous'))
        or 'anonymous login' in low
    ):
        found.append('ftp_anonymous')
    return found


# nxc's protocols (nxc/protocols/*).  Anything else in the first column is a
# module's name — its result is not interpreted by these patterns.
_NXC_PROTOCOLS = {'smb', 'ldap', 'winrm', 'rdp', 'ssh', 'ftp', 'vnc', 'mssql', 'wmi', 'nfs'}
# "NAME  address  port  host  [x] …" with any first column and an IPv4 or
# IPv6 address — the layout of every nxc result line.
_RESULT_LAYOUT = re.compile(
    r'^[A-Za-z][\w-]*\s+(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]*:[0-9a-fA-F:]+)\s+\d+\s+\S+\s+\[[^\]]+\]\s'
)
# A "(key:value)" flag in a line's message.
_HAS_FLAG = re.compile(r'\([^():]{1,40}:[^()]*\)')
# A table cell: an nxc header ("-Last PW Set-", spaces inside) or a token.
_HEADER_CELL = re.compile(r'-[\w ]+?-')
_CELL = re.compile(r'-[\w ]+?-(?=\s|$)|\S+')


def _table_row_shape(line: str) -> str:
    """A bracket-less row (a --users / --rid-brute / module table): the
    columns' count and kind only.  Its cells are account names, dates and
    descriptions, which no redaction rule can tell from the tool's words; an
    nxc header cell ("-Username-") is kept."""
    parts = line.split(None, 4)
    if len(parts) < 5:
        return nxc_line_shape(line)
    cells = [
        tok if _HEADER_CELL.fullmatch(tok) else ('<N>' if tok.isdigit() else '<T>')
        for tok in _CELL.findall(parts[4])
    ]
    return f"{parts[0]} <IP> {parts[2]} <HOST> " + " ".join(cells)


def uninterpreted_kind(host_data: Dict[str, Any], line: str) -> Optional[str]:
    """Why a recognised line still says more than BlueStick read, or None.

    ``module`` — the first column is a module, not a protocol: its result
    went through the protocol patterns (as a login, or as text).
    ``text_only`` — a status line carrying a claim ("[+]", "[!]" or a
    "(key:value)" flag) that produced no login and no catalog check: kept as
    the tool's line only.  A plain "[*]" banner is what the pattern reads
    (host, port, banner) and is not reported."""
    protocol = (host_data.get('protocol') or '').lower()
    if protocol and protocol not in _NXC_PROTOCOLS:
        return 'module_as_login' if host_data.get('auth_success') is True else 'module_as_text'
    if host_data.get('auth_success') is not None or host_data.get('os_name'):
        return None
    if netexec_line_checks(protocol, line, smbv1=host_data.get('smbv1')):
        return None
    status = re.search(r'\s\[([^\]]+)\]\s', f' {line} ')
    claim = (status and status.group(1).strip() in ('+', '!')) or _HAS_FLAG.search(line)
    return 'text_only' if claim else None


def writable_share(shares: Any) -> Optional[bool]:
    """Whether a share table grants WRITE on any share (v2.412.0): the
    `has:writable_share` column.  None for anything but a share table — a
    spider_plus listing (an object) says nothing about permissions."""
    if not isinstance(shares, list):
        return None
    return any(
        'WRITE' in str((s or {}).get('permissions') or '').upper()
        for s in shares if isinstance(s, dict)
    )


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

            # Authentication success
            'auth_success': re.compile(
                r'(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+\S+\s+\[\+\]\s+(.*)'
            ),
            # v2.419.0 (review R6) — `smb_shares`, `ldap_enum` and
            # `service_banner` were defined and never used: the share table
            # has its own reader, and LDAP / banner lines are `host_basic`.
            # Lines no pattern reads are listed on the import (line_shapes).
        }

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        """Parse netexec output file"""
        self._project_id = kwargs.get("project_id")
        self._hosts_recorded = 0
        self._filename = filename
        self.uninterpreted = None
        self.last_parse_stats = None
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
            # v2.420.0 — UTF-16 (a PowerShell redirect) decoded, stray NUL
            # bytes removed: PostgreSQL text cannot hold NUL, and one NUL in a
            # stored line failed the whole file (production, 2026-09-26).
            read = read_tool_text(file_path)
            content = read.text
            self._read_notes = []
            if read.encoding != 'utf-8':
                self._read_notes.append(f"read as {read.encoding.upper()} (e.g. a PowerShell redirect)")
            if read.nul_removed:
                self._read_notes.append(
                    f"{read.nul_removed} NUL byte{'s' if read.nul_removed != 1 else ''} removed "
                    "(binary or corrupted content in the capture)"
                )

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
            notes = list(self._read_notes)
            if self.uninterpreted:
                # v2.418.0 — the import says which lines it did not read, as
                # redacted shapes (Ingestion Results; collect-logs.sh).
                total = self.uninterpreted["total"]
                notes.append(
                    f"{total} line{'s' if total != 1 else ''} not interpreted "
                    f"({self.uninterpreted['distinct']} shape{'s' if self.uninterpreted['distinct'] != 1 else ''}) — "
                    "kept as the tool's text or dropped; the shapes are listed on the import"
                )
            if notes:
                self.last_parse_stats = {
                    "skipped": 0,
                    "warnings": "; ".join(notes),
                    "summary": None,
                    "partial": False,
                    "uninterpreted": self.uninterpreted,
                }

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
        # v2.418.0 — the lines not interpreted, as redacted shapes (see
        # app/services/line_shapes.py), for the import's receipt.  Names seen
        # in the host column are replaced wherever they recur.
        host_names: set = set()
        # (kind, line) pairs, shaped once every host name is known.
        gaps: List[Tuple[str, str]] = []

        for line in lines:
            line = _normalise_line(line)
            if not line or line.startswith('#'):
                continue
            columns = _STATUS_ROW.match(line) or _TABLE_ROW.match(line)
            if columns:
                host_names.add(line.split()[3])

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
            else:
                # The table ends at the IP's next status line.  Left open, every
                # later bracket-less row for the IP — --rid-brute, --users,
                # --pass-pol, a second run appended to the log — was stored as a
                # share ("500: LAB\Adminis", review 2026-09-23 C6d).
                status = _STATUS_ROW.match(line)
                if status:
                    share_columns.pop(status.group(2), None)

            # Try different patterns
            host_data = None

            # Try SMB enumeration pattern first (most detailed)
            match = self.patterns['smb_enum'].match(line)
            if match:
                host_data = self._parse_smb_enum_line(match, line)

            # Try authentication success pattern
            if not host_data:
                match = self.patterns['auth_success'].match(line)
                # v2.412.0 — "[+] Uploaded: …", "[+] Executed command" report
                # an action, not a login (they were stored as logins by a user
                # named "Uploaded"); they fall through to the plain line.
                if match and not match.group(4).strip().lower().startswith(_ACTION_RESULTS):
                    host_data = self._parse_auth_success_line(match, line)

            # Try basic host pattern
            if not host_data:
                match = self.patterns['host_basic'].match(line)
                if match:
                    host_data = self._parse_basic_host_line(match, line)

            if host_data:
                observations.setdefault(host_data['ip_address'], []).append(host_data)
                kind = uninterpreted_kind(host_data, line)
                if kind:
                    gaps.append((kind, line))
            elif columns:
                # A line about a host that no pattern read: dropped.  (A line
                # with no host columns is nxc's own chatter, not a result.)
                gaps.append(('dropped_table_row' if not _STATUS_ROW.match(line) else 'dropped', line))
            elif _RESULT_LAYOUT.match(line):
                # nxc's result layout that the patterns above cannot read (a
                # hyphenated module name, an IPv6 address): dropped.  Lines
                # outside the layout — the command the operator typed (its
                # own credentials), a log prefix, nxc's chatter — are not
                # results and are never reported.
                gaps.append(('dropped', line))

        tally = ShapeTally()
        known = tuple(sorted(host_names, key=len, reverse=True))
        for kind, gap_line in gaps:
            shape = _table_row_shape(gap_line) if kind == 'dropped_table_row' else nxc_line_shape(gap_line, known)
            tally.add('dropped' if kind == 'dropped_table_row' else kind, shape)
        self.uninterpreted = tally.receipt()

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
        self._record_misconfigs(host, host_data, scan_id)

    def _record_misconfigs(self, host: models.Host, host_data: Dict[str, Any], scan_id: int) -> None:
        """v2.412.0 — the line's weaknesses as catalog observations
        (app/services/misconfig_checks.py), on the port the line is about."""
        line = host_data.get('raw_line') or ''
        for check_id in netexec_line_checks(
            host_data.get('protocol'), line, username=host_data.get('username'),
            auth_success=host_data.get('auth_success'), smbv1=host_data.get('smbv1'),
        ):
            record_misconfig(
                self.db, check_id=check_id, host_id=host.id, scan_id=scan_id,
                source=VulnerabilitySource.NETEXEC, port_number=host_data.get('port'), evidence=line,
            )

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
            # v2.390.0 — "(SMBv1:True|False)"; "(SMBv1:None)" says nothing.
            'smbv1': True if 'smbv1:true' in low else False if 'smbv1:false' in low else None,
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
        # v2.411.0 — a VNC login is a password alone ("[+] badpassword"):
        # the token was stored as the username.  The line keeps it.
        if protocol.lower() in _PASSWORD_ONLY_PROTOCOLS:
            domain, username = None, None

        return {
            'ip_address': ip,
            'port': int(port),
            'protocol': protocol.lower(),
            'auth_success': True,
            'username': username,
            'domain': domain,
            'details': details.strip(),
            # v2.390.0 — "(Pwn3d!)": the credential is a local administrator.
            'local_admin': True if '(pwn3d!)' in details.lower() else None,
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
            # v2.413.0 — nxc prints a line only for a host whose service
            # answered (the "[*] RFB 3.8" / "[*] Banner:" line is that answer):
            # the host is up.  Only a status containing "up" counted, so every
            # nxc-only host was "unknown".
            'state': 'up',
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
            if protocol.lower() in _PASSWORD_ONLY_PROTOCOLS:
                username = None
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

        # v2.412.0 — the line's weaknesses (SMBv1 since v2.390.0; signing,
        # null / guest sessions, VNC no-auth, anonymous FTP) through the one
        # catalog, so nmap and NetExec name them the same.
        self._record_misconfigs(host, host_data, scan_id)

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

        A repeat for the same identity UPGRADES the row rather than being
        dropped: in a spray, ``[-] alice:Winter LOGON_FAILURE`` followed by
        ``[+] alice:Summer (Pwn3d!)`` kept only the failure, losing the valid
        credential and the local-admin flag (review 2026-09-23 C6b).  A
        success outranks a failure, local admin is OR'd, and blanks fill.
        """
        protocol = host_data.get('protocol', 'unknown')
        port = host_data.get('port')
        username = host_data.get('username')
        duplicate = (
            self.db.query(NetexecResult)
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
            new_success = host_data.get('auth_success')
            if new_success is True and duplicate.auth_success is not True:
                duplicate.auth_success = True
                # The evidence line is the one that proved the login.
                duplicate.raw_output = raw_output[:RAW_OUTPUT_LIMIT]
            elif duplicate.auth_success is None and new_success is not None:
                duplicate.auth_success = new_success
            if host_data.get('local_admin'):
                duplicate.local_admin = True
            for column, key in (('smbv1', 'smbv1'), ('shares', 'shares'),
                                ('hostname', 'hostname'), ('domain_name', 'domain')):
                if getattr(duplicate, column) is None and host_data.get(key) is not None:
                    setattr(duplicate, column, host_data.get(key))
            if duplicate.writable_share is None:
                duplicate.writable_share = writable_share(host_data.get('shares'))
            self.db.flush()
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
            raw_output=raw_output[:RAW_OUTPUT_LIMIT],
            tool=host_data.get('tool', 'netexec'),
            local_admin=host_data.get('local_admin'),
            smbv1=host_data.get('smbv1'),
            writable_share=writable_share(host_data.get('shares')),
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