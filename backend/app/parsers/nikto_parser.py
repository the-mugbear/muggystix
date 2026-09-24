from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import VulnerabilitySource
from app.parsers.streaming_json import iter_json_records
from app.parsers.parser_utils import (
    ScanClock,
    correlate_scan,
    ensure_scan,
    extract_first_ip,
    map_text_severity,
    normalize_ip,
    persist_host_observation,
    upsert_vulnerability,
)
from app.services.dns_name_service import ObservationCache, bind_hostname
from app.services.host_deduplication_service import HostDeduplicationService


TARGET_IP_PATTERN = re.compile(r"Target IP:\s*((?:\d{1,3}\.){3}\d{1,3})", re.IGNORECASE)
TARGET_HOST_PATTERN = re.compile(r"Target Host(?:name)?:\s*([^\s]+)", re.IGNORECASE)
TARGET_PORT_PATTERN = re.compile(r"Target Port:\s*(\d+)", re.IGNORECASE)
FINDING_PATTERN = re.compile(r"^\+\s+(.*)")
# `+ Start Time:   2024-04-01 02:00:00 (GMT-4)` / `+ End Time: … (GMT-4) (95 seconds)`.
# The (GMT±h) suffix makes it an absolute instant; without it the value is the
# scanner's wall clock.  These are run metadata, not findings.
TIME_LINE_PATTERN = re.compile(
    r"^\+\s+(Start|End) Time:\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"(?:\s+\(GMT([+-]?)(\d{1,2})(?::?(\d{2}))?\))?",
    re.IGNORECASE,
)
HOSTS_TESTED_PATTERN = re.compile(r"^\+\s+\d+\s+host\(s\)\s+tested", re.IGNORECASE)
# v2.387.0 — lines describing the run or the server, not a finding: they were
# stored as LOW vulnerabilities ("Platform: Unknown", "Server: openresty",
# "8234 requests: 4 errors and 11 items reported…").
RUN_METADATA_PATTERN = re.compile(
    r"^(?:server|platform|ssl info|message|root page / redirects to)\s*:"
    r"|^no cgi directories found"
    r"|^\d+\s+requests?:"
    r"|^\d+\s+error\(s\)",
    re.IGNORECASE,
)
BRACKET_ID_PATTERN = re.compile(r"^\[(\d+)\]\s+(.*)$")
OSVDB_ID_PATTERN = re.compile(r"^(OSVDB-\d+):\s*(.*)$", re.IGNORECASE)


# A top-level JSON OBJECT below this size is read whole (see _parse_json).
_WHOLE_OBJECT_LIMIT = 200 * 1024 * 1024


def _target_of(host_entry: dict) -> dict:
    """The target fields of a Nikto JSON host object, for its findings."""
    return {k: host_entry[k] for k in ("ip", "host", "hostname", "port") if host_entry.get(k) is not None}


class NiktoParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)
        self._name_cache = ObservationCache()

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name="nikto",
            scan_type="web_vulnerability_scan",
            project_id=self._project_id,
        )
        suffix = Path(filename).suffix.lower()
        # Only the text report carries run times; JSON/CSV have none.
        self._clock = ScanClock(models.SCAN_TIME_TOOL_RUN)

        if suffix == ".json":
            self._parse_json(file_path, scan)
        elif suffix == ".csv":
            self._parse_csv(file_path, scan)
        else:
            self._parse_text(file_path, scan)

        self._clock.apply(scan)
        correlate_scan(self.db, scan.id)
        return scan

    def _parse_json(self, file_path: str, scan: models.Scan) -> None:
        # Nikto 2.5 writes ONE host object ({ip, port, …, vulnerabilities:
        # [...]}) for a single target.  Streamed with the array keys below,
        # the reader unwrapped ``vulnerabilities`` and dropped the parent's
        # ip/port, so every finding was skipped and the job "succeeded" with
        # nothing (review 2026-09-23 R6; R01 of 09-21).  A top-level object is
        # read whole (one host's findings are small) and keeps its target.
        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            head = handle.read(4096).lstrip()
        if head.startswith("{") and os.path.getsize(file_path) < _WHOLE_OBJECT_LIMIT:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                try:
                    payload = json.load(handle)
                except json.JSONDecodeError:
                    payload = None
            if isinstance(payload, dict):
                self._record_json_entries(scan, [payload])
                return
        # Stream the records — Nikto JSON exports for large scopes are
        # dominated by per-finding ``description``/``msg`` bodies and
        # can easily reach several hundred MB.
        entries = iter_json_records(
            file_path,
            array_keys=("vulnerabilities", "findings"),
            tool_label="Nikto JSON",
        )
        self._record_json_entries(scan, entries)

    def _record_json_entries(self, scan: models.Scan, entries) -> None:
        for entry in entries:
            # A wrapper object carrying the findings under "findings".
            wrapped = entry.get("findings")
            if isinstance(wrapped, list) and not entry.get("msg"):
                self._record_json_entries(scan, [
                    {**_target_of(entry), **item} for item in wrapped if isinstance(item, dict)
                ])
                continue
            # v2.387.0 — Nikto's own ``-Format json`` is a list of HOST
            # objects, each holding its findings under ``vulnerabilities``
            # ({id, msg, url, method, references}) with the target on the
            # parent.  The host object used to be recorded as one empty
            # "Nikto finding" and every real finding was lost.
            nested = entry.get("vulnerabilities")
            if isinstance(nested, list):
                for item in nested:
                    if isinstance(item, dict):
                        self._record_json_finding(scan, {**_target_of(entry), **item})
                continue
            self._record_json_finding(scan, entry)

    def _record_json_finding(self, scan: models.Scan, entry: dict) -> None:
        ip_address = extract_first_ip(str(entry.get("ip") or entry.get("targetip") or entry.get("host") or ""))
        if not ip_address:
            return
        msg = entry.get("msg")
        if not msg and not entry.get("id"):
            return  # a target with nothing reported
        hostname = entry.get("hostname") or entry.get("host")
        port = self._coerce_port(entry.get("port")) or 80
        refs = entry.get("references")
        description = entry.get("description") or msg
        if refs and isinstance(refs, str) and description and refs not in description:
            description = f"{description}\nSee: {refs}"
        # Nikto 2.5 JSON keeps the path in ``url`` and some messages depend on
        # it ("contains 1 entry which should be manually viewed." is about
        # /robots.txt; "This might be interesting." about /public/).  The
        # text output prints "<path>: <msg>"; do the same when the message
        # does not start with a path itself.  "/" adds nothing and would
        # split the header checks by path.
        url = str(entry.get("url") or "").strip()
        title = str(msg or entry.get("id"))
        if msg and url and url != "/" and not str(msg).lstrip().startswith("/"):
            title = f"{url}: {msg}"
        self._record_finding(
            scan=scan,
            ip_address=ip_address,
            hostname=hostname,
            port=port,
            title=title,
            description=description,
            plugin_id=str(entry.get("id") or entry.get("osvdb") or "") or None,
            cve_id=entry.get("cve"),
            severity=map_text_severity(entry.get("severity")) if entry.get("severity") else map_text_severity("low"),
            # v2.390.0 — the reference link in the references column (it went
            # into the description only), and the request that found it.
            references=[str(refs)] if refs and isinstance(refs, str) else None,
            request=" ".join(p for p in (entry.get("method"), entry.get("url")) if p) or None,
        )

    def _parse_csv(self, file_path: str, scan: models.Scan) -> None:
        with open(file_path, "r", encoding="utf-8", errors="ignore", newline="") as handle:
            first = handle.readline()
            handle.seek(0)
            if first.lstrip('"').lower().startswith("nikto"):
                self._parse_native_csv(handle, scan)
                return
            reader = csv.DictReader(handle)
            for row in reader:
                ip_address = extract_first_ip(str(row.get("ip") or row.get("targetip") or row.get("host") or ""))
                if not ip_address:
                    continue
                port = self._coerce_port(row.get("port")) or 80
                self._record_finding(
                    scan=scan,
                    ip_address=ip_address,
                    hostname=row.get("hostname") or row.get("host"),
                    port=port,
                    title=str(row.get("msg") or row.get("id") or "Nikto finding"),
                    description=row.get("description") or row.get("msg"),
                    plugin_id=row.get("id") or row.get("osvdb"),
                    cve_id=row.get("cve"),
                    severity=map_text_severity(row.get("severity")) if row.get("severity") else map_text_severity("low"),
                )

    def _parse_native_csv(self, handle, scan: models.Scan) -> None:
        """Nikto's own ``-Format csv``: a ``"Nikto - vX/"`` banner and NO
        header row, then positional columns — hostname, ip, port, reference
        (a URL in 2.6; ``OSVDB-n`` in older releases), method, uri, message.
        A row with an empty message is the target line, not a finding.
        v2.387.0: this was read as a headed CSV, every row found no ``ip``
        column, and the file imported with nothing."""
        for row in csv.reader(handle):
            if len(row) < 7 or row[0].lower().startswith("nikto"):
                continue
            hostname, ip_raw, port_raw, ref, _method, uri, message = (c.strip() for c in row[:7])
            ip_address = extract_first_ip(ip_raw) or extract_first_ip(hostname)
            if not ip_address or not message:
                continue
            plugin_id = ref if ref and not ref.lower().startswith("http") else None
            title = message if not uri or message.startswith(uri) else f"{uri}: {message}"
            description = f"{title}\nSee: {ref}" if ref and plugin_id is None else title
            self._record_finding(
                scan=scan,
                ip_address=ip_address,
                hostname=hostname if hostname and hostname != ip_address else None,
                port=self._coerce_port(port_raw) or 80,
                title=title,
                description=description,
                plugin_id=plugin_id,
                cve_id=None,
                severity=map_text_severity("low"),
            )

    def _observe_time_line(self, match: re.Match) -> None:
        try:
            wall = datetime.strptime(match.group(2), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return
        if match.group(4) is not None:
            offset = timedelta(hours=int(match.group(4)), minutes=int(match.group(5) or 0))
            if match.group(3) == "-":
                offset = -offset
            try:
                zone = timezone(offset)
            except ValueError:
                # "(GMT+99)": not a real offset.  Drop the time, keep the file.
                return
            self._clock.observe(wall.replace(tzinfo=zone))
        else:
            self._clock.observe_clock(wall)

    def _parse_text(self, file_path: str, scan: models.Scan) -> None:
        current_ip: Optional[str] = None
        current_hostname: Optional[str] = None
        current_port = 80

        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                ip_match = TARGET_IP_PATTERN.search(line)
                if ip_match:
                    current_ip = extract_first_ip(ip_match.group(1))
                host_match = TARGET_HOST_PATTERN.search(line)
                if host_match:
                    current_hostname = host_match.group(1).strip()
                port_match = TARGET_PORT_PATTERN.search(line)
                if port_match:
                    current_port = int(port_match.group(1))

                # v2.333.0 — run metadata, not findings: these lines used to
                # match FINDING_PATTERN and every host got a LOW "Start Time:
                # …" vulnerability, while the scan window stayed empty.
                time_match = TIME_LINE_PATTERN.match(line.strip())
                if time_match:
                    self._observe_time_line(time_match)
                    continue
                if HOSTS_TESTED_PATTERN.match(line.strip()):
                    continue

                finding_match = FINDING_PATTERN.match(line.strip())
                if finding_match and current_ip:
                    message = finding_match.group(1).strip()
                    if message.lower().startswith("target") or RUN_METADATA_PATTERN.match(message):
                        continue
                    # 2.6: "[013587] /: Suggested security header missing: x. See: <url>"
                    # — the bracketed number is the check id.  Older releases:
                    # "OSVDB-3092: /admin/: …".  Anything else has no id (the
                    # text before the first colon used to be taken as one,
                    # so "/admin/" became a plugin id).
                    plugin_id = None
                    bracket = BRACKET_ID_PATTERN.match(message)
                    if bracket:
                        plugin_id, message = bracket.group(1), bracket.group(2).strip()
                    else:
                        osvdb = OSVDB_ID_PATTERN.match(message)
                        if osvdb:
                            plugin_id, message = osvdb.group(1), osvdb.group(2).strip()
                    title, _, ref = message.partition(" See: ")
                    self._record_finding(
                        scan=scan,
                        ip_address=current_ip,
                        hostname=current_hostname,
                        port=current_port,
                        title=title.strip(),
                        description=f"{title.strip()}\nSee: {ref.strip()}" if ref else title.strip(),
                        plugin_id=plugin_id,
                        cve_id=None,
                        severity=map_text_severity("low"),
                    )

    def _record_finding(
        self,
        *,
        scan: models.Scan,
        ip_address: str,
        hostname: Optional[str],
        port: int,
        title: str,
        description: Optional[str],
        plugin_id: Optional[str],
        cve_id: Optional[str],
        severity,
        references: Optional[list] = None,
        request: Optional[str] = None,
    ) -> None:
        # Nikto run against an address reports that address as its "host"
        # (JSON ``host``, text ``Target Hostname:``).  Stored as the host's
        # name, it then blocked real names from later imports, since an
        # equal-rank name is never replaced (review 2026-09-23 C6e).  One
        # guard for the JSON, text and CSV paths.
        if hostname and normalize_ip(hostname):
            hostname = None
        host, port_map = persist_host_observation(
            dedup_service=self.dedup_service,
            scan_id=scan.id,
            ip_address=ip_address,
            hostname=hostname,
            ports=[{"port_number": port, "protocol": "tcp", "state": "open", "service_name": "http"}],
            project_id=self._project_id,
        )
        persisted_port = port_map.get((port, "tcp"))
        # Phase 3 — nikto tested a NAME (Host header selects the vhost); the
        # finding belongs to that named endpoint, the host is where it was
        # observed.  Also records the HTTP observation name→address.
        name_id = bind_hostname(
            self.db, project_id=self._project_id, hostname=hostname,
            ip_address=ip_address, scan_id=scan.id, cache=self._name_cache,
        )
        upsert_vulnerability(
            db=self.db,
            host_id=host.id,
            scan_id=scan.id,
            source=VulnerabilitySource.NIKTO,
            title=title,
            severity=severity,
            plugin_id=plugin_id,
            port_id=persisted_port.id if persisted_port else None,
            description=description,
            cve_id=cve_id,
            name_id=name_id,
            # One Nikto id covers several distinct results (013587 = every
            # missing security header).
            key_on_title=True,
            references=references,
            plugin_output=request,
        )

    def _coerce_port(self, value: object) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(str(value))
        except ValueError:
            return None
