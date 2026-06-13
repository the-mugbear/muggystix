"""
httpx (ProjectDiscovery) JSONL parser — v2.12.0.

httpx is the canonical fast web fingerprinter recommended by agents in
feedback #2.  Each line of its output (with ``-json``) is a self-
contained JSON record describing one probe:

    {
      "timestamp": "...",
      "url": "https://10.0.1.5/",
      "input": "10.0.1.5:443",
      "title": "Welcome",
      "scheme": "https",
      "port": "443",
      "status_code": 200,
      "content_length": 612,
      "tech": ["Nginx:1.18.0", "React"],
      "webserver": "nginx/1.18.0",
      "favicon": "abc123...",
      "tls": {"issuer_dn": "...", "not_after": "...", ...},
      "host": "10.0.1.5",
      ...
    }

This parser:
  * Accepts .json (single-object or array) AND .jsonl (one object per line).
  * Resolves host_id from httpx's ``host`` / ``input`` / parsed URL.
  * Resolves port_id from (host_id, port, protocol) best-effort.
  * Writes rows into the unified ``web_interfaces`` table with
    ``source="httpx"``.
  * Idempotent via the (scan_id, url, source) unique constraint —
    re-parsing the same file is safe.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.db import models
from app.services.cert_fields import derive_cert_fields
from app.parsers.parser_utils import (
    correlate_scan,
    record_hosts_in_scan,
    resolve_host_cached,
    resolve_port_cached,
)
from app.parsers.streaming_json import iter_json_records

logger = logging.getLogger(__name__)


def looks_like_httpx(sample: bytes, filename: str) -> bool:
    """Content-based detection for httpx JSONL or JSON output.

    Filename-based routing (``"httpx" in filename``) is too narrow —
    agents will often upload whatever they named the file.  Look for
    httpx's distinctive field combination in the first non-blank line:
    either ``url`` + ``tech`` (httpx's signature field) or ``url`` +
    ``webserver`` + ``status_code``.

    v2.28.2 — accepts ``bytes`` (matching the rest of the content-
    detection family in ``app.parsers.content_detection``).  The prior
    signature claimed ``str`` but the caller in IngestionService
    always passed ``bytes`` from ``_read_sample``, so a non-httpx
    .json upload reaching this sniffer crashed with ``TypeError:
    startswith first arg must be bytes or a tuple of bytes, not str``
    on the ``line.startswith("{")`` line below — taking down every
    .json file that the dispatch tried httpx-first.
    """
    if "httpx" in filename.lower():
        return True
    # Tolerate both bytes (the production caller) and str (older
    # callers / tests).  Decode once at the top so the rest of the
    # function works in str-space.
    if isinstance(sample, (bytes, bytearray)):
        text = sample.decode("utf-8", errors="replace")
    else:
        text = sample
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return False  # first JSON-ish line must parse for httpx
        if not isinstance(obj, dict):
            return False
        has_url = "url" in obj
        has_tech = "tech" in obj
        has_server = "webserver" in obj
        has_status = "status_code" in obj or "status-code" in obj
        if has_url and (has_tech or (has_server and has_status)):
            return True
        return False
    return False


class HttpxParser:
    """Parser for httpx JSON / JSONL output."""

    def __init__(self, db: Session):
        self.db = db
        self._project_id: Optional[int] = None
        # Per-file host/port resolution caches — collapse the per-record Host
        # and Port lookups (many records share a host) to a dict hit.
        self._host_cache: dict = {}
        self._port_cache: dict = {}

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        self._host_cache.clear()
        self._port_cache.clear()
        start = time.time()
        logger.info("Starting httpx parse of %s", filename)

        records = self._load_records(file_path)
        if not records:
            raise ValueError("httpx file contained no parseable records")

        scan = models.Scan(
            filename=filename,
            scan_type="web_fingerprint",
            tool_name="httpx",
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
            except Exception as exc:
                logger.warning("httpx: skipping record due to %s", exc)
                skipped += 1

        # v2.12.2 — write HostScanHistory rows so the recon summary's
        # per-host breakdown + hosts_discovered count see this scan.
        # Without this, web-only ingests don't contribute to
        # /agent/recon/summary because that query joins through
        # host_scan_history, not web_interfaces.
        record_hosts_in_scan(self.db, scan.id, host_ids_seen)

        self.db.commit()

        try:
            correlate_scan(self.db, scan.id)
        except Exception as exc:
            logger.warning("httpx scan %s correlation failed: %s", scan.id, exc)

        elapsed = time.time() - start
        logger.info(
            "httpx %s: %d web_interfaces written, %d skipped in %.2fs",
            filename, written, skipped, elapsed,
        )
        # Expose ingestion quality so the orchestrator can persist it on
        # the IngestionJob row.  Previously the skipped count was only
        # logged, so a "completed" job that lost 30 records looked clean
        # to the user.  See IngestionService._process_job's return dict.
        self.last_parse_stats = {
            "skipped": skipped,
            "warnings": (
                f"{skipped} httpx record(s) malformed or missing required fields"
                if skipped > 0 else None
            ),
            "summary": f"{written} web interface{'s' if written != 1 else ''}",
        }
        return scan

    # ---------------------------------------------------------------

    def _load_records(self, file_path: str) -> Iterable[Dict[str, Any]]:
        """Stream httpx output in either JSON (array, single object) or
        JSONL form.

        httpx's default is JSONL (one object per line); some agents
        pipe through ``jq`` and end up with a single-line array or a
        pretty-printed single object.  The streaming helper handles
        all three uniformly and avoids loading multi-hundred-MB
        exports into memory.
        """
        return iter_json_records(file_path, tool_label="httpx JSON")

    def _upsert_record(self, record: Dict[str, Any], scan: models.Scan) -> Optional[int]:
        """Write or update a single web_interfaces row.

        Returns the resolved ``host_id`` on success (so the caller can
        write a HostScanHistory row for the per-session rollup), or
        ``None`` if the record was skipped (e.g. no URL or host could
        not be resolved).
        """
        url = record.get("url")
        if not url:
            return None

        # Resolve ip + hostname + port.  httpx's ``host`` field may hold
        # either an IP literal OR a hostname (the latter happens when
        # -tls-probe expands TLS-certificate SANs like "localhost" or
        # "pi.hole" into fresh probes).  ``host_ip`` is the resolved IP
        # and is present in recent httpx versions.  Pre-v2.13.1 we
        # picked ``host`` unconditionally and stored hostnames in the
        # ip_address column, polluting the host table with string-typed
        # IDs that couldn't be correlated to subnets.
        ip, hostname = self._resolve_ip_and_hostname(record, url)
        if ip is None:
            # No valid IP could be extracted — drop the record rather
            # than invent one.  A non-IP "host" string is a hostname we
            # couldn't resolve from the httpx record alone; let DNS
            # enrichment fill it in later once a real IP is observed.
            logger.info(
                "httpx: skipping record with no resolvable IP (host=%r, host_ip=%r, input=%r, url=%r)",
                record.get("host"), record.get("host_ip"), record.get("input"), url,
            )
            return None

        port = self._coerce_int(record.get("port")) or self._port_from_url(url)
        protocol = (record.get("scheme") or "").lower() or self._scheme_from_url(url)

        # Resolve host (create on demand — httpx hit it, it's real) and port,
        # both cached per-file.  Enriches a missing hostname on a cache/db hit
        # from the value httpx learned (TLS cert SAN, ``input``, etc.).
        host_row = resolve_host_cached(
            self.db, self._project_id, ip, self._host_cache, hostname=hostname,
        )
        port_row = resolve_port_cached(
            self.db, host_row, port, self._port_cache,
        )

        # Flatten technologies for chip rendering.
        tech_raw = record.get("tech") or record.get("technologies") or []
        if isinstance(tech_raw, dict):
            # Some httpx variants emit {"Nginx": "1.18.0", ...}
            technologies = [
                f"{k} {v}".strip() if v else k for k, v in tech_raw.items()
            ]
        elif isinstance(tech_raw, list):
            technologies = [str(t) for t in tech_raw if t]
        else:
            technologies = []

        tls_info = record.get("tls") if isinstance(record.get("tls"), dict) else None
        # Promote the queryable cert predicates once, here at ingest, so the
        # insight surfaces read typed columns instead of re-parsing tls_info.
        cert_not_after, cert_self_signed = derive_cert_fields(tls_info)

        # Favicon hash — httpx emits as ``favicon`` (mmh3 hex).
        favicon_hash = record.get("favicon") or record.get("favicon_path")
        if favicon_hash:
            favicon_hash = str(favicon_hash)[:64]

        # Upsert on (scan_id, url, source).  Since scan_id is new for
        # every upload, effectively an insert unless an earlier
        # httpx record on the same page is a dup (httpx itself
        # doesn't dedupe probes).
        existing = (
            self.db.query(models.WebInterface)
            .filter(
                models.WebInterface.scan_id == scan.id,
                models.WebInterface.url == url,
                models.WebInterface.source == "httpx",
            )
            .first()
        )
        if existing is None:
            wi = models.WebInterface(
                scan_id=scan.id,
                host_id=host_row.id if host_row else None,
                port_id=port_row.id if port_row else None,
                project_id=self._project_id,
                source="httpx",
                url=url,
                protocol=protocol,
                port=port,
                ip_address=ip,
                status_code=self._coerce_int(record.get("status_code") or record.get("status-code")),
                title=(record.get("title") or "")[:500] or None,
                server_header=(record.get("webserver") or record.get("server") or "")[:255] or None,
                content_length=self._coerce_int(record.get("content_length") or record.get("content-length")),
                technologies=technologies or None,
                favicon_hash=favicon_hash,
                tls_info=tls_info,
                cert_not_after=cert_not_after,
                cert_self_signed=cert_self_signed,
                raw=record,
            )
            self.db.add(wi)
            # Flush so a follow-up record with the same URL in the
            # same JSONL file (e.g. an httpx redirect chain) sees the
            # insert and takes the update branch instead of colliding
            # on the (scan_id, url, source) unique constraint.
            self.db.flush()
        else:
            # Update in place on re-ingest.
            existing.technologies = technologies or None
            existing.status_code = self._coerce_int(record.get("status_code") or record.get("status-code"))
            existing.title = (record.get("title") or "")[:500] or None
            existing.server_header = (record.get("webserver") or record.get("server") or "")[:255] or None
            existing.tls_info = tls_info
            existing.cert_not_after = cert_not_after
            existing.cert_self_signed = cert_self_signed
            existing.raw = record
        return host_row.id if host_row else None

    # ---------------------------------------------------------------

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
        """True iff ``value`` parses as an IPv4 or IPv6 literal."""
        if not value or not isinstance(value, str):
            return False
        try:
            ipaddress.ip_address(value.strip("[]"))  # strip [::1]-style brackets
            return True
        except ValueError:
            return False

    @classmethod
    def _resolve_ip_and_hostname(
        cls, record: Dict[str, Any], url: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Pick the canonical IP + hostname from an httpx record.

        Precedence for the IP:
          1. ``host_ip`` — recent httpx versions emit this as the
             resolved address; always an IP literal.
          2. ``host`` — only if it parses as an IP literal.  ``host`` is
             the echo of whatever httpx used to reach the target, which
             is sometimes a hostname (TLS-cert SAN expansion makes
             ``host`` = ``"localhost"`` / ``"pi.hole"``).
          3. ``input`` — same rule, IP literal only.
          4. URL hostname — only if it parses as an IP literal.

        Hostname selection (independent of IP):
          - ``host`` if it's a non-IP string.
          - Else the URL hostname if it's a non-IP string.
          - Else ``input`` if it's a non-IP string.

        Returns ``(ip, hostname)`` with either slot possibly None.
        """
        ip: Optional[str] = None
        for field in ("host_ip", "host", "input"):
            val = record.get(field)
            if cls._is_ip_literal(val):
                ip = val.strip("[]")
                break
        if ip is None:
            # Last try: the URL's netloc may carry an IP.
            try:
                parsed = urlparse(url)
                h = parsed.hostname
                if cls._is_ip_literal(h):
                    ip = h.strip("[]")
            except Exception:
                pass

        hostname: Optional[str] = None
        for field in ("host", "input"):
            val = record.get(field)
            if not val or not isinstance(val, str):
                continue
            stripped = val.strip()
            if cls._is_ip_literal(stripped):
                continue
            # Reject ``ip:port`` shapes (common in httpx ``input``) —
            # the left side is an IP literal, so the whole string is
            # a target locator, not a hostname.  Without this check
            # v2.13.2 output had ``hostname = "192.168.0.1:80"``.
            if ":" in stripped and cls._is_ip_literal(stripped.split(":", 1)[0]):
                continue
            hostname = stripped
            break
        if hostname is None:
            try:
                h = urlparse(url).hostname
                if h and not cls._is_ip_literal(h):
                    hostname = h
            except Exception:
                pass

        return ip, hostname

    @classmethod
    def _extract_ip_from_url(cls, url: str) -> Optional[str]:
        """Kept for backwards compatibility — returns URL hostname iff it's an IP literal."""
        try:
            parsed = urlparse(url)
        except Exception:
            return None
        h = parsed.hostname
        if cls._is_ip_literal(h):
            return h.strip("[]")
        return None

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
