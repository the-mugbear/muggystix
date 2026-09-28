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
# Hosts between progress reports (each report is also where a cancel stops
# the parse, and it commits the work so far).
_PROGRESS_EVERY_HOSTS = 250

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

# nxc prints this in the hostname column (and in "(name:…)") when the service
# gave it no name — no name, not a host called "None" (v2.428.4).
_NO_NAME = {'none', 'null', '-', ''}


def _host_name(value: Optional[str], ip: Optional[str] = None) -> Optional[str]:
    name = (value or '').strip()
    if name.lower() in _NO_NAME or name == ip:
        return None
    return name


def _is_credential(details: str) -> bool:
    """Whether a "[+]" / "[-]" detail starts with a credential — ``user:pass``,
    ``DOMAIN\\user[:pass]``, ``DOMAIN\\:`` (null session) — and so reports a
    login.  Everything else on those lines is a command or module result:
    ``[-] ERROR(MANTIS\\SQLEXPRESS): Line 1: …`` was stored as a failed login
    by ``SQLEXPRESS)`` (v2.428.4, MCP acceptance feedback #12), ``[+] Dumped …``
    as a login by a user named "Dumped …".  The first word decides: it carries
    ``:`` or ``\\`` and no parenthesis."""
    words = (details or '').split()
    if not words:
        return False
    token = words[0]
    return (':' in token or '\\' in token) and '(' not in token and ')' not in token


# v2.430.0 — nxc's `nfs --shares` table (nxc/protocols/nfs.py `shares()`,
# NetExec main 2026-09-28): a header "UID  Perms  Storage Usage  Share  Access
# List", a rule, then one row per export — mounted, "0  rw-  1.2GB/9.8GB
# /srv  10.0.0.0/24, 10.1.0.0/24"; not mountable, "-  ---  ---/---  /srv
# Everyone".  The access list is the export's groups joined with ", ", and
# "Everyone" when it names none.  Only the SMB header was recognised, so every
# row was dropped (production 2026-09-28: 2 364 lines over ~600 NFS servers).
_NFS_ANY_HOST = {'everyone', '*'}
_NFS_NO_NETWORK = 'no network'


def _nfs_share_row(rest: str, share_at: int, access_at: int) -> Optional[Dict[str, Any]]:
    """One export row of the NFS share table, cut at the header's offsets.
    A share longer than nxc's 30-character column pushes the access list
    right, so it is then cut at the first space after the share instead."""
    head = rest[:share_at].split()
    tail = rest[share_at:]
    if len(head) < 3 or not tail.strip():
        return None
    uid, perms, storage = head[0], head[1], ' '.join(head[2:])
    width = access_at - share_at
    if len(tail) > width and tail[width - 1] == ' ':
        name, access = tail[:width].strip(), tail[width:].strip()
    else:
        name, _, access = tail.strip().partition(' ')
        access = access.strip()
    if not name:
        return None
    access_list = [] if access.lower() == _NFS_NO_NETWORK else [a.strip() for a in access.split(',') if a.strip()]
    mounted = uid != '-'
    return {
        'protocol': 'nfs',
        'name': name,
        # nxc's r/w/x for the UID it mounted as; nothing when it could not mount.
        'permissions': perms if mounted else None,
        # Read after the permissions ("rwx · mountable from: …"), so the
        # access list is named for what it is, not "access" twice.
        'remark': ' · '.join([
            'nxc could not mount it' if not mounted else f"{storage} used",
            f"mountable from: {', '.join(access_list) or 'no network'}",
        ]),
        'uid': uid if mounted else None,
        'storage': storage if mounted else None,
        'access_list': access_list,
    }


def nfs_open_exports(shares: Any) -> List[str]:
    """The NFS exports whose access list lets any host mount them."""
    if not isinstance(shares, list):
        return []
    return [
        str(s.get('name')) for s in shares
        if isinstance(s, dict) and s.get('protocol') == 'nfs'
        and any(str(a).lower() in _NFS_ANY_HOST for a in (s.get('access_list') or []))
    ]


def netexec_check_evidence(check_id: str, line: str, shares: Any = None) -> str:
    """What a catalog observation shows as the tool's output: the line, or
    for an open NFS export, the exports themselves (the banner names none)."""
    if check_id == 'nfs_export_any_host':
        return f"NFS exports any host may mount (nxc --shares): {', '.join(nfs_open_exports(shares))}"
    return line


def netexec_line_checks(
    protocol: Optional[str], line: str, *, username: Optional[str] = None,
    auth_success: Optional[bool] = None, smbv1: Optional[bool] = None,
    shares: Any = None,
) -> List[str]:
    """The catalog checks one NetExec result reports (v2.412.0; a pure
    function since v2.414.0 so the backfill reads stored rows by the same
    rule).  The flags are read from the line itself, as nxc prints them
    (nxc/protocols/{smb,vnc,ftp,nfs}.py); an NFS row's export table too."""
    protocol = (protocol or '').lower()
    low = (line or '').lower()
    found: List[str] = []
    if protocol == 'nfs':
        # "(root escape:True)": nxc read the server's root filesystem through
        # an export.  False and None (no NFSv3 to try) are not findings.
        if '(root escape:true)' in low:
            found.append('nfs_root_escape')
        if nfs_open_exports(shares):
            found.append('nfs_export_any_host')
        return found
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
    # The NFS banner's claim is its root escape, which is read (v2.430.0).
    if protocol == 'nfs' and 'supported nfs versions' in line.lower():
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
    return any(_share_writable(s) for s in shares if isinstance(s, dict))


def _share_writable(share: Dict[str, Any]) -> bool:
    permissions = str(share.get('permissions') or '')
    if share.get('protocol') == 'nfs':
        # nxc's "rwx" triple for the UID it mounted the export as.
        return 'w' in permissions
    return 'WRITE' in permissions.upper()


class NetexecParser:
    """Parser for NetExec output with confidence-based conflict resolution"""

    def __init__(self, db: Session):
        self.db = db
        self.confidence_service = ConfidenceService()
        self.dedup_service = HostDeduplicationService(db)
        # (host_id, port_number) → port id, resolved this parse (v2.422.0).
        self._port_ids: Dict[Tuple[int, int], int] = {}

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
                # v2.421.0 — "Windows" stays in the OS name: the optional
                # prefix was matched OUTSIDE the group, so hosts read
                # "Server 2019 Standard 17763" / "10 Build 19041 x64".
                r'((?:Windows\s+)?[^(]+)\s*\(name:([^)]+)\)\s*\(domain:([^)]+)\)'
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
        self._port_ids: Dict[Tuple[int, int], int] = {}
        logger.info(f"Starting netexec parse of {filename}")

        # Create scan record
        scan = models.Scan(
            filename=filename,
            scan_type='netexec',
            tool_name='netexec'
        )
        self.db.add(scan)
        self.db.flush()
        # v2.422.0 — progress reports commit as the parse goes, so a cancelled
        # or failed import has a committed partial scan: named here so the
        # dispatcher removes it, as for nmap / masscan.
        self._created_scan_id = scan.id

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
        # v2.430.0 — keyed by (ip, port), and the NFS export table read too:
        # one host's SMB and NFS tables are two services' shares.
        shares: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        # ip → (kind, port, first cut, second cut) of the table open for it.
        share_columns: Dict[str, Tuple[str, int, int, int]] = {}
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
                ip, port, rest = row.group(2), int(row.group(3)), row.group(4)
                if rest.startswith('Share') and 'Permissions' in rest:
                    share_columns[ip] = ('smb', port, rest.index('Permissions'),
                                         rest.index('Remark') if 'Remark' in rest else len(rest))
                    shares.setdefault((ip, port), [])
                    continue
                if (row.group(1).upper() == 'NFS' and rest.startswith('UID')
                        and 'Storage Usage' in rest and 'Access List' in rest):
                    share_columns[ip] = ('nfs', port, rest.index('Share'), rest.index('Access List'))
                    shares.setdefault((ip, port), [])
                    continue
                if ip in share_columns:
                    kind, table_port, first_cut, second_cut = share_columns[ip]
                    if set(rest.replace(' ', '')) <= {'-'}:
                        continue
                    if kind == 'nfs':
                        entry = _nfs_share_row(rest, first_cut, second_cut)
                        if entry:
                            shares[(ip, table_port)].append(entry)
                        else:
                            gaps.append(('dropped_table_row', line))
                        continue
                    name = rest[:first_cut].strip()
                    if name:
                        shares[(ip, table_port)].append({
                            'name': name,
                            'permissions': rest[first_cut:second_cut].strip() or None,
                            'remark': rest[second_cut:].strip() or None,
                        })
                    continue
            else:
                # The table ends at the IP's next status line.  Left open, every
                # later bracket-less row for the IP — --rid-brute, --users,
                # --pass-pol, a second run appended to the log — was stored as a
                # share ("500: LAB\Adminis", review 2026-09-23 C6d).  nxc
                # reports an export it could not list mid-table ("[-] Failed
                # to list share: …") and goes on with the next row.
                status = _STATUS_ROW.match(line)
                if status and not (
                    share_columns.get(status.group(2), ('',))[0] == 'nfs'
                    and 'failed to list share:' in line.lower()
                ):
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
                if match and not match.group(4).strip().lower().startswith(_ACTION_RESULTS) and (
                    match.group(1).lower() in _PASSWORD_ONLY_PROTOCOLS
                    or _is_credential(match.group(4))
                ):
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

        # v2.422.0 — progress, and a point where a cancel takes effect.  A
        # 10 000-host sweep ran for minutes showing nothing, was cancelled as
        # "hung" from the UI, and kept writing to the end regardless
        # (production, 2026-09-26): report_progress raises ParseFailure once
        # the job is cancelled, which stops the parse here.
        # Each table belongs to the service it was read from: the line on its
        # port that describes the host (the SMB banner) or the first one.
        for (ip, port), table in shares.items():
            on_port = [o for o in observations.get(ip, []) if o.get('port') == port]
            target = next((o for o in on_port if o.get('os_name')), on_port[0] if on_port else None)
            if table and target is not None:
                target['shares'] = table

        from app.services.ingestion_service import report_progress
        total_hosts = len(observations)
        for index, ip_observations in enumerate(observations.values(), start=1):
            if index == 1 or index % _PROGRESS_EVERY_HOSTS == 0:
                report_progress(f"{index - 1}/{total_hosts} hosts")
            # The SMB banner names the host, its OS, domain and signing
            # posture; an auth line carries none of that.  Order in the file
            # must not decide which one describes the host.
            primary = next(
                (o for o in ip_observations if o.get('os_name')), ip_observations[0]
            )
            host =self._process_host_with_confidence(primary, scan_id, primary['raw_line'])
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
        # v2.422.0 — the port this parse already resolved, when it has; the
        # catalog write looked it up again for every host.
        port_id = self._port_ids.get((host.id, host_data.get('port')))
        shares = host_data.get('shares')
        for check_id in netexec_line_checks(
            host_data.get('protocol'), line, username=host_data.get('username'),
            auth_success=host_data.get('auth_success'), smbv1=host_data.get('smbv1'),
            shares=shares,
        ):
            record_misconfig(
                self.db, check_id=check_id, host_id=host.id, scan_id=scan_id,
                source=VulnerabilitySource.NETEXEC, port_number=host_data.get('port'),
                evidence=netexec_check_evidence(check_id, line, shares), port_id=port_id,
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
            'hostname': _host_name(name, ip),
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
        # The first word is the credential; drop the password (and anything
        # after it). A Kerberos "DOMAIN\\user from ccache" has no password.
        identity = details.strip().split(None, 1)[0].split(':', 1)[0]
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
            'hostname': _host_name(hostname, ip),
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
        if status.strip() == '-' and (
            protocol.lower() in _PASSWORD_ONLY_PROTOCOLS or _is_credential(details)
        ):
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
        self._port_ids[(host_id, port_number)] = port.id

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