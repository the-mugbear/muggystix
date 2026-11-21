from __future__ import annotations

import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import (
    HostPortBuffer, ProgressBeat, beat_while_reading, ensure_scan, extract_first_ip,
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
        # RustScan hands its ports to nmap and prints nmap's report in the
        # same console output.  That report (services, versions, scripts) is
        # not read here; say so rather than let it look imported (v2.417.0,
        # review R11/R12).
        embedded_nmap = False

        # R6 — heartbeat between hosts, outside each host's savepoint.  One
        # host that fails to write is skipped and counted, never the whole
        # file (comprehensive review 2026-10-16, B-OPS-6).
        beat = ProgressBeat("hosts", every=200)
        skipped = 0

        def write(ip_address: str, ports: list) -> None:
            nonlocal skipped
            beat.tick()
            if persist_host_observation(
                dedup_service=self.dedup_service,
                scan_id=scan.id,
                ip_address=ip_address,
                ports=ports,
                project_id=project_id,
                isolate=True,
            ) is None:
                skipped += 1

        # Grouped by address and written at a bound: the whole file used to be
        # held, one dict per line, until the first write (review 2026-11-20).
        hosts = HostPortBuffer(write)

        def add_port(address: str, port: int) -> None:
            # Every form of line comes through here: the IPv4 ones took a
            # port number of any size.
            if 0 < port <= 65535:
                hosts.add(address, {"port_number": port, "protocol": "tcp", "state": "open"})

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
                        add_port(ip_address, port)
                    continue

                list_match = LIST_PATTERN.search(line)
                if list_match:
                    ip_address = extract_first_ip(list_match.group(1))
                    if not ip_address:
                        continue
                    for port_text in list_match.group(2).split(","):
                        port_text = port_text.strip()
                        if port_text.isdigit():
                            add_port(ip_address, int(port_text))

        if not hosts.records:
            # An import of nothing is not a success (it used to finish
            # "processed successfully" with zero hosts).
            raise ValueError(
                "No RustScan open-port lines found (expected `Open 10.0.0.5:22` or "
                "`10.0.0.5 -> [22,80]`; IPv6 as `Open [2001:db8::1]:22`)."
                + (" The file holds an nmap report: import nmap's own output (-oX) for it."
                   if embedded_nmap else "")
            )

        hosts.drain()

        # The hosts of the scan are its history rows (a buffer drained in
        # parts holds no list of them, and a skipped host has no row).
        port_count = hosts.records
        host_count = skipped + (
            self.db.query(func.count(models.HostScanHistory.id))
            .filter(models.HostScanHistory.scan_id == scan.id).scalar() or 0
        )
        self.last_parse_stats = {
            "skipped": skipped,
            "warnings": (
                "This file also holds nmap's report (services, versions, scripts), which the "
                "RustScan import does not read: only open ports were imported. Import nmap's "
                "XML (pass `-- -oX out.xml` to RustScan) to keep them."
                if embedded_nmap else None
            ),
            "summary": f"{port_count} open port{'s' if port_count != 1 else ''} on {host_count} host{'s' if host_count != 1 else ''}"
                       + (f"; {skipped} host{'s' if skipped != 1 else ''} could not be written and were skipped"
                          if skipped else ""),
            "partial": skipped > 0,
        }
        return scan
