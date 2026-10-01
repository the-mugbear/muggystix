from __future__ import annotations

import re
from typing import Dict, List

from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import (
    ProgressBeat, beat_while_reading, correlate_scan, ensure_scan, extract_first_ip,
    normalize_ip, persist_host_observation,
)
from app.services.host_deduplication_service import HostDeduplicationService


OPEN_LINE_PATTERN = re.compile(r"open\s+((?:\d{1,3}\.){3}\d{1,3}):(\d+)", re.IGNORECASE)
LIST_PATTERN = re.compile(r"((?:\d{1,3}\.){3}\d{1,3}).*?\[([0-9,\s]+)\]")
# IPv6 (review 2026-10-01 B12).  RustScan prints a Rust `SocketAddr`, whose
# IPv6 form is bracketed — `Open [2001:db8::1]:443` — and a bare `IpAddr` in
# the summary line, `2001:db8::1 -> [443,80]`.  The candidate is validated as
# an address before it is used.
OPEN_LINE_V6_PATTERN = re.compile(r"open\s+\[([0-9A-Fa-f:.]+)\]:(\d+)", re.IGNORECASE)
LIST_V6_PATTERN = re.compile(r"(?:^|\s)([0-9A-Fa-f]*:[0-9A-Fa-f:.]+)\s+->\s+\[([0-9,\s]+)\]")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
# nmap's port table header / report line, printed after RustScan's own lines.
_NMAP_REPORT_LINE = re.compile(r"^PORT\s+STATE\s+SERVICE|^Nmap scan report for ", re.IGNORECASE)


class RustScanParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)
        self.last_parse_stats: dict | None = None

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        project_id = kwargs.get("project_id")
        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name="rustscan",
            scan_type="port_scan",
            project_id=project_id,
        )
        hosts: Dict[str, List[dict]] = {}
        # RustScan hands its ports to nmap and prints nmap's report in the
        # same console output.  That report (services, versions, scripts) is
        # not read here; say so rather than let it look imported (v2.417.0,
        # review R11/R12).
        embedded_nmap = False

        def add_port(address: str, port: int) -> None:
            if 0 < port <= 65535:
                hosts.setdefault(address, []).append(
                    {"port_number": port, "protocol": "tcp", "state": "open"}
                )

        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            # R6 — the read phase heartbeats (only the committed scan row exists).
            for line in beat_while_reading(handle, "lines read"):
                if "\x1b" in line:
                    line = _ANSI.sub("", line)
                if not embedded_nmap and _NMAP_REPORT_LINE.search(line):
                    embedded_nmap = True
                v6 = OPEN_LINE_V6_PATTERN.search(line)
                if v6:
                    address = normalize_ip(v6.group(1))
                    if address and ":" in address:
                        add_port(address, int(v6.group(2)))
                        continue
                v6_list = LIST_V6_PATTERN.search(line)
                if v6_list:
                    address = normalize_ip(v6_list.group(1))
                    if address and ":" in address:
                        for port_text in v6_list.group(2).split(","):
                            if port_text.strip().isdigit():
                                add_port(address, int(port_text.strip()))
                        continue
                match = OPEN_LINE_PATTERN.search(line)
                if match:
                    ip_address = extract_first_ip(match.group(1))
                    port = int(match.group(2))
                    if ip_address:
                        hosts.setdefault(ip_address, []).append(
                            {"port_number": port, "protocol": "tcp", "state": "open"}
                        )
                    continue

                list_match = LIST_PATTERN.search(line)
                if list_match:
                    ip_address = extract_first_ip(list_match.group(1))
                    if not ip_address:
                        continue
                    for port_text in list_match.group(2).split(","):
                        port_text = port_text.strip()
                        if port_text.isdigit():
                            hosts.setdefault(ip_address, []).append(
                                {
                                    "port_number": int(port_text),
                                    "protocol": "tcp",
                                    "state": "open",
                                }
                            )

        if not hosts:
            # An import of nothing is not a success (it used to finish
            # "processed successfully" with zero hosts).
            raise ValueError(
                "No RustScan open-port lines found (expected `Open 10.0.0.5:22` or "
                "`10.0.0.5 -> [22,80]`; IPv6 as `Open [2001:db8::1]:22`)."
                + (" The file holds an nmap report: import nmap's own output (-oX) for it."
                   if embedded_nmap else "")
            )

        # R6 — heartbeat between hosts (no savepoint wraps a host here; each
        # host's rows are written within its own iteration).
        beat = ProgressBeat("hosts", every=200)
        for ip_address, ports in hosts.items():
            beat.tick()
            persist_host_observation(
                dedup_service=self.dedup_service,
                scan_id=scan.id,
                ip_address=ip_address,
                ports=ports,
                project_id=project_id,
            )

        correlate_scan(self.db, scan.id)
        port_count = sum(len(p) for p in hosts.values())
        self.last_parse_stats = {
            "skipped": 0,
            "warnings": (
                "This file also holds nmap's report (services, versions, scripts), which the "
                "RustScan import does not read: only open ports were imported. Import nmap's "
                "XML (pass `-- -oX out.xml` to RustScan) to keep them."
                if embedded_nmap else None
            ),
            "summary": f"{port_count} open port{'s' if port_count != 1 else ''} on {len(hosts)} host{'s' if len(hosts) != 1 else ''}",
            "partial": False,
        }
        return scan
