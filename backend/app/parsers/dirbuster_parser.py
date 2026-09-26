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
    correlate_scan,
    ensure_scan,
    normalize_ip,
    persist_host_observation,
)
from app.services.dns_name_service import (
    InvalidName,
    address_state_for_names,
    get_or_create_name,
    normalize_fqdn,
    record_observation,
)
from app.services.host_deduplication_service import HostDeduplicationService

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

        suffix = Path(filename).suffix.lower()
        if suffix in (".json", ".jsonl"):
            # Accept both extensions — dispatcher routes both here;
            # gating on strict `== ".json"` demoted JSONL to the URL-
            # grep text path which fails closed with "No valid URL
            # entries found in file" (less bad than naabu/amass/smbmap
            # silent zero-success, but still wrong).
            hosts = self._parse_json(file_path)
        elif suffix == ".csv":
            hosts = self._parse_csv(file_path)
        else:
            hosts = self._parse_text(file_path)

        if not hosts:
            raise ValueError("No valid URL entries found in file")

        hosts, unresolved = self._bind_named_targets(hosts, scan)
        if not hosts and not unresolved:
            raise ValueError("No valid URL entries found in file")

        self._persist(hosts, scan)
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
        correlate_scan(self.db, scan.id)
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

    def _parse_json(self, file_path: str) -> Dict[HostKey, List[dict]]:
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
        return self._parse_entries(records)

    # ------------------------------------------------------------------
    # CSV parsing (dirsearch CSV, ffuf CSV)
    # ------------------------------------------------------------------

    def _parse_csv(self, file_path: str) -> Dict[HostKey, List[dict]]:
        hosts: Dict[HostKey, List[dict]] = {}
        with open(file_path, "r", encoding="utf-8", errors="ignore", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
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
                key: HostKey = (ip, port, scheme)
                hosts.setdefault(key, []).append({
                    "path": path,
                    "status_code": status,
                    "size": size,
                    "origin": _origin(host, port, scheme),
                })
        return hosts

    # ------------------------------------------------------------------
    # Text parsing (gobuster, feroxbuster, dirsearch, dirbuster)
    # ------------------------------------------------------------------

    def _parse_text(self, file_path: str) -> Dict[HostKey, List[dict]]:
        hosts: Dict[HostKey, List[dict]] = {}
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
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
                key: HostKey = (ip, port, scheme)
                hosts.setdefault(key, []).append({
                    "path": path,
                    "status_code": status,
                    "size": size,
                    "origin": _origin(host, port, scheme),
                })

        return hosts

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

    def _parse_entries(self, entries) -> Dict[HostKey, List[dict]]:
        hosts: Dict[HostKey, List[dict]] = {}
        for entry in entries:
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
            key: HostKey = (ip, port, scheme)
            hosts.setdefault(key, []).append({
                "path": path,
                "status_code": status,
                "size": size,
                "origin": _origin(host, port, scheme),
            })
        return hosts

    def _persist(
        self,
        hosts: Dict[HostKey, List[dict]],
        scan: models.Scan,
    ) -> None:
        for (ip, port, scheme), findings in hosts.items():
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
            base = _origin(ip, port, scheme)
            seen: set = set()
            for f in findings:
                path = f.get("path") or "/"
                # The URL the tool requested (its name, when it asked for
                # one); the row is still attached to the bound address.
                url = (f.get("origin") or base) + (path if path.startswith("/") else f"/{path}")
                if url in seen:
                    continue
                seen.add(url)
                self.db.add(models.WebPath(
                    project_id=self._project_id, host_id=host.id,
                    port_id=port_row.id if port_row else None, scan_id=scan.id,
                    source=scan.tool_name or "dirbuster", url=url, path=path,
                    status_code=f.get("status_code"), size=f.get("size"),
                ))
            self.db.flush()

    @staticmethod
    def _coerce_int(value: object) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
