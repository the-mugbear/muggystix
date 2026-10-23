"""Masscan parser with batch ingestion and deduplicated persistence."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from lxml import etree

from app.parsers.cut_short import EndTracking, settle_cut, xml_cut_short
from app.parsers.streaming_json import ends_early
from app.parsers.xml_stream_helpers import clear_element, iterparse_safe, strip_namespace
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import (
    ProgressBeat, ScanClock, announce_scan, beat_while_reading, correlate_scan, epoch_to_utc, normalize_ip,
    without_nul,
)
from app.services import scan_sightings
from app.services.host_deduplication_service import HostDeduplicationService, should_replace_service

logger = logging.getLogger(__name__)

# Peak-memory backstop (review A-4).  The collectors group masscan's
# RANDOMLY-ORDERED (ip, port) records into an in-memory {ip: [ports]} map so a
# host's scattered ports collapse to a single upsert — that grouping is
# load-bearing and must NOT be chunked away.  But a pathological multi-/16
# sweep can buffer millions of port dicts at once and OOM the worker (which
# then re-queues into the same OOM).  So we flush to the DB once this many
# DISTINCT IPs are buffered.  Set deliberately HIGH: a normal scan (≤ a /16 =
# 65k IPs) never reaches it, so it behaves exactly as before — full grouping,
# one upsert per IP, exact counts.  Only a genuinely huge scan flushes, trading
# some repeat host-upserts for bounded RAM.
_COLLECT_FLUSH_IPS = 100_000
# Review 2026-10-01 R6 — heartbeat while COLLECTING, every this many records.
_READ_BEAT_EVERY = 50_000


def _port_reason(raw: Any) -> Optional[str]:
    """Why masscan calls the port open (``syn-ack``).  A banner record repeats
    the port with ``reason="response"`` — that is the banner arriving, not the
    port's state — so it is not a reason."""
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()[:50]
    return None if not value or value == "response" else value


class MasscanParser:
    """Parse Masscan output formats with batch DB operations."""

    XML_SCHEMA_REFERENCE = "https://nmap.org/book/nmap-dtd.html"

    def __init__(self, db: Session):
        self.db = db
        self._written: Dict[str, set] = {}

    def _address(self, written: Any) -> Optional[str]:
        """The address a record names, in the one spelling a host is stored
        in (``HostDeduplicationService.host_address``) — or None, and the
        record is counted unread, when it names none (review 2026-10-23b,
        ING-1: the raw text was inserted as the host, so one bad token failed
        the whole import and one IPv6 address written two ways was two
        hosts)."""
        try:
            address, other = HostDeduplicationService.host_address(written)
        except ValueError:
            self._unread_lines += 1
            return None
        if other:
            self._written.setdefault(address, set()).add(other)
        return address

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        """Dispatch to format-specific parsers based on file extension."""
        self._project_id = kwargs.get("project_id")
        if self._project_id is None:
            # Every scan and host belongs to a project (e3b7a1c9d5f0).
            raise ValueError("MasscanParser.parse_file needs project_id")
        # v2.390.0 — `--banners` output: (ip, port, proto, kind) → the latest banner.
        self._banners: Dict[Tuple[str, int, str, str], str] = {}
        self._truncated_at: Optional[str] = None
        # Records decoded before JSON text that is not a record (None = none).
        self._json_unread_after: Optional[int] = None
        self._unread_lines = 0
        # The other spellings the file wrote each address in (``_address``).
        self._written: Dict[str, set] = {}
        self._dedup = HostDeduplicationService(self.db)
        self.last_parse_stats = None
        start = time.time()
        scan = self._create_scan_record(filename)
        # JSON records and list lines carry per-record epoch timestamps; the
        # XML format records a run start/finish instead (set directly).
        self._clock = ScanClock()

        try:
            logger.info(
                "Masscan parser starting for %s (scan_id=%s)",
                filename,
                scan.id,
            )
            suffix = Path(filename).suffix.lower()

            # Phase 1+2 interleaved: collect host/port data and persist it.
            # The collector buffers into an {ip: [ports]} map and calls this
            # sink whenever the buffer crosses _COLLECT_FLUSH_IPS distinct IPs
            # (and once more for the residual), draining it to the DB so peak
            # RAM stays bounded on huge scans (review A-4).  For a normal scan
            # the threshold is never hit, so this fires exactly once on the
            # full map — identical to the old collect-all-then-persist path.
            counters = {"hosts": 0, "ips": 0}

            def _flush(buf: Dict[str, List[Dict[str, Any]]]) -> None:
                if not buf:
                    return
                counters["ips"] += len(buf)
                counters["hosts"] += self._batch_persist(scan.id, buf)
                # The banners whose ports are now stored, written with them
                # (review 2026-10-22, A-R6): they were held for the whole file
                # and written in one loop at its end, with no heartbeat.
                self._store_banners(scan.id)

            if suffix == ".xml":
                residual = self._collect_xml(file_path, scan, _flush)
            elif suffix == ".json":
                residual = self._collect_json(file_path, _flush)
            else:
                residual = self._collect_list(file_path, _flush)

            _flush(residual)
            self._clock.apply(scan)
            # What is left names no port this import stored or found.
            self._store_banners(scan.id, final=True)

            if counters["hosts"] == 0:
                # Fail closed — pre-v2.55.0 this path committed an
                # empty scan and returned success, which let the
                # dispatcher's unconditional masscan_list fallback (also
                # removed in v2.54.0) silently produce a
                # `tool_name='masscan'` scan for arbitrary `.txt` /
                # `.json` uploads.  Now an empty result is a parse
                # failure; the dispatcher will try the next attempt or
                # surface a `parse_errors` row.
                raise ValueError(
                    f"Masscan parser found 0 hosts in {filename}; "
                    f"file is empty or not masscan output."
                )

            total_unique_ips = counters["ips"]
            processed_hosts = counters["hosts"]

            # Commit parsed host data before correlation
            self.db.commit()

            elapsed = time.time() - start
            logger.info(
                "Masscan parser persisted %d hosts / %d unique IPs in %.2fs (filename=%s)",
                processed_hosts,
                total_unique_ips,
                elapsed,
                filename,
            )

            try:
                correlate_scan(self.db, scan.id)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Masscan scan %s correlation failed: %s", scan.id, exc)
                try:
                    self.db.rollback()
                except Exception:
                    pass

            # A file that was CUT SHORT (XML or JSON) never reaches here
            # unless the operator asked to keep what it holds; the dispatcher
            # then marks the import partial and says where the file ended.
            # What follows is INVALID content, which keeps what precedes it.
            #
            # v2.419.0 (review H4) — XML that stops being XML keeps the hosts
            # read before the break, and says the rest is unknown rather than
            # importing as complete.
            warnings: List[str] = []
            if self._truncated_at:
                warnings.append(
                    f"The XML ended early ({self._truncated_at}); the {processed_hosts} host"
                    f"{'s' if processed_hosts != 1 else ''} before that point were imported, "
                    "anything after it is not in the inventory."
                )
            elif self._json_unread_after is not None:
                # JSON that stops being JSON.  The records before it were
                # always kept; until 2026-10-11 the text, and every record
                # after it, were dropped with a log line and the import looked
                # complete.
                after = self._json_unread_after
                warnings.append(
                    f"After {after:,} record{'s' if after != 1 else ''} the file holds text that "
                    "is not a masscan record; it, and anything after it, could not be read and "
                    "is not in the inventory."
                )
            unread = self._unread_lines
            if unread:
                # N9 — lines and records that were not read (reported the way
                # the sibling parsers report skipped rows): a list line that
                # is not a record, and in every format a record whose address
                # is not an IP address (review 2026-10-23b, ING-1).
                warnings.append(
                    f"{unread} record{'s' if unread != 1 else ''} of the file named no IP address "
                    "and were skipped"
                    if suffix in (".xml", ".json") else
                    f"{unread} line{'s' if unread != 1 else ''} of the list file were not masscan "
                    "records and were skipped"
                )
            if warnings:
                self.last_parse_stats = {
                    "skipped": unread,
                    "warnings": " ".join(warnings),
                    "summary": None,
                    "partial": True,
                }
            return scan
        except Exception:
            self.db.rollback()
            logger.exception("Masscan parser failed for %s", filename)
            raise

    # ------------------------------------------------------------------
    # Phase 1 — Collect data from file into {ip: [ports]} dict
    # ------------------------------------------------------------------

    def _maybe_flush(
        self,
        host_ports: Dict[str, List[Dict[str, Any]]],
        flush: Optional[Callable[[Dict[str, List[Dict[str, Any]]]], None]],
    ) -> None:
        """Drain ``host_ports`` to the DB once it buffers too many distinct IPs.

        ``flush`` pops every entry, so on return the map is empty and the
        collector keeps accumulating from where it left off.  No-op when no
        sink is wired (e.g. a direct unit-test call) or the threshold isn't met.
        """
        if flush is not None and len(host_ports) >= _COLLECT_FLUSH_IPS:
            flush(host_ports)

    def _collect_xml(
        self,
        file_path: str,
        scan: models.Scan,
        flush: Optional[Callable[[Dict[str, List[Dict[str, Any]]]], None]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        host_ports: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        # Complete <host> elements read (masscan writes one per port): what
        # precedes a cut.
        records_read = 0

        read_beat = ProgressBeat("records read", every=_READ_BEAT_EVERY)
        # Read through a handle that knows when the reader reached its end
        # (how a file that was cut short is told from invalid XML).
        source = EndTracking.open(file_path)
        try:
            # See xml_stream_helpers.iterparse_safe for the hardening
            # flag rationale (XXE / billion-laughs / huge-tree defense).
            context = iterparse_safe(source)
            for event, elem in context:
                tag = strip_namespace(elem.tag)

                if event == "start" and tag in {"nmaprun", "masscan"}:
                    # Review 2026-10-01 R4 — masscan writes nmap's XML dialect
                    # (<nmaprun scanner="masscan">), so this parser read ANY
                    # nmap file: tried as the fallback after the nmap parser
                    # failed, it imported open ports only — no services,
                    # scripts or OS — and the job completed.  A root that
                    # names another scanner is not masscan output.
                    scanner = (elem.get("scanner") or "").strip().lower()
                    if scanner and scanner != "masscan":
                        raise ValueError(
                            f"Not masscan XML: the file says it was written by '{scanner}'."
                        )
                    scan.version = elem.get("version")
                    scan.command_line = elem.get("args")
                    scan.tool_name = elem.get("scanner", "masscan")
                    scan.start_time = self._parse_timestamp(elem.get("start"))
                    if scan.start_time is not None:
                        scan.time_source = models.SCAN_TIME_TOOL_RUN

                if event == "end" and tag == "host":
                    # R6 — the read phase heartbeats (a file of few hosts and
                    # very many records never reaches the flush threshold).
                    read_beat.tick()
                    records_read += 1
                    host_info = self._extract_xml_host(elem)
                    if host_info:
                        host_ports[host_info["ip_address"]].extend(host_info["ports"])
                    clear_element(elem)
                    self._maybe_flush(host_ports, flush)
                elif event == "end" and tag == "finished":
                    # Epoch only: `timestr` is the scanner's zone-less local
                    # clock and would not be comparable with the epoch start.
                    end_time = self._parse_timestamp(elem.get("time"))
                    if end_time is not None and scan.start_time is not None and end_time >= scan.start_time:
                        scan.end_time = end_time
        except etree.XMLSyntaxError as exc:
            line = getattr(exc, "lineno", "?")
            position = getattr(exc, "position", None)
            column = position[1] if isinstance(position, tuple) and len(position) > 1 else "?"
            logger.warning(
                "Masscan XML parsing halted for %s near line=%s column=%s after %s records: %s",
                file_path, line, column, records_read, exc,
            )
            if xml_cut_short(source, file_path):
                # The file simply ENDS.  Settled in the one place (owner,
                # 2026-10-11): this raises (the import fails as cut short and
                # the operator is asked) unless the import keeps what the file
                # holds; then the records read so far are imported and the
                # dispatcher marks it partial.
                settle_cut(records_read, label="Masscan XML", cause=exc)
            else:
                # INVALID XML at this point — not the owner's question, and
                # handled as it always was (v2.419.0, H4): the hosts read
                # before it are kept and the import is partial, with the same
                # warning.
                self._truncated_at = f"near line {line}"
        finally:
            source.close()

        # v2.333.0 — no utcnow() fallback: a truncated file has no known end,
        # and the upload time is not when the scan finished.
        return dict(host_ports)

    def _collect_json(
        self,
        file_path: str,
        flush: Optional[Callable[[Dict[str, List[Dict[str, Any]]]], None]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        host_ports: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        for entry in beat_while_reading(self._iter_json_entries(file_path), every=_READ_BEAT_EVERY):
            written = entry.get("ip") or entry.get("addr")
            if not written:
                continue
            ip_address = self._address(written)
            if ip_address is None:
                continue
            self._observe_epoch(entry.get("timestamp"))
            for port_info in entry.get("ports", []):
                try:
                    port_number = int(port_info.get("port"))
                except (TypeError, ValueError):
                    continue
                protocol = port_info.get("proto", "tcp")
                state = port_info.get("status", "open")
                if state != "open":
                    continue
                service = port_info.get("service")
                if isinstance(service, dict) and service.get("banner"):
                    self._note_banner(ip_address, port_number, protocol, service.get("name"), service.get("banner"))
                host_ports[ip_address].append({
                    "port_number": port_number,
                    "protocol": protocol,
                    "state": state,
                    "reason": _port_reason(port_info.get("reason")),
                })
            # Flush at entry boundaries so an IP's ports from one record stay
            # together in the same persist call.
            self._maybe_flush(host_ports, flush)

        return dict(host_ports)

    def _collect_list(
        self,
        file_path: str,
        flush: Optional[Callable[[Dict[str, List[Dict[str, Any]]]], None]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        host_ports: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        gnmap_lines_seen = 0

        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            # NUL removed (review 2026-10-01 R5): a banner line is stored, and
            # PostgreSQL text cannot hold NUL.
            for line in beat_while_reading(without_nul(handle), "lines read", every=_READ_BEAT_EVERY):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Masscan verbose/greppable-style lines:
                #   Timestamp: 1234567890\tHost: 10.0.0.1 ()\tPorts: 80/open/tcp//http//
                if line.startswith("Timestamp:") and "Host:" in line and "Ports:" in line:
                    parsed = self._parse_timestamp_line(line)
                    if parsed:
                        written, ports = parsed
                        ip_address = self._address(written)
                        if ip_address is None:
                            continue
                        ts_match = re.match(r"Timestamp:\s*(\d+)", line)
                        if ts_match:
                            self._observe_epoch(ts_match.group(1))
                        host_ports[ip_address].extend(ports)
                        self._maybe_flush(host_ports, flush)
                    continue

                # Detect gnmap/greppable format lines that don't belong here
                if line.startswith("Host:") and ("Ports:" in line or "Status:" in line):
                    gnmap_lines_seen += 1
                    continue

                # Simple list format: open tcp 80 10.0.0.1
                parts = line.split()
                if len(parts) < 4:
                    # Review 2026-10-01 N9 — counted: a line that is not a
                    # list record was dropped without the import saying so.
                    self._unread_lines += 1
                    continue
                state, protocol, port_str, written = parts[:4]
                if state not in ("open", "banner"):
                    continue
                ip_address = self._address(written)
                if ip_address is None:
                    continue
                # `--banners`: "banner tcp 80 10.0.0.1 <epoch> <kind> <text…>"
                if state == "banner" and len(parts) >= 7 and port_str.isdigit():
                    self._note_banner(ip_address, int(port_str), protocol, parts[5], line.split(None, 6)[6])
                    continue
                if state != "open":
                    continue
                try:
                    port_number = int(port_str)
                except ValueError:
                    self._unread_lines += 1
                    continue
                # `open tcp 80 10.0.0.1 1711938600` — 5th field is epoch.
                if len(parts) >= 5:
                    self._observe_epoch(parts[4])
                host_ports[ip_address].append({
                    "port_number": port_number,
                    "protocol": protocol,
                    "state": state,
                })
                self._maybe_flush(host_ports, flush)

        if gnmap_lines_seen > 0 and not host_ports:
            logger.warning(
                "Masscan _collect_list detected %d gnmap-format lines in %s. "
                "This file appears to be greppable output (e.g. masscan -oG or nmap -oG). "
                "It should be uploaded with a .gnmap extension for correct parsing.",
                gnmap_lines_seen,
                file_path,
            )

        return dict(host_ports)

    # ------------------------------------------------------------------
    # Phase 2 — Batch persist, through the dedup service's set-based form
    # (``upsert_hosts_bulk`` / ``upsert_ports_bulk``: its rules, a few
    # statements per 500 hosts for scans of tens of thousands).
    # ------------------------------------------------------------------

    def _batch_persist(
        self,
        scan_id: int,
        host_ports: Dict[str, List[Dict[str, Any]]],
    ) -> int:
        """Persist all collected host/port data with minimal DB round-trips."""
        all_ips = list(host_ports.keys())
        total_ips = len(all_ips)
        logger.info("Masscan batch persist: %d unique IPs to process", total_ips)

        # --- Step 1: Upsert hosts in batches --------------------------------
        BATCH = 500
        processed_hosts = 0
        processed_ports = 0

        for i in range(0, total_ips, BATCH):
            batch_ips = all_ips[i : i + BATCH]
            ip_to_host_id = self._dedup.upsert_hosts_bulk(
                self._project_id, scan_id, {ip: self._written.get(ip, ()) for ip in batch_ips},
            )
            processed_hosts += len(ip_to_host_id)

            if (i + BATCH) % 2000 == 0 or i + BATCH >= total_ips:
                logger.info(
                    "Masscan batch persist: hosts %d/%d",
                    min(i + BATCH, total_ips),
                    total_ips,
                )
                from app.services.ingestion_service import report_progress
                report_progress(f"{min(i + BATCH, total_ips)}/{total_ips} hosts")

            # --- Step 2: Upsert only the ports for the current host batch ----
            batch_port_rows = self._build_port_rows_for_hosts(batch_ips, ip_to_host_id, host_ports)
            processed_ports += len(batch_port_rows)
            if batch_port_rows:
                self._dedup.upsert_ports_bulk(scan_id, batch_port_rows)
                logger.info(
                    "Masscan batch persist: ports %d processed after hosts %d/%d",
                    processed_ports,
                    min(i + BATCH, total_ips),
                    total_ips,
                )

            # Release the host batch now that both host and port data are persisted.
            for ip in batch_ips:
                host_ports.pop(ip, None)

        logger.info("Masscan batch persist: %d unique ports processed", processed_ports)
        return processed_hosts

    def _build_port_rows_for_hosts(
        self,
        ips: List[str],
        ip_to_host_id: Dict[str, int],
        host_ports: Dict[str, List[Dict[str, Any]]],
    ) -> List[Tuple[int, Dict[str, Any]]]:
        rows: List[Tuple[int, Dict[str, Any]]] = []
        for ip in ips:
            host_id = ip_to_host_id.get(ip)
            if host_id is None:
                continue

            seen: Dict[Tuple[int, str], Dict[str, Any]] = {}
            for port_data in host_ports.get(ip, []):
                key = (port_data["port_number"], str(port_data.get("protocol", "tcp")).lower())
                kept = seen.get(key)
                if kept is not None:
                    # masscan writes a port once per record (the SYN-ACK, then
                    # one per banner): the first record is kept, and takes the
                    # reason from whichever record carries one, and the
                    # service name the one merge rule prefers (a later
                    # ``Ports:`` line's name was dropped).
                    if not kept.get("reason") and port_data.get("reason"):
                        kept["reason"] = port_data["reason"]
                    if should_replace_service(kept.get("service_name"), 0, port_data.get("service_name"), 0):
                        kept["service_name"] = port_data.get("service_name")
                    continue
                seen[key] = dict(port_data)
                rows.append((host_id, seen[key]))
        return rows

    # ------------------------------------------------------------------
    # Line-level parsers (unchanged)
    # ------------------------------------------------------------------

    def _parse_timestamp_line(self, line: str) -> Optional[tuple]:
        """Parse a masscan ``Timestamp: … Host: … Ports: …`` line.

        Returns ``(ip_address, ports_list)`` or *None* on failure.
        """
        host_match = re.search(r"Host:\s+(\S+)", line)
        if not host_match:
            return None
        ip_address = host_match.group(1)

        ports_match = re.search(r"Ports:\s+(.*)", line)
        if not ports_match:
            return None

        ports: List[Dict[str, Any]] = []
        for port_entry in ports_match.group(1).split(","):
            # Each entry looks like: 80/open/tcp//http//
            fields = port_entry.strip().split("/")
            if len(fields) < 3:
                continue
            try:
                port_number = int(fields[0])
            except ValueError:
                continue
            state = fields[1] if fields[1] else "open"
            if state != "open":
                continue
            protocol = fields[2] if fields[2] else "tcp"
            service_name = fields[4] if len(fields) > 4 and fields[4] else None
            port_dict: Dict[str, Any] = {
                "port_number": port_number,
                "protocol": protocol,
                "state": state,
            }
            if service_name:
                port_dict["service_name"] = service_name
            ports.append(port_dict)

        if not ports:
            return None
        return ip_address, ports

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _create_scan_record(self, filename: str) -> models.Scan:
        scan = models.Scan(
            filename=filename,
            scan_type="port_scan",
            tool_name="masscan",
            project_id=self._project_id,
        )
        self.db.add(scan)
        self.db.flush()
        announce_scan(self.db, scan)
        return scan

    def _note_banner(self, ip: str, port: int, protocol: Optional[str], kind: Optional[str], banner: str) -> None:
        # XML carries the bytes escaped ("\x0d\x0a"); JSON already decoded them.
        text = re.sub(r"\\x0d\\x0a|\\x0a", "\n", str(banner)).replace("\\x0d", "").replace("\r", "").strip()
        if text and hasattr(self, "_banners"):
            # Keyed, so a repeated banner replaces the earlier one instead of
            # accumulating (the list held every banner of the file).
            self._banners[(ip, port, protocol or "tcp", (kind or "banner").strip() or "banner")] = text[:20000]

    _BANNER_CHUNK = 1000

    def _store_banners(self, scan_id: int, *, final: bool = False) -> None:
        """v2.390.0 — `--banners` output as script output on its port
        (``masscan-<kind>``: http.server, title, http, ssh …), shown in the
        inspector beside nmap's.  It was read past and dropped in all three
        formats.  A banner is written once its port is stored; one whose
        port is not (yet) in the project waits for the next flush — a list
        file's ``banner`` line may come before its ``open`` line reaches the
        database — and at the ``final`` call it is dropped (not open).

        Called after every flush of the collected ports (review 2026-10-22,
        A-R6): the banners were held for the whole file, unbounded by the
        flush threshold the ports have, and written in one loop at the end
        with no heartbeat.  Each chunk is followed by a heartbeat (the rows
        carry their sightings, so the cleanup finds them by scan).

        Batched per 1,000 addresses: the ports in one query, their existing
        masscan scripts in one more, one flush.  It was a query per banner
        (plus the script lookup and a flush each) — ~5 statements per banner,
        minutes for ``--banners`` over a /16 (review 2026-09-23 R5)."""
        if not self._banners:
            return
        by_ip: Dict[str, List[Tuple[int, str, str, str]]] = {}
        for (ip, port, proto, kind), banner in self._banners.items():
            by_ip.setdefault(ip, []).append((port, proto, kind, banner))
        self._banners = {}
        beat = ProgressBeat("banner addresses", every=self._BANNER_CHUNK)
        ips = list(by_ip)
        for start in range(0, len(ips), self._BANNER_CHUNK):
            chunk = ips[start:start + self._BANNER_CHUNK]
            # A host stored in another spelling of the address is found as
            # ``upsert_hosts_bulk`` found it, the canonical row first.
            spellings = set(chunk).union(*(self._written.get(ip, ()) for ip in chunk))
            port_ids: Dict[Tuple[str, int, str], int] = {}
            for pid, stored, number, proto in (
                self.db.query(models.Port.id, models.Host.ip_address, models.Port.port_number, models.Port.protocol)
                .join(models.Host, models.Host.id == models.Port.host_id)
                .filter(models.Host.project_id == self._project_id, models.Host.ip_address.in_(spellings))
                .all()
            ):
                key = (normalize_ip(stored), number, proto)
                if key not in port_ids or stored == key[0]:
                    port_ids[key] = pid
            wanted = {
                (port_ids[(ip, port, proto)], f"masscan-{kind}"): banner
                for ip in chunk for port, proto, kind, banner in by_ip[ip]
                if (ip, port, proto) in port_ids
            }
            if not final:
                # Its port may still come: kept for the next flush.
                for ip in chunk:
                    for port, proto, kind, banner in by_ip[ip]:
                        if (ip, port, proto) not in port_ids:
                            self._banners[(ip, port, proto, kind)] = banner
            if wanted:
                self._write_banner_scripts(scan_id, wanted)
            beat.tick(len(chunk))

    def _write_banner_scripts(self, scan_id: int, wanted: Dict[Tuple[int, str], str]) -> None:
        """One chunk's banners as ``masscan-<kind>`` scripts on their ports,
        with this scan's sightings."""
        existing = {
            (s.port_id, s.script_id): s
            for s in self.db.query(models.Script).filter(
                models.Script.port_id.in_({pid for pid, _ in wanted}),
                models.Script.script_id.like("masscan-%"),
            )
        }
        reported = []
        for (pid, script_id), banner in wanted.items():
            row = existing.get((pid, script_id))
            if row is not None:
                # scan_id stays the first recorder; this scan's report of
                # the banner is a sighting, below.
                row.output = banner
                row.last_seen = func.now()
            else:
                row = models.Script(port_id=pid, script_id=script_id, output=banner, scan_id=scan_id)
                self.db.add(row)
            reported.append(row)
        self.db.flush()
        scan_sightings.see_many(
            self.db, scan_sightings.SCRIPT, [(row.id, scan_id) for row in reported])

    def _extract_xml_host(self, host_elem: etree._Element) -> Optional[Dict[str, Any]]:
        address_elem = host_elem.find("address")
        if address_elem is None:
            return None
        written = address_elem.get("addr")
        if not written:
            return None
        ip_address = self._address(written)
        if ip_address is None:
            return None

        ports: List[Dict[str, Any]] = []
        ports_elem = host_elem.find("ports")
        if ports_elem is not None:
            for port_elem in ports_elem.findall("port"):
                try:
                    port_number = int(port_elem.get("portid"))
                except (TypeError, ValueError):
                    continue
                protocol = port_elem.get("protocol", "tcp")
                state_elem = port_elem.find("state")
                state = state_elem.get("state") if state_elem is not None else "open"
                if state != "open":
                    continue
                service_elem = port_elem.find("service")
                if service_elem is not None and service_elem.get("banner"):
                    self._note_banner(ip_address, port_number, protocol, service_elem.get("name"), service_elem.get("banner"))
                ports.append({
                    "port_number": port_number,
                    "protocol": protocol,
                    "state": state,
                    "reason": _port_reason(state_elem.get("reason") if state_elem is not None else None),
                })

        if not ports:
            return None

        return {"ip_address": ip_address, "ports": ports}

    # Read fixed-size chunks rather than iterating line-by-line: masscan
    # -oJ frequently emits the whole record array on a single line, and
    # `for line in handle` would pull the entire file into memory at once,
    # defeating the streaming intent.  64 KiB keeps the working buffer
    # bounded regardless of how the file is formatted.
    _JSON_READ_CHUNK = 65536

    # A single masscan record is a small dict; if the buffer grows past this
    # without the decoder yielding one, the content is a giant top-level object
    # or is corrupt past the sample window. Cap it so a bad ``.json`` routed
    # here raises a parse error instead of buffering the whole file (up to
    # MAX_FILE_SIZE) into a Python str and OOM-ing the worker in a retry loop.
    _MAX_JSON_RECORD_BYTES = 16 * 1024 * 1024

    # masscan before 1.0.4 wrote no array: one record per line with a trailing
    # comma, closed by this line — which is not JSON (the key is unquoted).
    _FINISHED_MARKER = re.compile(r'^\{\s*"?finished"?\s*:\s*\d+\s*\}$')

    # What separates two records (and opens the array): skipped in place.
    _JSON_SEPARATORS = re.compile(r"[, \n\r\t\[]*")

    def _iter_json_entries(self, file_path: str) -> Iterable[Dict[str, Any]]:
        """The records of ``masscan -oJ``, decoded one at a time.

        How the file ENDS is read too (owner, 2026-10-11 — a file that was cut
        short used to import as if it were whole, its undecodable tail dropped
        with a log line).  masscan writes ``[``, one record per line, then
        ``]`` when it finishes, so:

        * the text left after the last complete record is the BEGINNING of a
          JSON value (``streaming_json.ends_early``: the reader accepts every
          byte and wants more) — cut short, inside a record;
        * nothing is left, but the file opened an array and never closed it —
          cut short, between records;
        * the text left is the old closing line ``{finished: 1}`` — complete;
        * the text left is anything else — invalid content.  The records
          before it are imported, as they always were; what changed is that
          the import now SAYS so (``_json_unread_after`` → partial, with a
          warning) where it used to drop that text, and every record after
          it, with a log line and look complete.  A record the decoder
          refuses is such text — e.g. one whose banner holds a raw control
          character, which this decoder has always refused.

        A cut is settled in the one place (``cut_short.settle_cut``): it
        raises, or the records end here when the import keeps them.  A file
        with NO opening ``[`` that stops between two records cannot be told
        from a complete one and is read as before."""
        decoder = json.JSONDecoder()
        buffer = ""
        # Where the unread text of ``buffer`` starts.  The records are
        # decoded in place (review 2026-10-22, nitpick): stripping the
        # separators and slicing off each decoded record copied the rest of
        # the buffer twice per record.  The buffer is cut once per read.
        pos = 0
        read = 0
        opened_array: Optional[bool] = None
        closed_array = False
        invalid = False
        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            while not invalid:
                chunk = handle.read(self._JSON_READ_CHUNK)
                if not chunk:
                    break
                # Append raw — do NOT strip(), which would eat significant
                # whitespace inside a string literal split across two reads.
                buffer = buffer[pos:] + chunk
                pos = 0
                if opened_array is None and buffer.strip():
                    opened_array = buffer.lstrip().startswith("[")
                while pos < len(buffer):
                    pos = self._JSON_SEPARATORS.match(buffer, pos).end()
                    if pos >= len(buffer):
                        break
                    if buffer[pos] == "]":
                        closed_array = True
                        pos += 1
                        continue
                    try:
                        entry, index = decoder.raw_decode(buffer, pos)
                    except json.JSONDecodeError:
                        # A whole line (masscan writes a record per line)
                        # that is not the beginning of a JSON value is text
                        # that is not a record: decided NOW.  Read on, the
                        # buffer grew until the cap below and the whole
                        # import failed — whether the records before it were
                        # kept depended on how much followed (review
                        # 2026-10-14, H7).  The end of the file says what it
                        # is (below).
                        if buffer.find("\n", pos) != -1 and not ends_early(buffer[pos:]):
                            invalid = True
                            break
                        # Otherwise "need more data" — break and read the
                        # next chunk. But if the undecodable buffer is already
                        # huge, it's a single oversized record; fail instead
                        # of accumulating the whole file into memory.
                        if len(buffer) - pos > self._MAX_JSON_RECORD_BYTES:
                            raise ValueError(
                                "Masscan JSON record exceeds "
                                f"{self._MAX_JSON_RECORD_BYTES} bytes or is malformed"
                            )
                        break
                    read += 1
                    yield entry
                    pos = index
        left = buffer[pos:].lstrip(", \n\r\t[")
        if left.strip(", \n\r\t[]"):
            if self._FINISHED_MARKER.match(left.strip(", \n\r\t[]")):
                return
            if not ends_early(left):
                logger.warning(
                    "Masscan JSON: text that is not a record after %d records; it and what follows is not read",
                    read,
                )
                self._json_unread_after = read
                return
            settle_cut(read, label="Masscan JSON")
        elif opened_array and not closed_array:
            settle_cut(read, label="Masscan JSON")

    def _parse_timestamp(self, timestamp: Optional[str]) -> Optional[datetime]:
        # v2.333.0 — was datetime.fromtimestamp: the CONTAINER's local time,
        # which only matched nmap's UTC convention while TZ was unset.
        return epoch_to_utc(timestamp)

    # Set per parse in parse_file; None when a collector is driven directly.
    _clock: Optional[ScanClock] = None
    _unread_lines = 0

    def _observe_epoch(self, raw: Any) -> None:
        if self._clock is not None:
            self._clock.observe(epoch_to_utc(raw))
