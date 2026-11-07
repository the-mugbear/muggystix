"""BlueStick observations — the tool-agnostic format (2.537.0).

A tool BlueStick has no parser for, a script, or an agent working from its own
output writes one JSON object per scanner observation, as JSON lines (a JSON
array is read too)::

    {"schema": "bluestick-observation/1", "tool": "my-scanner",
     "host": "10.0.0.5", "hostname": "web01.example.com",
     "port": 443, "protocol": "tcp", "service": "https",
     "title": "Outdated TLS library", "severity": "high",
     "description": "…", "solution": "…", "references": ["https://…"],
     "cvss": {"score": 9.8, "vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"},
     "cves": ["CVE-2024-0001", "CVE-2024-0002"], "exploitable": true,
     "plugin_id": "my-check-7", "evidence": "the response that showed it",
     "observed_at": "2026-11-06T10:00:00Z"}

**Every field is the uploader's statement, and an absent or null field is the
explicit absence of one** — the normalization rule every parser follows
(``source_statements``): no ``severity`` is ``UNKNOWN`` (never a default
rating), no ``exploitable`` is NULL (not "no exploit"), no ``cvss`` is no
score.  ``host`` is an address and is required: a line without one cannot be
placed and is skipped and counted, never guessed from the name.  ``title`` is
required unless ``check_id`` names a weakness-catalog check, in which case the
line is that check (the catalog's title and severity, the line's ``evidence``;
``misconfig_checks.record_misconfig``).  ``port`` is optional (an observation
with none is host-level); a port named is recorded open — the observation
proves it answered.  Rows carry ``source = external``; the tool is named on
the scan (the first line's ``tool``, else the upload's ``tool_name``, else
``external``).  ``cvss_version`` is derived from the vector by the writer, not
read from the line.

Recognised by its ``schema`` key (``looks_like_observations``); a cut last
line is reported as cut short like every JSON-lines format; a malformed line
is skipped and counted (``write_record``), never the file.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import VulnerabilitySeverity, VulnerabilitySource
from app.parsers.parser_utils import (
    ProgressBeat,
    ScanClock,
    ensure_scan,
    map_text_severity,
    normalize_ip,
    parse_rfc3339,
    persist_host_observation,
    upsert_vulnerability,
    write_record,
)
from app.parsers.streaming_json import iter_json_records
from app.services.dns_name_service import ObservationCache, bind_hostname
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.misconfig_checks import CHECKS, record_misconfig

logger = logging.getLogger(__name__)

SCHEMA_PREFIX = "bluestick-observation/"
DEFAULT_TOOL = "external"
_CVE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)
_PROTOCOLS = {"tcp", "udp", "sctp"}


class LineProblem(ValueError):
    """A line that is not an observation this format can place."""


def is_observation_record(record: Any) -> bool:
    return isinstance(record, dict) and str(record.get("schema") or "").lower().startswith(SCHEMA_PREFIX)


def _text(value: Any, limit: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit] if limit else text


def _port(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        raise LineProblem(f"port {value!r} is not a number")
    if not 0 < port < 65536:
        raise LineProblem(f"port {port} is out of range")
    return port


def _cves(value: Any) -> List[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out: List[str] = []
    for item in items:
        text = str(item or "").strip().upper()
        if text and _CVE.match(text) and text not in out:
            out.append(text)
    return out


def _references(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    items = value if isinstance(value, (list, tuple)) else [value]
    refs = [str(v).strip() for v in items if v is not None and str(v).strip()]
    return refs or None


def _exploitable(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise LineProblem(f"exploitable must be true, false or null, not {value!r}")


def _cvss(value: Any) -> tuple[Optional[float], Optional[str]]:
    """``{"score": …, "vector": …}``; either part may be absent."""
    if value is None:
        return None, None
    if not isinstance(value, dict):
        raise LineProblem("cvss must be an object with score and/or vector")
    score = value.get("score")
    if score is not None:
        try:
            score = float(score)
        except (TypeError, ValueError):
            raise LineProblem(f"cvss.score {score!r} is not a number")
        if not 0.0 <= score <= 10.0:
            raise LineProblem(f"cvss.score {score} is out of range")
    return score, _text(value.get("vector"), 200)


def _severity(value: Any) -> VulnerabilitySeverity:
    """Stated → mapped; absent → UNKNOWN; a word that is not a severity is a
    line problem (a silent UNKNOWN would hide a typo in the uploader's
    statement)."""
    if value is None or str(value).strip() == "":
        return VulnerabilitySeverity.UNKNOWN
    mapped = map_text_severity(str(value))
    if mapped == VulnerabilitySeverity.UNKNOWN and str(value).strip().lower() != "unknown":
        raise LineProblem(f"severity {value!r} is not critical / high / medium / low / info / unknown")
    return mapped


class ObservationsParser:
    """Parser for the BlueStick observation format (JSON lines or a JSON array)."""

    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)
        self._name_cache = ObservationCache()
        self._project_id: Optional[int] = None
        self.last_parse_stats: Dict[str, Any] = {}

    def _reset_caches(self) -> None:
        self._name_cache = ObservationCache()
        self.dedup_service.discard_rolled_back_state()

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        upload_tool = _text(kwargs.get("source_tool"), 100)
        records = iter_json_records(file_path, array_keys=("observations",), tool_label="BlueStick observations")

        scan: Optional[models.Scan] = None
        clock = ScanClock(models.SCAN_TIME_TOOL_RECORDS)
        seen = recorded = 0
        hosts: set = set()
        problems: List[str] = []

        beat = ProgressBeat("observations")
        for record in records:
            if not is_observation_record(record):
                continue
            beat.tick()
            seen += 1
            ip = normalize_ip(_text(record.get("host")))
            if not ip:
                problems.append(f"line {seen}: no host address" + (
                    f" (hostname {record.get('hostname')!r} is not enough)" if record.get("hostname") else ""))
                continue
            if scan is None:
                scan = ensure_scan(
                    self.db, filename=filename,
                    tool_name=(_text(record.get("tool"), 100) or upload_tool or DEFAULT_TOOL),
                    scan_type="vulnerability_scan", project_id=self._project_id,
                )
            clock.observe(parse_rfc3339(record.get("observed_at")))
            outcome = write_record(
                self.db,
                lambda: self._record(scan, record, ip),
                on_rollback=self._reset_caches,
            )
            if outcome.error is not None:
                logger.warning("observations: skipping line %d on %s: %s", seen, ip, outcome.error)
                problems.append(f"line {seen} on {ip}: {outcome.error}")
                continue
            recorded += 1
            hosts.add(ip)

        if seen == 0:
            raise ValueError(
                f"No BlueStick observation in {filename}: every line is an object whose "
                f"\"schema\" starts with \"{SCHEMA_PREFIX}\"."
            )
        if scan is None:
            raise ValueError(f"No observation in {filename} names a host address, so none can be placed.")

        clock.apply(scan)
        self.db.commit()

        self.last_parse_stats = {
            "skipped": len(problems),
            "warnings": (
                f"{len(problems)} line(s) were not recorded: " + "; ".join(problems[:10])
                + (" …" if len(problems) > 10 else "")
            ) if problems else None,
            "summary": (
                f"{recorded} scanner observation{'s' if recorded != 1 else ''} "
                f"on {len(hosts)} host{'s' if len(hosts) != 1 else ''}"
            ),
            "partial": bool(problems),
        }
        return scan

    def _record(self, scan: models.Scan, record: Dict[str, Any], ip: str) -> None:
        port = _port(record.get("port"))
        protocol = (_text(record.get("protocol")) or "tcp").lower()
        if protocol not in _PROTOCOLS:
            raise LineProblem(f"protocol {protocol!r} is not tcp / udp / sctp")
        hostname = (_text(record.get("hostname"), 253) or "").rstrip(".").lower() or None
        if hostname and normalize_ip(hostname):
            hostname = None
        check_id = _text(record.get("check_id"), 50)
        title = _text(record.get("title"), 500)
        if check_id and check_id not in CHECKS:
            raise LineProblem(f"check_id {check_id!r} is not a catalog check")
        if not check_id and not title:
            raise LineProblem("title is required (or check_id naming a catalog check)")
        severity = _severity(record.get("severity"))
        cvss_score, cvss_vector = _cvss(record.get("cvss"))
        cves = _cves(record.get("cves"))
        exploitable = _exploitable(record.get("exploitable"))

        ports = []
        if port:
            ports.append({"port_number": port, "protocol": protocol, "state": "open",
                          "service_name": _text(record.get("service"), 100)})
        host, port_map = persist_host_observation(
            dedup_service=self.dedup_service, scan_id=scan.id, ip_address=ip,
            hostname=hostname, ports=ports, project_id=self._project_id,
        )
        persisted_port = port_map.get((port, protocol)) if port else None
        name_id = bind_hostname(
            self.db, project_id=self._project_id, hostname=hostname,
            ip_address=ip, scan_id=scan.id, cache=self._name_cache,
        )
        evidence = _text(record.get("evidence"))

        if check_id:
            record_misconfig(
                self.db, check_id=check_id, host_id=host.id, scan_id=scan.id,
                source=VulnerabilitySource.EXTERNAL,
                port_id=persisted_port.id if persisted_port else None,
                name_id=name_id, evidence=evidence,
            )
            return

        upsert_vulnerability(
            db=self.db, host_id=host.id, scan_id=scan.id, source=VulnerabilitySource.EXTERNAL,
            title=title, severity=severity,
            plugin_id=_text(record.get("plugin_id"), 50),
            port_id=persisted_port.id if persisted_port else None,
            description=_text(record.get("description")),
            solution=_text(record.get("solution")),
            references=_references(record.get("references")),
            cvss_score=cvss_score, cvss_vector=cvss_vector,
            cve_id=cves[0] if cves else None, cve_ids=cves,
            exploitable=exploitable,
            name_id=name_id,
            # Several lines may share a plugin id with different titles.
            key_on_title=True,
            plugin_output=evidence,
        )
