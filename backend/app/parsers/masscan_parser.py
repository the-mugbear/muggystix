"""Masscan parser with batch ingestion and deduplicated persistence."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from lxml import etree

from app.parsers.xml_stream_helpers import clear_element, iterparse_safe, strip_namespace
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import correlate_scan

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


class MasscanParser:
    """Parse Masscan output formats with batch DB operations."""

    XML_SCHEMA_REFERENCE = "https://nmap.org/book/nmap-dtd.html"

    def __init__(self, db: Session):
        self.db = db

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        """Dispatch to format-specific parsers based on file extension."""
        self._project_id = kwargs.get("project_id")
        start = time.time()
        scan = self._create_scan_record(filename)

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

            if suffix == ".xml":
                residual = self._collect_xml(file_path, scan, _flush)
            elif suffix == ".json":
                residual = self._collect_json(file_path, _flush)
            else:
                residual = self._collect_list(file_path, _flush)

            _flush(residual)

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

        try:
            # See xml_stream_helpers.iterparse_safe for the hardening
            # flag rationale (XXE / billion-laughs / huge-tree defense).
            context = iterparse_safe(file_path)
        except etree.XMLSyntaxError as exc:
            line = getattr(exc, "lineno", "?")
            position = getattr(exc, "position", None)
            column = position[1] if isinstance(position, tuple) and len(position) > 1 else "?"
            logger.error(
                "Masscan XML failed to initialise iterparse for %s (line=%s column=%s): %s",
                file_path, line, column, exc,
            )
            raise

        try:
            for event, elem in context:
                tag = strip_namespace(elem.tag)

                if event == "start" and tag in {"nmaprun", "masscan"}:
                    scan.version = elem.get("version")
                    scan.command_line = elem.get("args")
                    scan.tool_name = elem.get("scanner", "masscan")
                    scan.start_time = self._parse_timestamp(elem.get("start"))

                if event == "end" and tag == "host":
                    host_info = self._extract_xml_host(elem)
                    if host_info:
                        host_ports[host_info["ip_address"]].extend(host_info["ports"])
                    clear_element(elem)
                    self._maybe_flush(host_ports, flush)
                elif event == "end" and tag == "finished":
                    end_time = self._parse_timestamp(elem.get("time"))
                    if end_time is None:
                        end_time = self._parse_timestr(elem.get("timestr"))
                    scan.end_time = end_time or scan.end_time
        except etree.XMLSyntaxError as exc:
            line = getattr(exc, "lineno", "?")
            position = getattr(exc, "position", None)
            column = position[1] if isinstance(position, tuple) and len(position) > 1 else "?"
            logger.warning(
                "Masscan XML parsing halted for %s near line=%s column=%s "
                "(likely truncated/incomplete scan). Recovered %s hosts before error: %s",
                file_path, line, column, len(host_ports), exc,
            )

        if scan.end_time is None:
            scan.end_time = datetime.utcnow()

        return dict(host_ports)

    def _collect_json(
        self,
        file_path: str,
        flush: Optional[Callable[[Dict[str, List[Dict[str, Any]]]], None]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        host_ports: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        for entry in self._iter_json_entries(file_path):
            ip_address = entry.get("ip") or entry.get("addr")
            if not ip_address:
                continue
            for port_info in entry.get("ports", []):
                try:
                    port_number = int(port_info.get("port"))
                except (TypeError, ValueError):
                    continue
                protocol = port_info.get("proto", "tcp")
                state = port_info.get("status", "open")
                if state != "open":
                    continue
                host_ports[ip_address].append({
                    "port_number": port_number,
                    "protocol": protocol,
                    "state": state,
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
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Masscan verbose/greppable-style lines:
                #   Timestamp: 1234567890\tHost: 10.0.0.1 ()\tPorts: 80/open/tcp//http//
                if line.startswith("Timestamp:") and "Host:" in line and "Ports:" in line:
                    parsed = self._parse_timestamp_line(line)
                    if parsed:
                        ip_address, ports = parsed
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
                    continue
                state, protocol, port_str, ip_address = parts[:4]
                if state != "open":
                    continue
                try:
                    port_number = int(port_str)
                except ValueError:
                    continue
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
    # Phase 2 — Batch persist using bulk SQL operations
    #
    # NOTE: This intentionally uses raw SQL against hosts_v2/ports_v2 rather
    # than HostDeduplicationService for throughput on large masscan scans
    # (tens of thousands of hosts).  Trade-offs vs the ORM dedup service:
    #   - No savepoint-based retry on concurrent inserts (acceptable: scans
    #     are processed serially by the ingestion queue).
    #   - first_seen relies on the column's server_default (consistent with
    #     the dedup service).
    #   - If the v2 schema changes, this SQL must be updated in lockstep.
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
            ip_to_host_id = self._upsert_hosts_batch(scan_id, batch_ips)
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
                self._upsert_ports_batch(scan_id, batch_port_rows)
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

            seen: Set[Tuple[int, str]] = set()
            for port_data in host_ports.get(ip, []):
                key = (port_data["port_number"], str(port_data.get("protocol", "tcp")).lower())
                if key in seen:
                    continue
                seen.add(key)
                rows.append((host_id, port_data))
        return rows

    def _upsert_hosts_batch(
        self, scan_id: int, ips: List[str]
    ) -> Dict[str, int]:
        """Upsert a batch of hosts and return {ip: host_id} mapping."""
        if not ips:
            return {}

        project_id = self._project_id

        # Find existing hosts (scoped to project)
        host_query = (
            self.db.query(models.Host.id, models.Host.ip_address)
            .filter(models.Host.ip_address.in_(ips))
        )
        if project_id is not None:
            host_query = host_query.filter(models.Host.project_id == project_id)
        existing = host_query.all()
        existing_map = {row.ip_address: row.id for row in existing}

        # Update existing hosts: bump last_seen and scan_id
        if existing_map:
            update_sql = (
                "UPDATE hosts_v2 SET last_seen = NOW(), "
                "last_updated_scan_id = :scan_id, state = 'up' "
                "WHERE ip_address = ANY(:ips)"
            )
            update_params: Dict[str, Any] = {"scan_id": scan_id, "ips": list(existing_map.keys())}
            if project_id is not None:
                update_sql += " AND project_id = :project_id"
                update_params["project_id"] = project_id
            self.db.execute(text(update_sql), update_params)

        # Insert new hosts
        new_ips = [ip for ip in ips if ip not in existing_map]
        if new_ips:
            values_clauses = []
            params: Dict[str, Any] = {"scan_id": scan_id}
            if project_id is not None:
                params["project_id"] = project_id
            for idx, ip in enumerate(new_ips):
                params[f"ip_{idx}"] = ip
                if project_id is not None:
                    values_clauses.append(
                        f"(:ip_{idx}, 'up', :scan_id, :project_id)"
                    )
                else:
                    values_clauses.append(
                        f"(:ip_{idx}, 'up', :scan_id, NULL)"
                    )
            if project_id is not None:
                sql = (
                    "INSERT INTO hosts_v2 (ip_address, state, last_updated_scan_id, project_id) "
                    "VALUES " + ", ".join(values_clauses) + " "
                    "ON CONFLICT (project_id, ip_address) DO UPDATE SET "
                    "last_seen = NOW(), last_updated_scan_id = :scan_id, state = 'up' "
                    "RETURNING id, ip_address"
                )
            else:
                sql = (
                    "INSERT INTO hosts_v2 (ip_address, state, last_updated_scan_id, project_id) "
                    "VALUES " + ", ".join(values_clauses) + " "
                    "ON CONFLICT DO NOTHING "
                    "RETURNING id, ip_address"
                )
            result = self.db.execute(text(sql), params)
            for row in result:
                existing_map[row.ip_address] = row.id

            # Backfill any IPs the RETURNING clause didn't yield.  The
            # no-project branch uses ON CONFLICT DO NOTHING, which returns
            # nothing for a row a concurrent writer inserted between our
            # SELECT and this INSERT — without this re-select those IPs
            # would be absent from existing_map and their ports silently
            # dropped in _build_port_rows_for_hosts.
            missing = [ip for ip in new_ips if ip not in existing_map]
            if missing:
                backfill_q = (
                    self.db.query(models.Host.id, models.Host.ip_address)
                    .filter(models.Host.ip_address.in_(missing))
                )
                if project_id is not None:
                    backfill_q = backfill_q.filter(models.Host.project_id == project_id)
                for row in backfill_q.all():
                    existing_map[row.ip_address] = row.id

        # Insert host_scan_history for all hosts in this batch
        history_values = []
        h_params: Dict[str, Any] = {"scan_id": scan_id}
        for idx, (ip, host_id) in enumerate(existing_map.items()):
            if ip in ips:  # only for this batch
                h_params[f"hid_{idx}"] = host_id
                history_values.append(f"(:hid_{idx}, :scan_id, 'up')")
        if history_values:
            sql = (
                "INSERT INTO host_scan_history (host_id, scan_id, state_at_scan) "
                "VALUES " + ", ".join(history_values) + " "
                "ON CONFLICT (host_id, scan_id) DO NOTHING"
            )
            self.db.execute(text(sql), h_params)

        self.db.flush()
        return {ip: existing_map[ip] for ip in ips if ip in existing_map}

    # Each port row binds 5 params (hid/pn/proto/st/svc); the history
    # insert binds 1 (pid).  PostgreSQL caps a statement at 65 535 bind
    # params, so a 500-host batch of a wide-port masscan scan
    # (e.g. -p0-65535) easily overflows one INSERT.  Sub-batch the rows
    # so each statement stays well under the ceiling.  5 000 rows ×
    # 5 params = 25 000, comfortably bounded.
    _PORT_UPSERT_CHUNK = 5000

    def _upsert_ports_batch(
        self,
        scan_id: int,
        rows: List[Tuple[int, Dict[str, Any]]],
    ) -> None:
        """Upsert a batch of ports, sub-chunked under the bind-param limit."""
        if not rows:
            return
        for start in range(0, len(rows), self._PORT_UPSERT_CHUNK):
            self._upsert_ports_chunk(scan_id, rows[start:start + self._PORT_UPSERT_CHUNK])

    def _upsert_ports_chunk(
        self,
        scan_id: int,
        rows: List[Tuple[int, Dict[str, Any]]],
    ) -> None:
        """Upsert a single sub-batch of ports (caller bounds the size)."""
        if not rows:
            return

        values_clauses = []
        params: Dict[str, Any] = {"scan_id": scan_id}
        for idx, (host_id, port_data) in enumerate(rows):
            params[f"hid_{idx}"] = host_id
            params[f"pn_{idx}"] = port_data["port_number"]
            params[f"proto_{idx}"] = port_data.get("protocol", "tcp")
            params[f"st_{idx}"] = port_data.get("state", "open")
            params[f"svc_{idx}"] = port_data.get("service_name")
            values_clauses.append(
                f"(:hid_{idx}, :pn_{idx}, :proto_{idx}, :st_{idx}, "
                f":svc_{idx}, :scan_id, TRUE)"
            )

        # service_name merge MIRRORS the canonical rule in
        # host_deduplication_service.should_replace_service.  Masscan's bulk path
        # carries only a name (no confidence), so the rule reduces to
        # empty-or-longer-wins: take the new name only when we have nothing yet,
        # or it's non-empty and more specific (longer).  The old
        # COALESCE(NULLIF(...)) was last-non-empty-wins, which let a masscan
        # re-scan clobber a longer/better nmap service name. Keep in lockstep.
        sql = (
            "INSERT INTO ports_v2 "
            "(host_id, port_number, protocol, state, service_name, "
            "last_updated_scan_id, is_active) "
            "VALUES " + ", ".join(values_clauses) + " "
            "ON CONFLICT (host_id, port_number, protocol) DO UPDATE SET "
            "state = EXCLUDED.state, last_seen = NOW(), "
            "last_updated_scan_id = EXCLUDED.last_updated_scan_id, "
            "is_active = TRUE, "
            "service_name = CASE "
            "WHEN COALESCE(ports_v2.service_name, '') = '' THEN EXCLUDED.service_name "
            "WHEN NULLIF(EXCLUDED.service_name, '') IS NOT NULL "
            "AND length(EXCLUDED.service_name) > length(ports_v2.service_name) "
            "THEN EXCLUDED.service_name "
            "ELSE ports_v2.service_name END "
            "RETURNING id, host_id, port_number, protocol"
        )
        result = self.db.execute(text(sql), params)
        port_rows = list(result)

        # Insert port_scan_history
        if port_rows:
            ph_values = []
            ph_params: Dict[str, Any] = {"scan_id": scan_id}
            for idx, row in enumerate(port_rows):
                ph_params[f"pid_{idx}"] = row.id
                ph_values.append(f"(:pid_{idx}, :scan_id, 'open')")
            sql = (
                "INSERT INTO port_scan_history (port_id, scan_id, state_at_scan) "
                "VALUES " + ", ".join(ph_values) + " "
                "ON CONFLICT (port_id, scan_id) DO NOTHING"
            )
            self.db.execute(text(sql), ph_params)

        self.db.flush()

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
            created_at=datetime.utcnow(),
            project_id=self._project_id,
        )
        self.db.add(scan)
        self.db.flush()
        return scan

    def _extract_xml_host(self, host_elem: etree._Element) -> Optional[Dict[str, Any]]:
        address_elem = host_elem.find("address")
        if address_elem is None:
            return None
        ip_address = address_elem.get("addr")
        if not ip_address:
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
                ports.append({
                    "port_number": port_number,
                    "protocol": protocol,
                    "state": state,
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

    def _iter_json_entries(self, file_path: str) -> Iterable[Dict[str, Any]]:
        decoder = json.JSONDecoder()
        buffer = ""
        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            while True:
                chunk = handle.read(self._JSON_READ_CHUNK)
                if not chunk:
                    break
                # Append raw — do NOT strip(), which would eat significant
                # whitespace inside a string literal split across two reads.
                buffer += chunk
                while buffer:
                    buffer = buffer.lstrip(", \n\r\t[")
                    if not buffer:
                        break
                    if buffer.startswith("]"):
                        buffer = buffer[1:]
                        continue
                    try:
                        entry, index = decoder.raw_decode(buffer)
                    except json.JSONDecodeError:
                        break
                    yield entry
                    buffer = buffer[index:]
        buffer = buffer.strip(", \n\r\t[]")
        if buffer:
            try:
                entry, _ = decoder.raw_decode(buffer)
                yield entry
            except json.JSONDecodeError:
                logger.warning("Trailing JSON buffer ignored while parsing Masscan output")

    def _parse_timestamp(self, timestamp: Optional[str]) -> Optional[datetime]:
        if not timestamp:
            return None
        try:
            return datetime.fromtimestamp(int(timestamp))
        except (ValueError, TypeError):
            return None

    def _parse_timestr(self, timestr: Optional[str]) -> Optional[datetime]:
        if not timestr:
            return None
        for fmt in ('%a %b %d %H:%M:%S %Y', '%Y-%m-%d %H:%M:%S'):
            try:
                return datetime.strptime(timestr, fmt)
            except ValueError:
                continue
        logger.warning("Masscan parser unable to parse finished timestamp string '%s'", timestr)
        return None
