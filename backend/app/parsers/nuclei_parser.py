"""Parser for Nuclei JSON results (``-je file.json`` / ``-jsonl`` / ``-j``).

v2.411.0.  Nuclei was advertised as ingestible (tool registry, the output
contract, AGENTS.md's upload table, the Tool Reference run command) while no
parser existed, so an upload was "not recognised".  Every result is one
template match::

    {"template-id": "http-missing-security-headers",
     "info": {"name": "HTTP Missing Security Headers", "severity": "info",
              "description": "…", "reference": ["…"], "remediation": "…",
              "classification": {"cve-id": null, "cvss-score": 0}},
     "matcher-name": "strict-transport-security",
     "type": "http", "host": "https://www.example.com", "port": "443",
     "url": "https://www.example.com", "matched-at": "https://www.example.com",
     "ip": "10.0.0.5", "timestamp": "2026-09-25T10:00:00.123456789Z"}

Each match becomes a scanner observation on the host (and port) it matched,
with Nuclei's own severity.  A template reports several distinct results
through its matchers (one per missing header), so the matcher name is part of
the title and the observation keys on title as well as template id.  A match
proves the port answered, so the port is recorded open.

A result is placed by its ``ip``; a result without one (a DNS or file
template) cannot be placed on a host and is counted as skipped, never
guessed from the name.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import VulnerabilitySource
from app.parsers.parser_utils import (
    ScanClock,
    correlate_scan,
    ensure_scan,
    map_text_severity,
    normalize_ip,
    parse_rfc3339,
    persist_host_observation,
    upsert_vulnerability,
)
from app.parsers.streaming_json import iter_json_records
from app.services.dns_name_service import ObservationCache, bind_hostname
from app.services.host_deduplication_service import HostDeduplicationService
from app.services.misconfig_checks import nuclei_header_check, record_misconfig

logger = logging.getLogger(__name__)

_DEFAULT_PORTS = {"https": 443, "http": 80}
# Template types whose target is a web endpoint: the port is an HTTP service.
_WEB_TYPES = {"http", "headless"}


def _as_list(value: Any) -> List[str]:
    """Nuclei writes several ``info`` fields as a list, a comma-separated
    string or null depending on the template."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _coerce_port(value: Any) -> Optional[int]:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _split_host_port(raw: str) -> Tuple[Optional[str], Optional[int]]:
    """``name[:port]``, ``a.b.c.d[:port]`` or ``[v6][:port]`` (a network
    template's ``host`` / ``matched-at``).  A bare IPv6 address has several
    colons and no port."""
    raw = raw.strip()
    if raw.startswith("["):
        addr, _, rest = raw[1:].partition("]")
        return addr or None, _coerce_port(rest.lstrip(":")) if rest.startswith(":") else None
    if raw.count(":") == 1:
        name, _, port = raw.partition(":")
        return name or None, _coerce_port(port)
    return raw or None, None


def _target_of(record: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
    """``(ip, hostname, port, scheme)`` of one result.

    The address is Nuclei's ``ip``; the name and port come from ``host`` /
    ``url`` / ``matched-at``, whichever carries them (``host`` is a URL for
    HTTP templates and ``name[:port]`` for network ones)."""
    ip = normalize_ip(str(record.get("ip") or "").strip() or None)
    hostname: Optional[str] = None
    port = _coerce_port(record.get("port"))
    scheme = str(record.get("scheme") or "").lower() or None

    for key in ("url", "host", "matched-at"):
        raw = str(record.get(key) or "").strip()
        if not raw:
            continue
        if "://" in raw:
            parts = urlsplit(raw)
            name = parts.hostname
            scheme = scheme or (parts.scheme or "").lower() or None
            try:
                port = port or parts.port
            except ValueError:
                pass
        else:
            name, token_port = _split_host_port(raw)
            port = port or token_port
        if name and not hostname and not normalize_ip(name):
            hostname = name.rstrip(".").lower()
        if not ip and name:
            ip = normalize_ip(name)

    if port is None and scheme in _DEFAULT_PORTS:
        port = _DEFAULT_PORTS[scheme]
    return ip, hostname, port, scheme


def _title_of(record: Dict[str, Any], info: Dict[str, Any]) -> str:
    name = str(info.get("name") or record.get("template-id") or "Nuclei result").strip()
    matcher = str(record.get("matcher-name") or "").strip()
    return f"{name}: {matcher}" if matcher else name


def _evidence_of(record: Dict[str, Any]) -> Optional[str]:
    """What the template saw on THIS host — shown as the per-host scanner
    output in the inspector."""
    lines = []
    if record.get("matched-at"):
        lines.append(f"Matched at: {record['matched-at']}")
    extracted = _as_list(record.get("extracted-results"))
    if extracted:
        lines.append("Extracted: " + ", ".join(extracted[:20]) + (" …" if len(extracted) > 20 else ""))
    if record.get("type"):
        lines.append(f"Template type: {record['type']}")
    return "\n".join(lines) or None


class NucleiParser:
    """Parser for Nuclei JSON / JSONL results."""

    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)
        self._name_cache = ObservationCache()
        self._project_id: Optional[int] = None
        self.last_parse_stats: Dict[str, Any] = {}

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        records = iter_json_records(file_path, array_keys=("results",), tool_label="Nuclei JSON")

        scan: Optional[models.Scan] = None
        clock = ScanClock(models.SCAN_TIME_TOOL_RECORDS)
        seen = recorded = 0
        hosts: set = set()
        no_address: List[str] = []
        failed: List[str] = []

        for record in records:
            if not isinstance(record, dict) or not record.get("template-id"):
                continue
            seen += 1
            ip, hostname, port, scheme = _target_of(record)
            if not ip:
                no_address.append(str(record.get("matched-at") or record.get("host") or record["template-id"]))
                continue
            if scan is None:
                scan = ensure_scan(
                    self.db, filename=filename, tool_name="nuclei",
                    scan_type="vulnerability_scan", project_id=self._project_id,
                )
            clock.observe(parse_rfc3339(record.get("timestamp")))
            # One SAVEPOINT per result: a malformed one is skipped and counted
            # instead of rolling back every result before it.
            sp = self.db.begin_nested()
            try:
                self._record(scan, record, ip, hostname, port, scheme)
                sp.commit()
            except Exception as exc:  # noqa: BLE001 — isolate one bad result
                sp.rollback()
                self._name_cache = ObservationCache()
                logger.warning("nuclei: skipping %s on %s: %s", record.get("template-id"), ip, exc)
                failed.append(f"{record.get('template-id')} on {ip} ({exc})")
                continue
            recorded += 1
            hosts.add(ip)

        if seen == 0:
            raise ValueError(f"Nuclei parser found no results in {filename}; file is empty or not Nuclei JSON.")
        if scan is None:
            raise ValueError(
                f"Nuclei results in {filename} carry no IP address, so none can be placed on a host "
                "(DNS and file templates report a name only)."
            )

        clock.apply(scan)
        self.db.commit()
        try:
            correlate_scan(self.db, scan.id)
        except Exception as exc:  # noqa: BLE001 — host data is already committed
            logger.warning("nuclei scan %s correlation failed: %s", scan.id, exc)

        skipped = len(no_address) + len(failed)
        warnings = []
        if no_address:
            warnings.append(
                f"{len(no_address)} result(s) had no IP address and were not recorded: "
                + "; ".join(no_address[:10]) + (" …" if len(no_address) > 10 else "")
            )
        if failed:
            warnings.append(
                f"{len(failed)} result(s) failed to import: "
                + "; ".join(failed[:10]) + (" …" if len(failed) > 10 else "")
            )
        self.last_parse_stats = {
            "skipped": skipped,
            "warnings": " · ".join(warnings) or None,
            "summary": (
                f"{recorded} scanner observation{'s' if recorded != 1 else ''} "
                f"on {len(hosts)} host{'s' if len(hosts) != 1 else ''}"
            ),
            "partial": bool(skipped),
        }
        return scan

    def _record(
        self, scan: models.Scan, record: Dict[str, Any], ip: str,
        hostname: Optional[str], port: Optional[int], scheme: Optional[str],
    ) -> None:
        info = record.get("info") if isinstance(record.get("info"), dict) else {}
        template_type = str(record.get("type") or "").lower()
        ports = []
        if port:
            ports.append({
                "port_number": port, "protocol": "tcp", "state": "open",
                "service_name": "http" if template_type in _WEB_TYPES or scheme in _DEFAULT_PORTS else None,
            })
        host, port_map = persist_host_observation(
            dedup_service=self.dedup_service, scan_id=scan.id, ip_address=ip,
            hostname=hostname, ports=ports, project_id=self._project_id,
        )
        persisted_port = port_map.get((port, "tcp")) if port else None
        # The template ran against a NAME (Host header / SNI) when the target
        # was one; the observation belongs to that named endpoint.
        name_id = bind_hostname(
            self.db, project_id=self._project_id, hostname=hostname,
            ip_address=ip, scan_id=scan.id, cache=self._name_cache,
        )

        # v2.414.0 — a missing-security-header matcher is a catalog check,
        # titled the same as Nikto's and testssl's report of it.
        check_id = nuclei_header_check(str(record["template-id"]), record.get("matcher-name"))
        if check_id:
            record_misconfig(
                self.db, check_id=check_id, host_id=host.id, scan_id=scan.id,
                source=VulnerabilitySource.NUCLEI,
                port_id=persisted_port.id if persisted_port else None,
                name_id=name_id, evidence=_evidence_of(record),
            )
            return

        classification = info.get("classification") if isinstance(info.get("classification"), dict) else {}
        cves = [c.upper() for c in _as_list(classification.get("cve-id")) if c.upper().startswith("CVE-")]
        references = _as_list(info.get("reference")) + cves[1:]
        try:
            cvss = float(classification.get("cvss-score")) if classification.get("cvss-score") not in (None, "") else None
        except (TypeError, ValueError):
            cvss = None

        upsert_vulnerability(
            db=self.db,
            host_id=host.id,
            scan_id=scan.id,
            source=VulnerabilitySource.NUCLEI,
            title=_title_of(record, info),
            severity=map_text_severity(str(info.get("severity") or "")),
            plugin_id=str(record["template-id"]),
            port_id=persisted_port.id if persisted_port else None,
            description=str(info.get("description") or "").strip() or None,
            solution=str(info.get("remediation") or "").strip() or None,
            cvss_score=cvss if cvss else None,
            cve_id=cves[0] if cves else None,
            references=references or None,
            name_id=name_id,
            # One template id, several results (one per matcher).
            key_on_title=True,
            plugin_output=_evidence_of(record),
        )
