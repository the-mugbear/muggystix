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
    """Return (host, port, protocol_scheme, path) or None."""
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return None
    host = parsed.hostname
    if not host:
        return None
    scheme = (parsed.scheme or "http").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    return host, port, scheme, path


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

        self._persist(hosts, scan)
        correlate_scan(self.db, scan.id)
        return scan

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
        records = list(
            iter_json_records(
                file_path,
                array_keys=("results",),
                tool_label="Directory-buster JSON",
            )
        )
        if not records:
            return {}

        # feroxbuster's per-entry shape uses ``status_code``; dirsearch
        # uses ``status``; ffuf (already unwrapped from ``{"results":…}``)
        # uses ``status``.  Probe the first record to pick the column
        # set, then apply uniformly to the whole batch.
        if "status_code" in records[0]:
            return self._parse_entries(
                records, url_key="url", status_key="status_code", size_key="content_length",
            )
        # ffuf JSON nests size under ``length``; dirsearch under
        # ``content-length``.  Try ffuf's name first since the helper
        # already collapsed ``{"results": …}`` for us.
        size_key = "length" if "length" in records[0] else "content-length"
        return self._parse_entries(
            records, url_key="url", status_key="status", size_key=size_key,
        )

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
                size = self._coerce_int(row.get("content-length") or row.get("length") or row.get("size"))
                key: HostKey = (ip, port, scheme)
                hosts.setdefault(key, []).append({
                    "path": path,
                    "status_code": status,
                    "size": size,
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
                })

        return hosts

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _parse_entries(
        self,
        entries: list,
        url_key: str,
        status_key: str,
        size_key: str,
    ) -> Dict[HostKey, List[dict]]:
        hosts: Dict[HostKey, List[dict]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            raw_url = str(entry.get(url_key) or "")
            parsed = _parse_url(raw_url)
            if not parsed:
                continue
            host, port, scheme, path = parsed
            ip = normalize_ip(host) or host
            status = self._coerce_int(entry.get(status_key))
            size = self._coerce_int(entry.get(size_key))
            key: HostKey = (ip, port, scheme)
            hosts.setdefault(key, []).append({
                "path": path,
                "status_code": status,
                "size": size,
            })
        return hosts

    def _persist(
        self,
        hosts: Dict[HostKey, List[dict]],
        scan: models.Scan,
    ) -> None:
        for (ip, port, scheme), findings in hosts.items():
            service = "https" if scheme == "https" else "http"
            # Build a summary of discovered paths for service_extrainfo
            path_lines = []
            for f in findings:
                code = f.get("status_code") or "???"
                path = f.get("path") or "/"
                size = f.get("size")
                entry = f"[{code}] {path}"
                if size is not None:
                    entry += f" ({size}B)"
                path_lines.append(entry)
            extrainfo = "; ".join(path_lines[:50])  # cap to avoid huge strings
            if len(findings) > 50:
                extrainfo += f"; ... and {len(findings) - 50} more"

            persist_host_observation(
                dedup_service=self.dedup_service,
                scan_id=scan.id,
                ip_address=ip,
                project_id=self._project_id,
                ports=[{
                    "port_number": port,
                    "protocol": "tcp",
                    "state": "open",
                    "service_name": service,
                    "service_extrainfo": extrainfo,
                }],
            )

    @staticmethod
    def _coerce_int(value: object) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
