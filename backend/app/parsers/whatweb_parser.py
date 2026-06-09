"""
whatweb JSON parser — v2.140.0.

whatweb is a Ruby web tech-fingerprinter that ships in the Debian / Kali
apt repos, so it's reliably available where ProjectDiscovery's httpx
(a Go binary / Python-CLI-collision minefield) is not.  This parser
promotes it from "acceptable fallback when httpx is blocked" to a
first-class web tool the recon agent can run and upload.

Operator invocation (matches the recon tool-catalog entry)::

    whatweb -a 3 --input-file=targets.txt --log-json=whatweb.json --no-errors

whatweb's ``--log-json`` writes one JSON object per target.  Depending
on the version that's a top-level array, a stream of objects, or JSONL
— ``iter_json_records`` handles all three uniformly.  Each record looks
like::

    {
      "target": "http://192.168.7.200:80/",
      "http_status": 200,
      "plugins": {
        "Apache":       {"version": ["2.4.41"], "string": ["..."]},
        "HTTPServer":   {"string": ["Apache/2.4.41 (Ubuntu)"]},
        "IP":           {"string": ["192.168.7.200"]},
        "PHP":          {"version": ["7.4.3"]},
        "Title":        {"string": ["Index of /"]},
        "Country":      {"string": ["RESERVED"], "module": ["ZZ"]}
      }
    }

This parser mirrors the httpx parser exactly — same ``WebInterface``
table, same host/port resolution, same (scan_id, url, source) upsert —
only the field extraction differs (whatweb nests everything under
``plugins`` instead of flat fields).  Rows are written with
``source="whatweb"`` so the UI can tell which tool produced them.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import correlate_scan, record_hosts_in_scan
from app.parsers.streaming_json import iter_json_records

logger = logging.getLogger(__name__)


# whatweb plugin names that are HTTP/metadata signals, not detected
# technologies.  ``Title`` / ``HTTPServer`` / ``IP`` are extracted into
# dedicated columns; the rest are header noise that would clutter the
# tech-chip list.  Compared case-insensitively.  Anything NOT in this set
# is treated as a fingerprint and rendered as a tech chip — whatweb's
# plugin names ARE its fingerprints, so the default-include is correct.
_NON_TECH_PLUGINS = {
    "ip", "country", "title", "httpserver", "uncommonheaders",
    "redirectlocation", "cookies", "httponly", "x-frame-options",
    "strict-transport-security", "x-xss-protection", "content-security-policy",
    "x-content-type-options", "access-control-allow-origin", "via-proxy",
    "allow", "email", "meta-author", "meta-refresh", "script", "frame",
    "html5", "passwordfield", "x-powered-by",
}


def looks_like_whatweb(sample: bytes, filename: str) -> bool:
    """Content-based detection for whatweb ``--log-json`` output.

    whatweb records carry a ``target`` URL plus a ``plugins`` dict (its
    fingerprint map); ``http_status`` is usually present too.  That
    combination distinguishes whatweb from httpx (``url`` + ``tech``),
    dnsx (``host`` + record-type arrays), and amass (``name`` +
    ``addresses``), so it rarely false-positives.  A filename match wins
    outright for operators who name the file ``whatweb-*.json``.
    """
    if "whatweb" in filename.lower():
        return True
    from app.parsers.content_detection import _peek_json_shape
    _, rec = _peek_json_shape(sample)
    if not isinstance(rec, dict):
        return False
    return "target" in rec and isinstance(rec.get("plugins"), dict)


class WhatwebParser:
    """Parser for whatweb JSON / JSONL output."""

    def __init__(self, db: Session):
        self.db = db
        self._project_id: Optional[int] = None

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        start = time.time()
        logger.info("Starting whatweb parse of %s", filename)

        records = self._load_records(file_path)
        if not records:
            raise ValueError("whatweb file contained no parseable records")

        scan = models.Scan(
            filename=filename,
            scan_type="web_fingerprint",
            tool_name="whatweb",
            created_at=datetime.utcnow(),
            project_id=self._project_id,
        )
        self.db.add(scan)
        self.db.flush()

        written = 0
        skipped = 0
        host_ids_seen: set = set()
        for record in records:
            try:
                host_id = self._upsert_record(record, scan)
                if host_id:
                    written += 1
                    host_ids_seen.add(host_id)
                else:
                    skipped += 1
            except Exception as exc:  # noqa: BLE001 — one bad record mustn't sink the upload
                logger.warning("whatweb: skipping record due to %s", exc)
                skipped += 1

        # Write HostScanHistory rows so /agent/recon/summary's per-host
        # breakdown + hosts_discovered count see this scan (the summary
        # query joins through host_scan_history, not web_interfaces).
        record_hosts_in_scan(self.db, scan.id, host_ids_seen)

        self.db.commit()

        try:
            correlate_scan(self.db, scan.id)
        except Exception as exc:  # pragma: no cover — best effort
            logger.warning("whatweb scan %s correlation failed: %s", scan.id, exc)

        elapsed = time.time() - start
        logger.info(
            "whatweb %s: %d web_interfaces written, %d skipped in %.2fs",
            filename, written, skipped, elapsed,
        )
        if written == 0:
            raise ValueError(
                f"whatweb parser found 0 usable records in {filename}; every "
                f"record was missing a target URL or a resolvable IP, or the "
                f"file isn't whatweb --log-json output."
            )
        self.last_parse_stats = {
            "skipped": skipped,
            "warnings": (
                f"{skipped} whatweb record(s) malformed or missing required fields"
                if skipped > 0 else None
            ),
        }
        return scan

    # ------------------------------------------------------------------

    def _load_records(self, file_path: str) -> Iterable[Dict[str, Any]]:
        """Stream whatweb output in JSON (array / single object) or JSONL
        form — the version + post-processing (jq, etc.) determines which,
        and the streaming helper handles all three uniformly."""
        return iter_json_records(file_path, tool_label="whatweb JSON")

    def _upsert_record(self, record: Dict[str, Any], scan: models.Scan) -> Optional[int]:
        """Write or update a single web_interfaces row from a whatweb
        record.  Returns the resolved ``host_id`` on success, or ``None``
        if the record was skipped (no target URL, or no resolvable IP)."""
        url = record.get("target") or record.get("url")
        if not url or not isinstance(url, str):
            return None

        plugins = record.get("plugins")
        if not isinstance(plugins, dict):
            plugins = {}

        ip, hostname = self._resolve_ip_and_hostname(record, plugins, url)
        if ip is None:
            logger.info(
                "whatweb: skipping record with no resolvable IP (target=%r, IP plugin=%r)",
                url, self._plugin_first_string(plugins, "IP"),
            )
            return None

        port = self._port_from_url(url)
        protocol = self._scheme_from_url(url)

        # Resolve host_id by IP within the project; create on demand
        # (whatweb hit it, so it's real) — mirrors the httpx parser.
        host_row = (
            self.db.query(models.Host)
            .filter(
                models.Host.ip_address == ip,
                models.Host.project_id == self._project_id,
            )
            .first()
        )
        if host_row is None:
            host_row = models.Host(
                ip_address=ip,
                hostname=hostname,
                state="up",
                project_id=self._project_id,
            )
            self.db.add(host_row)
            self.db.flush()
        elif hostname and not host_row.hostname:
            host_row.hostname = hostname

        port_row = None
        if host_row and port:
            port_row = (
                self.db.query(models.Port)
                .filter(
                    models.Port.host_id == host_row.id,
                    models.Port.port_number == port,
                    models.Port.protocol == "tcp",
                )
                .first()
            )

        technologies = self._extract_technologies(plugins)
        title = self._plugin_first_string(plugins, "Title")
        server_header = self._plugin_first_string(plugins, "HTTPServer") or \
            self._plugin_first_string(plugins, "Server")
        status_code = self._coerce_int(record.get("http_status") or record.get("status_code"))

        existing = (
            self.db.query(models.WebInterface)
            .filter(
                models.WebInterface.scan_id == scan.id,
                models.WebInterface.url == url,
                models.WebInterface.source == "whatweb",
            )
            .first()
        )
        if existing is None:
            wi = models.WebInterface(
                scan_id=scan.id,
                host_id=host_row.id if host_row else None,
                port_id=port_row.id if port_row else None,
                project_id=self._project_id,
                source="whatweb",
                url=url,
                protocol=protocol,
                port=port,
                ip_address=ip,
                status_code=status_code,
                title=(title or "")[:500] or None,
                server_header=(server_header or "")[:255] or None,
                content_length=None,  # whatweb --log-json carries no body length
                technologies=technologies or None,
                favicon_hash=None,    # whatweb has no favicon hash
                tls_info=None,        # whatweb has no structured TLS block
                raw=record,
            )
            self.db.add(wi)
            # Flush so a duplicate target later in the same file takes the
            # update branch instead of colliding on the unique constraint.
            self.db.flush()
        else:
            existing.technologies = technologies or None
            existing.status_code = status_code
            existing.title = (title or "")[:500] or None
            existing.server_header = (server_header or "")[:255] or None
            existing.raw = record
        return host_row.id if host_row else None

    # ------------------------------------------------------------------
    # whatweb plugin helpers

    @staticmethod
    def _plugin_first_string(plugins: Dict[str, Any], name: str) -> Optional[str]:
        """First ``string`` value for a named plugin, or None.  whatweb
        nests values as ``{"PluginName": {"string": [...], ...}}``."""
        entry = plugins.get(name)
        if not isinstance(entry, dict):
            return None
        values = entry.get("string")
        if isinstance(values, list) and values:
            first = values[0]
            return str(first) if first not in (None, "") else None
        return None

    @classmethod
    def _extract_technologies(cls, plugins: Dict[str, Any]) -> List[str]:
        """Flatten whatweb's plugin map into a tech-chip list.

        Every plugin name that isn't pure HTTP/metadata noise becomes a
        chip; a ``version`` array (when present) is appended so e.g.
        ``Apache`` + ``["2.4.41"]`` renders as ``"Apache 2.4.41"``.
        """
        techs: List[str] = []
        for name, entry in plugins.items():
            if not isinstance(name, str) or name.lower() in _NON_TECH_PLUGINS:
                continue
            versions: List[str] = []
            if isinstance(entry, dict):
                raw_versions = entry.get("version")
                if isinstance(raw_versions, list):
                    versions = [str(v).strip() for v in raw_versions if str(v).strip()]
                elif raw_versions not in (None, ""):
                    versions = [str(raw_versions).strip()]
            techs.append(f"{name} {'/'.join(versions)}".strip() if versions else name)
        return techs

    # ------------------------------------------------------------------
    # IP / port / scheme resolution (shared shape with the httpx parser)

    @classmethod
    def _resolve_ip_and_hostname(
        cls, record: Dict[str, Any], plugins: Dict[str, Any], url: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Pick the canonical IP + hostname for a whatweb record.

        IP precedence: whatweb's ``IP`` plugin (the resolved address) →
        the URL netloc if it's an IP literal.  Hostname: the URL netloc
        when it's NOT an IP literal.
        """
        ip: Optional[str] = None
        ip_candidate = cls._plugin_first_string(plugins, "IP")
        if cls._is_ip_literal(ip_candidate):
            ip = ip_candidate.strip("[]")
        if ip is None:
            try:
                h = urlparse(url).hostname
                if cls._is_ip_literal(h):
                    ip = h.strip("[]")
            except Exception:
                pass

        hostname: Optional[str] = None
        try:
            h = urlparse(url).hostname
            if h and not cls._is_ip_literal(h):
                hostname = h
        except Exception:
            pass

        return ip, hostname

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _is_ip_literal(value: Any) -> bool:
        if not value or not isinstance(value, str):
            return False
        try:
            ipaddress.ip_address(value.strip("[]"))
            return True
        except ValueError:
            return False

    @staticmethod
    def _port_from_url(url: str) -> Optional[int]:
        try:
            parsed = urlparse(url)
        except Exception:
            return None
        if parsed.port:
            return parsed.port
        if parsed.scheme == "https":
            return 443
        if parsed.scheme == "http":
            return 80
        return None

    @staticmethod
    def _scheme_from_url(url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
        except Exception:
            return None
        return parsed.scheme or None
