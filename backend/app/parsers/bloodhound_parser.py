from __future__ import annotations

import json
import logging
import os
import re
from typing import Iterable, Iterator

import ijson
from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import correlate_scan, ensure_scan, extract_first_ip, persist_host_observation
from app.services.host_deduplication_service import HostDeduplicationService

logger = logging.getLogger(__name__)

# Files larger than this are streamed with ijson instead of json.load().
# A 200 MB BloodHound export json.load()s to ~1-2 GB of Python objects; with
# multiple concurrent uploads the worker would OOM. ijson reads incrementally
# at constant memory.
_BLOODHOUND_STREAM_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MB

# Heuristic: peek this many bytes of the file to detect whether the top
# level is an array, an object with "data", or an object with "computers".
# All BloodHound exports we've seen put one of those keys near the start.
_BLOODHOUND_STRUCTURE_PEEK_BYTES = 64 * 1024


class BloodHoundParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)
        self.last_parse_stats: dict | None = None

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        project_id = kwargs.get("project_id")
        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name="bloodhound",
            scan_type="ad_inventory",
            project_id=project_id,
        )

        file_size = os.path.getsize(file_path)
        if file_size >= _BLOODHOUND_STREAM_THRESHOLD_BYTES:
            logger.info(
                "BloodHound %s (%d bytes) over %d-byte threshold; streaming with ijson",
                filename, file_size, _BLOODHOUND_STREAM_THRESHOLD_BYTES,
            )
            entries: Iterable[dict] = self._stream_entries(file_path)
        else:
            entries = self._load_entries(file_path)

        # v2.417.0 (review R03/R12) — what the file held and what was used.
        # BloodHound's security content (ACEs, sessions, local admins,
        # delegation, SPNs) is not imported; a computer without an address
        # is skipped.  Both used to be silent, and a file with no usable
        # computer "succeeded" with nothing in it.
        entries_seen = 0
        imported = 0
        no_address: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entries_seen += 1
            properties = entry.get("Properties") or entry.get("properties") or {}
            ip_address = extract_first_ip(
                str(
                    properties.get("ipv4")
                    or properties.get("IPAddress")
                    or properties.get("ip")
                    or entry.get("ip")
                    or ""
                )
            )
            hostname = (
                properties.get("name")
                or properties.get("dnshostname")
                or entry.get("name")
            )
            if not ip_address:
                no_address.append(str(hostname or entry.get("ObjectIdentifier") or "unnamed object"))
                continue

            persist_host_observation(
                dedup_service=self.dedup_service,
                scan_id=scan.id,
                ip_address=ip_address,
                hostname=str(hostname) if hostname else None,
                ports=[],
                project_id=project_id,
            )
            imported += 1

        if imported == 0:
            raise ValueError(
                f"BloodHound file had {entries_seen} object(s) and no computer with an IPv4 "
                "address. BlueStick imports only computers' addresses and names from "
                "BloodHound (not users, groups, ACLs, sessions or delegation); a SharpHound "
                "computers file needs resolved addresses (e.g. an `ipv4` property)."
            )

        correlate_scan(self.db, scan.id)
        self.last_parse_stats = {
            "skipped": len(no_address),
            "warnings": (
                f"{len(no_address)} object(s) with no IPv4 address were not imported: "
                + ", ".join(no_address[:10]) + (" …" if len(no_address) > 10 else "")
                if no_address else None
            ),
            "summary": (
                f"{imported} computer{'s' if imported != 1 else ''} (address and name only; "
                "BloodHound relationships and properties are not imported)"
            ),
            "partial": bool(no_address),
        }
        return scan

    @staticmethod
    def _load_entries(file_path: str) -> Iterable[dict]:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            try:
                payload = json.load(handle)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid or truncated BloodHound JSON: {exc}") from exc
        if isinstance(payload, list):
            return payload
        return payload.get("data") or payload.get("computers") or []

    @staticmethod
    def _stream_entries(file_path: str) -> Iterator[dict]:
        """Stream entries via ijson without loading the whole file into memory.

        Detects the top-level structure (array | {data: [...]} | {computers: [...]})
        by peeking at the first chunk, then yields items from the appropriate
        array path. Falls back to raising ValueError if no known array shape
        is detected.
        """
        prefix = _detect_bloodhound_prefix(file_path)
        if prefix is None:
            raise ValueError(
                "BloodHound JSON exceeds streaming threshold but no recognized "
                "top-level array (array | data | computers) was found at the start."
            )
        with open(file_path, "rb") as handle:
            try:
                yield from ijson.items(handle, prefix)
            except ijson.JSONError as exc:
                raise ValueError(
                    f"Invalid or truncated BloodHound JSON during streaming: {exc}"
                ) from exc


_PREFIX_PATTERNS = (
    # (regex against the peek window, ijson prefix to use)
    (re.compile(rb'^\s*\['), "item"),
    (re.compile(rb'^\s*\{[^}]*?"data"\s*:\s*\['), "data.item"),
    (re.compile(rb'^\s*\{[^}]*?"computers"\s*:\s*\['), "computers.item"),
)


def _detect_bloodhound_prefix(file_path: str) -> str | None:
    with open(file_path, "rb") as handle:
        head = handle.read(_BLOODHOUND_STRUCTURE_PEEK_BYTES)
    for pattern, prefix in _PREFIX_PATTERNS:
        if pattern.search(head):
            return prefix
    return None
