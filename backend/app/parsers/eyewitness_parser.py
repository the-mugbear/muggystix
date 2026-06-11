"""
EyeWitness parser — v2.12.0.

Rewritten to write into the unified ``web_interfaces`` table (same
target as the new httpx parser) instead of the deprecated
``eyewitness_results`` table.

EyeWitness output shapes handled:
  * JSON report (``report.json`` or similar) — list of probes under
    ``results`` or ``data`` or at the top level.
  * CSV report — one probe per row with a URL column.
  * ZIP bundle containing the JSON report plus a ``screenshots/`` dir.
    When a zip is uploaded, the parser extracts PNGs into
    ``uploads/web_screenshots/{scan_id}/`` and stores the relative
    path in ``screenshot_path``; served at
    ``GET /projects/{pid}/web-interfaces/{id}/screenshot``.

Rows carry ``source="eyewitness"`` and resolve ``host_id`` from the
URL's IP (best effort).  If the URL contains a hostname instead of
an IP, we fall back to ``ip_address`` from the record if EyeWitness
emitted one.
"""

from __future__ import annotations

import csv
import logging
import os
import re
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import models
from app.parsers.parser_utils import correlate_scan, record_hosts_in_scan
from app.parsers.streaming_json import iter_json_records

logger = logging.getLogger(__name__)

# Decompression-bomb defenses for EyeWitness ZIP bundles.
# A ~1 KB zip can claim to contain 4 GB of zero-filled PNGs; without these
# caps the extracted bytes would exhaust the upload volume.
_EYEWITNESS_MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024   # 500 MB across all extracted files
_EYEWITNESS_MAX_PER_FILE_UNCOMPRESSED = 50 * 1024 * 1024  # 50 MB per single file
_EYEWITNESS_MAX_FILE_COUNT = 5000                         # entries in the zip


def _safe_extract_zip_member(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    dst: Path,
    *,
    per_file_cap: int,
    total_so_far: int,
    total_cap: int,
) -> int:
    """Stream-extract a zip member while enforcing per-file and running-total
    uncompressed-byte caps. Returns the number of bytes actually written.

    Defends against a spoofed central-directory size_field by counting bytes
    on the way out and aborting + cleaning up if the cap is exceeded mid-stream.
    """
    written = 0
    try:
        with zf.open(info) as src, open(dst, "wb") as out:
            while True:
                chunk = src.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > per_file_cap or total_so_far + written > total_cap:
                    out.close()
                    dst.unlink(missing_ok=True)
                    raise ValueError(
                        f"EyeWitness zip entry {info.filename!r} exceeded "
                        f"decompression caps mid-stream after {written} bytes"
                    )
                out.write(chunk)
    except Exception:
        dst.unlink(missing_ok=True)
        raise
    return written


def _eyewitness_parse_stats(skipped: int) -> Dict[str, Any]:
    """Shared parse-stats shape for all three EyeWitness parse paths
    (json, zip bundle, csv).  Persisted onto the IngestionJob row by
    IngestionService so the UI can show "12 rows skipped" alongside
    the completed status."""
    return {
        "skipped": skipped,
        "warnings": (
            f"{skipped} EyeWitness record(s) malformed or missing required fields"
            if skipped > 0 else None
        ),
    }


class EyewitnessParser:
    def __init__(self, db: Session):
        self.db = db
        self._project_id: Optional[int] = None

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        # Per-parse memoization for the per-record Host/Port lookups.  EyeWitness
        # emits many URLs per host (http + https, several ports/paths), so the
        # same IP and (host, port) recur across records — without a cache each
        # record re-queries hosts_v2 / ports_v2 (the N+1 the parser was flagged
        # for).  Streaming-safe: filled lazily, including the Host rows this
        # parse creates.  Reset per upload (one parse_file == one scan).
        self._host_cache: dict = {}   # ip -> Host (or None when unresolvable)
        self._port_cache: dict = {}   # (host_id, port_number) -> Port or None
        start = time.time()
        logger.info("Starting EyeWitness parse of %s", filename)

        lower = filename.lower()
        try:
            if lower.endswith(".zip"):
                result = self._parse_zip_bundle(file_path, filename)
            elif lower.endswith(".json"):
                result = self._parse_json(file_path, filename, screenshot_dir=None)
            elif lower.endswith(".csv"):
                result = self._parse_csv(file_path, filename)
            else:
                raise ValueError("Unsupported EyeWitness file format. Expected .json, .csv, or .zip")
        except Exception:
            elapsed = time.time() - start
            logger.exception("EyeWitness parse of %s failed after %.2fs", filename, elapsed)
            raise

        logger.info(
            "Successfully parsed EyeWitness %s in %.2fs",
            filename, time.time() - start,
        )
        return result

    # -----------------------------------------------------------------
    # Shape-specific parsers
    # -----------------------------------------------------------------

    def _parse_zip_bundle(self, file_path: str, filename: str) -> models.Scan:
        """Extract a .zip bundle that contains the report JSON plus a
        ``screenshots/`` directory.

        Screenshots land in ``uploads/web_screenshots/{scan_id}/``.
        The web_interfaces row's ``screenshot_path`` is stored relative
        to that directory so the streaming endpoint can resolve it by
        joining with the configured storage root.
        """
        # Create the scan upfront so we know scan_id for the extract
        # directory.  If parsing fails mid-way, the scan row gets
        # cleaned up with the rollback.
        scan = self._build_scan(filename)
        self.db.add(scan)
        self.db.flush()

        screenshot_dir = Path(settings.UPLOAD_DIR) / "web_screenshots" / str(scan.id)
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        report_json_path: Optional[Path] = None
        extracted_png_count = 0

        with zipfile.ZipFile(file_path) as zf:
            infolist = zf.infolist()

            # Decompression-bomb pre-check. uncompressed_size in the central
            # directory header can be spoofed, so we also enforce a streaming
            # cap below — but rejecting an obvious bomb up-front saves I/O.
            if len(infolist) > _EYEWITNESS_MAX_FILE_COUNT:
                raise ValueError(
                    f"EyeWitness zip contains {len(infolist)} entries "
                    f"(max {_EYEWITNESS_MAX_FILE_COUNT})"
                )
            declared_total = 0
            for info in infolist:
                if info.is_dir():
                    continue
                if info.file_size > _EYEWITNESS_MAX_PER_FILE_UNCOMPRESSED:
                    raise ValueError(
                        f"EyeWitness zip entry {info.filename!r} declares "
                        f"uncompressed size {info.file_size} bytes "
                        f"(max {_EYEWITNESS_MAX_PER_FILE_UNCOMPRESSED})"
                    )
                declared_total += info.file_size
                if declared_total > _EYEWITNESS_MAX_TOTAL_UNCOMPRESSED:
                    raise ValueError(
                        f"EyeWitness zip declares >"
                        f"{_EYEWITNESS_MAX_TOTAL_UNCOMPRESSED} bytes uncompressed"
                    )

            actual_total = 0
            for info in infolist:
                if info.is_dir():
                    continue
                # Path-traversal guard — reject any entry whose
                # resolved target escapes the extract dir.
                safe_name = os.path.basename(info.filename)
                if not safe_name or safe_name.startswith("."):
                    continue
                lower = safe_name.lower()
                if lower.endswith(".json"):
                    # Extract the report JSON into the parse area.
                    dst = screenshot_dir.parent / f"report-{scan.id}.json"
                    actual_total += _safe_extract_zip_member(
                        zf, info, dst,
                        per_file_cap=_EYEWITNESS_MAX_PER_FILE_UNCOMPRESSED,
                        total_so_far=actual_total,
                        total_cap=_EYEWITNESS_MAX_TOTAL_UNCOMPRESSED,
                    )
                    report_json_path = dst
                elif lower.endswith((".png", ".jpg", ".jpeg")):
                    dst = screenshot_dir / safe_name
                    actual_total += _safe_extract_zip_member(
                        zf, info, dst,
                        per_file_cap=_EYEWITNESS_MAX_PER_FILE_UNCOMPRESSED,
                        total_so_far=actual_total,
                        total_cap=_EYEWITNESS_MAX_TOTAL_UNCOMPRESSED,
                    )
                    extracted_png_count += 1

        if report_json_path is None:
            raise ValueError("EyeWitness zip bundle contained no .json report")

        written, skipped = self._load_and_write_json(
            report_json_path, scan, screenshot_dir_rel=str(scan.id),
        )

        self.db.commit()
        self._finalize(scan)
        logger.info(
            "EyeWitness bundle %s: %d web_interfaces written (%d skipped), %d screenshots extracted",
            filename, written, skipped, extracted_png_count,
        )
        self.last_parse_stats = _eyewitness_parse_stats(skipped)
        return scan

    def _parse_json(
        self, file_path: str, filename: str, screenshot_dir: Optional[str],
    ) -> models.Scan:
        """Parse a bare EyeWitness JSON report (no zip bundle)."""
        scan = self._build_scan(filename)
        self.db.add(scan)
        self.db.flush()
        _written, skipped = self._load_and_write_json(
            Path(file_path), scan, screenshot_dir_rel=None,
        )
        self.db.commit()
        self._finalize(scan)
        self.last_parse_stats = _eyewitness_parse_stats(skipped)
        return scan

    def _parse_csv(self, file_path: str, filename: str) -> models.Scan:
        """Parse EyeWitness CSV output.  Screenshots unavailable from
        CSV-only uploads — the parser stores the raw path EyeWitness
        emitted (usually an absolute path on the agent's machine) but
        the streaming endpoint will 404 unless a matching zip is also
        uploaded."""
        scan = self._build_scan(filename)
        self.db.add(scan)
        self.db.flush()

        host_ids_seen: set = set()
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            written = 0
            skipped = 0
            for row in reader:
                try:
                    host_id = self._write_row(row, scan, screenshot_dir_rel=None, csv_mode=True)
                    if host_id is None:
                        skipped += 1
                    else:
                        written += 1
                        if host_id is not True:
                            host_ids_seen.add(host_id)
                except Exception as exc:
                    logger.warning("EyeWitness CSV row skipped: %s", exc)
                    skipped += 1

        # v2.12.2: write HostScanHistory rows so /agent/recon/summary
        # counts EyeWitness ingests against the per-session host total.
        record_hosts_in_scan(self.db, scan.id, host_ids_seen)
        self.db.commit()
        self._finalize(scan)
        logger.info("EyeWitness CSV %s: %d rows written, %d skipped", filename, written, skipped)
        self.last_parse_stats = _eyewitness_parse_stats(skipped)
        return scan

    # -----------------------------------------------------------------
    # Shared helpers
    # -----------------------------------------------------------------

    def _build_scan(self, filename: str) -> models.Scan:
        return models.Scan(
            filename=filename,
            scan_type="web_screenshot",
            tool_name="eyewitness",
            created_at=datetime.utcnow(),
            project_id=self._project_id,
        )

    def _finalize(self, scan: models.Scan) -> None:
        try:
            correlate_scan(self.db, scan.id)
        except Exception as exc:
            logger.warning("EyeWitness scan %s correlation failed: %s", scan.id, exc)

    def _load_and_write_json(
        self, path: Path, scan: models.Scan, screenshot_dir_rel: Optional[str],
    ) -> tuple[int, int]:
        # EyeWitness JSON for large engagement scopes can run to
        # hundreds of MB once the per-record metadata + page_text body
        # is included.  Stream rather than load the whole graph.
        records = iter_json_records(
            str(path),
            array_keys=("results", "data", "pages", "servers"),
            tool_label="EyeWitness JSON",
        )
        written = 0
        skipped = 0
        host_ids_seen: set = set()
        for record in records:
            try:
                host_id = self._write_row(record, scan, screenshot_dir_rel=screenshot_dir_rel)
                if host_id is None:
                    skipped += 1
                else:
                    written += 1
                    if host_id is not True:  # _write_row returns True for "wrote, no host_id"
                        host_ids_seen.add(host_id)
            except Exception as exc:
                logger.warning("EyeWitness record skipped: %s", exc)
                skipped += 1
        # v2.12.2: same host_scan_history fix as httpx_parser.  Web-only
        # ingests must contribute to recon-session host counts.
        record_hosts_in_scan(self.db, scan.id, host_ids_seen)
        return written, skipped

    def _write_row(
        self,
        record: Dict[str, Any],
        scan: models.Scan,
        screenshot_dir_rel: Optional[str],
        csv_mode: bool = False,
    ) -> Optional[int]:
        """Write or update a web_interfaces row from one EyeWitness probe.

        Returns the resolved ``host_id`` so the caller (``_load_and_write_json``
        and ``_parse_csv``) can collect distinct hosts and write
        HostScanHistory rows in one batch via ``record_hosts_in_scan``.
        Returns ``None`` if the record was skipped (no URL).
        Returns ``True`` if the record was written but no host_id could
        be resolved (e.g. URL with hostname instead of IP, no matching
        Host row) — caller should count it as written but not as a
        history candidate.
        """
        url = record.get("url") or record.get("URL") or record.get("remote_system")
        if not url:
            return None
        ip = record.get("ip") or record.get("IP") or record.get("ip_address") or self._extract_ip_from_url(url)
        port = self._coerce_int(record.get("port") or record.get("Port"))
        if port is None:
            port = self._port_from_url(url)
        protocol = (record.get("protocol") or record.get("Protocol") or "").lower()
        if not protocol:
            protocol = self._scheme_from_url(url)

        host_row = None
        if ip:
            host_row = self._host_cache.get(ip)
            if host_row is None:
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
                        state="up",
                        project_id=self._project_id,
                    )
                    self.db.add(host_row)
                    self.db.flush()
                self._host_cache[ip] = host_row

        port_row = None
        if host_row and port:
            pkey = (host_row.id, port)
            if pkey in self._port_cache:
                port_row = self._port_cache[pkey]
            else:
                port_row = (
                    self.db.query(models.Port)
                    .filter(
                        models.Port.host_id == host_row.id,
                        models.Port.port_number == port,
                        models.Port.protocol == "tcp",
                    )
                    .first()
                )
                # Cache the miss too: this parse never creates Port rows, so a
                # (host, port) that's absent now stays absent for the file.
                self._port_cache[pkey] = port_row

        # Screenshot handling.  If we extracted from a zip, the record's
        # screenshot_path might be absolute or subdirectory-prefixed —
        # normalize to basename and check the extracted dir.
        raw_screenshot = record.get("screenshot_path") or record.get("screenshot")
        screenshot_rel: Optional[str] = None
        if raw_screenshot:
            basename = os.path.basename(str(raw_screenshot))
            if screenshot_dir_rel and basename:
                candidate = (
                    Path(settings.UPLOAD_DIR) / "web_screenshots"
                    / screenshot_dir_rel / basename
                )
                if candidate.exists():
                    screenshot_rel = f"{screenshot_dir_rel}/{basename}"
            if not screenshot_rel and not csv_mode:
                # No extracted PNG match — keep the raw path for
                # debugging but the streaming endpoint will 404.
                screenshot_rel = None

        title = record.get("title") or record.get("page_title") or record.get("Title")
        server = record.get("server") or record.get("server_header") or record.get("Server")
        content_length = self._coerce_int(
            record.get("content_length") or record.get("Content Length")
        )
        response_code = self._coerce_int(
            record.get("response_code") or record.get("Response Code") or record.get("status_code")
        )
        page_text = record.get("page_text") or record.get("Page Text")

        existing = (
            self.db.query(models.WebInterface)
            .filter(
                models.WebInterface.scan_id == scan.id,
                models.WebInterface.url == url,
                models.WebInterface.source == "eyewitness",
            )
            .first()
        )
        if existing is None:
            wi = models.WebInterface(
                scan_id=scan.id,
                host_id=host_row.id if host_row else None,
                port_id=port_row.id if port_row else None,
                project_id=self._project_id,
                source="eyewitness",
                url=url,
                protocol=protocol or None,
                port=port,
                ip_address=ip,
                status_code=response_code,
                title=(title or "")[:500] or None,
                server_header=(server or "")[:255] or None,
                content_length=content_length,
                screenshot_path=screenshot_rel,
                page_text=(page_text or None),
                raw=record,
            )
            self.db.add(wi)
        else:
            existing.status_code = response_code
            existing.title = (title or "")[:500] or None
            existing.server_header = (server or "")[:255] or None
            existing.screenshot_path = screenshot_rel or existing.screenshot_path
            existing.raw = record
        return host_row.id if host_row else True

    # -----------------------------------------------------------------

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _extract_ip_from_url(url: str) -> Optional[str]:
        match = re.search(r"(\d+\.\d+\.\d+\.\d+)", url or "")
        return match.group(1) if match else None

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
