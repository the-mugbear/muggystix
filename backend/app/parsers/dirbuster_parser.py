"""Parser for directory brute-force / web content discovery tools.

Supports output from:
  - DirBuster (text report)
  - Gobuster  (text / JSON via ``-o``)
  - Feroxbuster (text / JSON via ``--output`` / ``-o``)
  - ffuf (JSON via ``-o`` / ``-of json``, or text/CSV)
  - Dirsearch (text / JSON / CSV)

Each tool has slightly different output formats, but all fundamentally
produce the same data: an HTTP endpoint (URL), a status code, and
optionally the response size.  This parser normalises all formats into
host + port observations enriched with discovered paths stored in
``service_extrainfo``.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.db import models
from app.parsers.streaming_json import iter_json_records
from app.parsers.parser_utils import (
    ProgressBeat,
    beat_while_reading,
    ensure_scan,
    normalize_ip,
    persist_host_observation,
)
from app.services.dns_name_service import (
    InvalidName,
    address_state_for_names,
    get_or_create_name,
    insert_ignore_many,
    normalize_fqdn,
    record_observation,
)
from app.services.host_deduplication_service import HostDeduplicationService

# Paths held before they are written (``_collect``), and rows per INSERT.
_FLUSH_PATHS = 200_000
_INSERT_CHUNK = 5_000

# ---------------------------------------------------------------------------
# Compiled patterns reused across text-format parsers
# ---------------------------------------------------------------------------

# Gobuster / DirBuster: "http://10.0.0.1:8080/admin  (Status: 200) [Size: 1234]"
_GOBUSTER_LINE = re.compile(
    r"(https?://[^\s]+)\s+\(Status:\s*(\d{3})\)(?:\s*\[Size:\s*(\d+)])?",
    re.IGNORECASE,
)

# Feroxbuster: "200  GET  1234l  5678w  91011c  http://10.0.0.1/login"
_FEROX_LINE = re.compile(
    r"^(\d{3})\s+\S+\s+\S+\s+\S+\s+\S+\s+(https?://\S+)",
    re.IGNORECASE,
)

# Dirsearch: "200   512B   http://10.0.0.1:80/admin/"
_DIRSEARCH_LINE = re.compile(
    r"^(\d{3})\s+[\d.]+[KMGBb]*\s+(https?://\S+)",
    re.IGNORECASE,
)

# Generic fallback: bare URL on a line (possibly preceded by a status code)
_GENERIC_URL = re.compile(
    r"(?:(\d{3})\s+)?.*?(https?://[^\s\"',]+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_url(raw_url: str) -> Optional[Tuple[str, int, str, str]]:
    """Return (host, port, protocol_scheme, path) or None.

    v2.416.0 — the path keeps its query string: ``/admin?user=1`` and
    ``/admin?user=2`` are different requests (query fuzzing), and they were
    one row."""
    try:
        parsed = urlparse(raw_url)
        port = parsed.port
    except Exception:
        return None
    host = parsed.hostname
    if not host:
        return None
    scheme = (parsed.scheme or "http").lower()
    port = port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return host, port, scheme, path


def _origin(host: str, port: int, scheme: str) -> str:
    """The URL origin as the tool requested it — the NAME when it asked for a
    name (v2.416.0).  Rebuilt from the bound address, a virtual host's path
    pointed at whatever the IP serves by default."""
    default_port = 443 if scheme == "https" else 80
    shown = f"[{host}]" if ":" in host else host
    return f"{scheme}://{shown}" + ("" if port == default_port else f":{port}")


HostKey = Tuple[str, int, str]  # (ip, port, scheme)


class DirBusterParser:
    """Unified parser for directory brute-force tool output."""

    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name=self._detect_tool(filename),
            scan_type="web_content_discovery",
            project_id=self._project_id,
        )
        # What the readers collect (``_collect``) and what was written of it.
        self._scan = scan
        self._buffered: Dict[HostKey, List[dict]] = {}
        self._buffered_paths = 0
        self._paths_read = 0
        self._services: set = set()             # the web services written, across every part
        self._unresolved: Dict[str, int] = {}   # name → paths not attached

        suffix = Path(filename).suffix.lower()
        if suffix in (".json", ".jsonl"):
            # Accept both extensions — dispatcher routes both here;
            # gating on strict `== ".json"` demoted JSONL to the URL-
            # grep text path which fails closed with "No valid URL
            # entries found in file" (less bad than naabu/amass/smbmap
            # silent zero-success, but still wrong).
            self._parse_json(file_path)
        elif suffix == ".csv":
            self._parse_csv(file_path)
        else:
            self._parse_text(file_path)

        if not self._paths_read:
            raise ValueError("No valid URL entries found in file")

        self._drain()
        hosts, unresolved = self._services, self._unresolved
        if not hosts and not unresolved:
            raise ValueError("No valid URL entries found in file")

        if unresolved:
            paths = sum(unresolved.values())
            sample = ", ".join(sorted(unresolved)[:5])
            self.last_parse_stats = {
                "skipped": paths,
                "warnings": (
                    f"{paths} discovered path(s) on {len(unresolved)} name(s) with no single "
                    f"known address were not attached to a host ({sample}). The names were "
                    f"recorded; import DNS evidence for them (dnsx, DNS CSV) and re-process "
                    f"this file to attach the paths."
                ),
                "summary": f"{len(hosts)} web service{'s' if len(hosts) != 1 else ''}",
                "partial": True,
            }
        return scan

    def _bind_named_targets(
        self, hosts: Dict[HostKey, List[dict]], scan: models.Scan,
    ) -> Tuple[Dict[HostKey, List[dict]], Dict[str, int]]:
        """Re-key every target that is a NAME to the address the inventory
        currently holds for it; return ``(by_address, unresolved)``.

        The URL's hostname used to be passed straight through as
        ``Host.ip_address`` — a host whose "IP" was ``web.example.test``, which
        no subnet can correlate and which becomes a second identity the moment
        the real address is imported.  The server never resolves anything, so
        a name binds ONLY through existing evidence (the one
        ``current_binding_condition`` rule), and only when that evidence names
        a single address: the tool does not say which address it reached, so
        several current addresses is not a binding.  Otherwise the name is
        kept as a DISCOVERED named asset and its paths are reported, not
        attached."""
        bound: Dict[HostKey, List[dict]] = {}
        named: Dict[str, List[HostKey]] = {}
        for key, findings in hosts.items():
            if normalize_ip(key[0]):
                bound.setdefault(key, []).extend(findings)
            else:
                named.setdefault(key[0], []).append(key)

        unresolved: Dict[str, int] = {}
        if not named:
            return bound, unresolved

        name_ids: Dict[str, int] = {}
        if self._project_id is not None:
            for raw in named:
                try:
                    fqdn, kind = normalize_fqdn(raw)
                except InvalidName:
                    continue
                name_ids[raw] = get_or_create_name(self.db, self._project_id, fqdn, kind).id
        states = (
            address_state_for_names(self.db, self._project_id, list(name_ids.values()))
            if name_ids else {}
        )

        for raw, keys in named.items():
            state = states.get(name_ids.get(raw))
            current = list(state.current) if state else []
            if len(current) == 1:
                for _name, port, scheme in keys:
                    bound.setdefault((current[0], port, scheme), []).extend(hosts[(raw, port, scheme)])
                continue
            unresolved[raw] = sum(len(hosts[k]) for k in keys)
            if raw in name_ids:
                record_observation(
                    self.db, project_id=self._project_id, name=raw,
                    record_type=models.DNS_OBS_DISCOVERED, value=scan.tool_name or "dirbuster",
                    scan_id=scan.id,
                )
        return bound, unresolved

    # ------------------------------------------------------------------
    # Tool detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_tool(filename: str) -> str:
        name = filename.lower()
        for tool in ("gobuster", "feroxbuster", "ffuf", "dirsearch", "dirbuster"):
            if tool in name:
                return tool
        return "dirbuster"

    # ------------------------------------------------------------------
    # JSON parsing (ffuf, feroxbuster, dirsearch)
    # ------------------------------------------------------------------

    def _parse_json(self, file_path: str) -> None:
        # ffuf wraps results in {"results": [...]}; feroxbuster and
        # dirsearch emit top-level arrays.  The streaming helper picks
        # the right shape and avoids loading huge directory-busting
        # exports (millions of URLs is realistic) into memory.
        # Iterated lazily — ``list(...)`` materialised the whole export,
        # defeating the streaming helper — and each record's own field names
        # are read.  Probing records[0] picked the columns for the whole file,
        # which is wrong for feroxbuster (a configuration record comes first,
        # then ``status`` / ``content_length``) and dirsearch
        # (``contentLength``), so every size came out None (review 2026-09-23
        # R5; R16 of 09-21).
        records = iter_json_records(
            file_path,
            array_keys=("results",),
            tool_label="Directory-buster JSON",
        )
        self._parse_entries(records)

    # ------------------------------------------------------------------
    # CSV parsing (dirsearch CSV, ffuf CSV)
    # ------------------------------------------------------------------

    def _parse_csv(self, file_path: str) -> None:
        with open(file_path, "r", encoding="utf-8", errors="ignore", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in beat_while_reading(reader, "rows read"):
                raw_url = row.get("url") or row.get("URL") or row.get("FUZZ") or ""
                parsed = _parse_url(raw_url)
                if not parsed:
                    continue
                host, port, scheme, path = parsed
                ip = normalize_ip(host) or host
                status = self._coerce_int(row.get("status") or row.get("Status") or row.get("status_code"))
                # ffuf's CSV names the size `content_length` (v2.416.0).
                size = self._coerce_int(
                    row.get("content_length") or row.get("content-length")
                    or row.get("length") or row.get("size")
                )
                self._collect((ip, port, scheme), {
                    "path": path,
                    "status_code": status,
                    "size": size,
                    "origin": _origin(host, port, scheme),
                })

    # ------------------------------------------------------------------
    # Text parsing (gobuster, feroxbuster, dirsearch, dirbuster)
    # ------------------------------------------------------------------

    def _parse_text(self, file_path: str) -> None:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in beat_while_reading(fh, "lines read"):
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("="):
                    continue

                url: Optional[str] = None
                status: Optional[int] = None
                size: Optional[int] = None

                # Try specific patterns first
                m = _GOBUSTER_LINE.search(line)
                if m:
                    url, status, size = m.group(1), self._coerce_int(m.group(2)), self._coerce_int(m.group(3))
                else:
                    m = _FEROX_LINE.match(line)
                    if m:
                        status, url = self._coerce_int(m.group(1)), m.group(2)
                    else:
                        m = _DIRSEARCH_LINE.match(line)
                        if m:
                            status, url = self._coerce_int(m.group(1)), m.group(2)
                        else:
                            m = _GENERIC_URL.search(line)
                            if m:
                                status, url = self._coerce_int(m.group(1)), m.group(2)

                if not url:
                    continue
                parsed = _parse_url(url)
                if not parsed:
                    continue
                host, port, scheme, path = parsed
                ip = normalize_ip(host) or host
                self._collect((ip, port, scheme), {
                    "path": path,
                    "status_code": status,
                    "size": size,
                    "origin": _origin(host, port, scheme),
                })

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    # The same fact under each tool's name: feroxbuster, ffuf, dirsearch.
    _STATUS_KEYS = ("status_code", "status")
    _SIZE_KEYS = ("content_length", "length", "content-length", "contentLength", "size")

    @staticmethod
    def _first(entry: dict, keys) -> object:
        for k in keys:
            if entry.get(k) is not None:
                return entry[k]
        return None

    def _parse_entries(self, entries) -> None:
        # R6 — the read phase heartbeats (only the committed scan row exists).
        for entry in beat_while_reading(entries, "results read"):
            if not isinstance(entry, dict):
                continue
            # feroxbuster's first line describes the run, not a path.
            if entry.get("type") not in (None, "response"):
                continue
            raw_url = str(entry.get("url") or "")
            parsed = _parse_url(raw_url)
            if not parsed:
                continue
            host, port, scheme, path = parsed
            ip = normalize_ip(host) or host
            status = self._coerce_int(self._first(entry, self._STATUS_KEYS))
            size = self._coerce_int(self._first(entry, self._SIZE_KEYS))
            self._collect((ip, port, scheme), {
                "path": path,
                "status_code": status,
                "size": size,
                "origin": _origin(host, port, scheme),
            })

    # ------------------------------------------------------------------
    # Collected in parts, written at a bound
    # ------------------------------------------------------------------

    def _collect(self, key: HostKey, record: dict) -> None:
        """One path the file states, kept until ``_FLUSH_PATHS`` are held and
        then written (``_drain``).  The whole export used to be held — a dict
        per path, "millions of URLs is realistic" — before the first write, so
        a large one ended the worker for memory on every retry (review
        2026-11-20, C11).  An ordinary file never reaches the bound and is
        written exactly as before, in one part."""
        self._buffered.setdefault(key, []).append(record)
        self._buffered_paths += 1
        self._paths_read += 1
        if self._buffered_paths >= _FLUSH_PATHS:
            self._drain()

    def _drain(self) -> None:
        hosts, self._buffered, self._buffered_paths = self._buffered, {}, 0
        if not hosts:
            return
        bound, unresolved = self._bind_named_targets(hosts, self._scan)
        for name, paths in unresolved.items():
            self._unresolved[name] = self._unresolved.get(name, 0) + paths
        self._services.update(bound)
        self._persist(bound, self._scan)

    def _persist(
        self,
        hosts: Dict[HostKey, List[dict]],
        scan: models.Scan,
    ) -> None:
        # R6 — heartbeat between web services, counted in paths (one service can
        # carry a million of them); outside the host's savepoint.
        beat = ProgressBeat("paths", every=5000)
        for (ip, port, scheme), findings in hosts.items():
            beat.tick()
            # The name carries no confidence, so should_replace_service lets it
            # fill a blank but never replace an nmap identification (v2.390.3;
            # before that this parser queried "is the port already named?" per
            # port to dodge the longer-name rule).  The paths are rows
            # (web_paths), not a capped string in service_extrainfo.
            port_entry = {
                "port_number": port, "protocol": "tcp", "state": "open",
                "service_name": "https" if scheme == "https" else "http",
            }

            persisted = persist_host_observation(
                dedup_service=self.dedup_service,
                scan_id=scan.id,
                ip_address=ip,
                project_id=self._project_id,
                ports=[port_entry],
                isolate=True,
            )
            if not persisted:
                continue
            host, port_map = persisted
            port_row = port_map.get((port, "tcp"))
            host_id, port_id = host.id, port_row.id if port_row else None
            source = scan.tool_name or "dirbuster"
            base = _origin(ip, port, scheme)
            seen: set = set()
            rows: List[dict] = []
            for f in findings:
                path = f.get("path") or "/"
                # The URL the tool requested (its name, when it asked for
                # one); the row is still attached to the bound address.
                url = (f.get("origin") or base) + (path if path.startswith("/") else f"/{path}")
                if url in seen:
                    continue
                seen.add(url)
                rows.append({
                    "project_id": self._project_id, "host_id": host_id, "port_id": port_id,
                    "scan_id": scan.id, "source": source, "url": url, "path": path,
                    "status_code": f.get("status_code"), "size": f.get("size"),
                })
            # In statements of ``_INSERT_CHUNK`` rows, a heartbeat between
            # them: one ORM object per path and ONE flush for the service was
            # a single long step for "a million of them".  A URL this scan
            # already holds (the file states it twice, in two parts) is left
            # as it is (``uq_web_paths_scan_url``).
            for start in range(0, len(rows), _INSERT_CHUNK):
                chunk = rows[start:start + _INSERT_CHUNK]
                insert_ignore_many(self.db, models.WebPath.__table__, chunk)
                beat.tick(len(chunk))

    @staticmethod
    def _coerce_int(value: object) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
