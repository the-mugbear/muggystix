from __future__ import annotations

# v2.86.11 — switched from full-tree ``DET.parse`` to streaming
# ``iterparse_safe`` + ``clear_element`` per result.  Pre-fix, large
# Greenbone/OpenVAS exports materialized every ``<result>`` node into
# memory before processing the first one — worker RSS would spike for
# the duration of the parse, and the heartbeat / progress writes the
# outer ingestion loop relies on were delayed by however long the
# parse blocked.  The streaming pattern mirrors ``nmap_parser`` and
# ``nessus_parser``, which already use this shape.
#
# The hardening flags (``resolve_entities=False`` / ``no_network=True``
# / ``huge_tree=False``) live in ``xml_stream_helpers.iterparse_safe``
# so any tampering shows up in one place — see the audit-finding C1
# comment block in that module.
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

from lxml.etree import XMLSyntaxError
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import VulnerabilitySource
from app.parsers.parser_utils import (
    ProgressBeat,
    ScanClock,
    correlate_scan,
    ensure_scan,
    extract_first_ip,
    map_numeric_severity,
    map_text_severity,
    parse_rfc3339,
    persist_host_observation,
    upsert_vulnerability,
)
from app.parsers.xml_stream_helpers import clear_element, iterparse_safe, strip_namespace
from app.services.host_deduplication_service import HostDeduplicationService

logger = logging.getLogger(__name__)


# Flush the SQLAlchemy session every N processed results so a large
# parse doesn't hold thousands of pending INSERT statements in memory
# at once.  100 is a balance: small enough that the session stays
# bounded, large enough that the per-flush overhead doesn't dominate.
_FLUSH_BATCH_SIZE = 100


_EXPLOIT_MATURITY = re.compile(r"(?:^|/)E:(POC|P|F|H|A)(?:/|$)", re.IGNORECASE)


def vector_states_exploit(vector: Optional[str]) -> bool:
    """True when a CVSS vector's exploit-maturity metric says exploit code
    exists: ``E:P`` / ``E:F`` / ``E:H`` (3.x), ``E:POC`` / ``E:F`` / ``E:H``
    (2.0), ``E:A`` / ``E:P`` (4.0) — the same bar as the Nessus path's
    ``exploit_code_maturity``.  ``E:U`` (unproven), ``E:X`` / ``E:ND`` (not
    defined) and a base-only vector state nothing."""
    return bool(vector and _EXPLOIT_MATURITY.search(vector))


def _observe_report_time(clock: ScanClock, raw: Optional[str]) -> None:
    """A GVM report's ``<scan_start>`` / ``<scan_end>`` (ISO 8601, normally
    with an offset).  A value without one is the scanner's wall clock."""
    if not raw or not raw.strip():
        return
    value = raw.strip()
    aware = parse_rfc3339(value)
    if aware is not None:
        clock.observe(aware)
        return
    try:
        clock.observe_clock(datetime.fromisoformat(value))
    except ValueError:
        return


class OpenVASParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        project_id = kwargs.get("project_id")
        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name="openvas",
            scan_type="vulnerability_scan",
            project_id=project_id,
        )

        # Counted, so a file where nothing could be recorded fails instead of
        # "succeeding" empty — which also stopped the dispatcher's fallback
        # chain on a mis-routed file (review 2026-09-23 R6; R13 of 09-21).
        seen = recorded = failed = 0
        saw_report = False
        beat = ProgressBeat("results")
        try:
            context = iterparse_safe(file_path, events=("end",))
            processed = 0
            # v2.333.0 — the report records its own run window; it used to be
            # ignored and start_time was the upload time.
            clock = ScanClock(models.SCAN_TIME_TOOL_RUN)
            for _event, elem in context:
                tag = strip_namespace(elem.tag)
                if tag in ("scan_start", "scan_end"):
                    _observe_report_time(clock, elem.text)
                    continue
                if tag == "report":
                    saw_report = True
                    continue
                if tag == "host" and elem.find("ip") is not None:
                    # The report's own per-host block (never a result's
                    # <host>, which holds the address as text).
                    sp = self.db.begin_nested()
                    try:
                        self._process_report_host(elem, scan.id, project_id)
                        sp.commit()
                    except Exception as exc:  # noqa: BLE001 — isolate one bad block
                        sp.rollback()
                        self.dedup_service.discard_rolled_back_state()
                        logger.warning("Skipping malformed OpenVAS report host: %s", exc)
                    finally:
                        clear_element(elem)
                    continue
                if tag != "result":
                    continue
                seen += 1
                # Per-result savepoint so one malformed <result> (a dedup
                # flush failure, over-long field, etc.) is skipped rather than
                # rolling back the entire upload.  Mirrors nmap/gnmap/nessus.
                sp = self.db.begin_nested()
                try:
                    if self._process_result(elem, scan.id, project_id):
                        recorded += 1
                    sp.commit()
                    processed += 1
                except Exception as exc:  # noqa: BLE001 — isolate one bad row
                    sp.rollback()
                    self.dedup_service.discard_rolled_back_state()
                    failed += 1
                    logger.warning("Skipping malformed OpenVAS result: %s", exc)
                finally:
                    # Free memory and prune predecessors so the document
                    # doesn't accumulate even after the per-result clear.
                    clear_element(elem)
                if processed and processed % _FLUSH_BATCH_SIZE == 0:
                    self.db.flush()
                # R6 — heartbeat between results, outside the result's
                # savepoint: a cancel or timeout stops the import instead of
                # being counted as a malformed result.
                beat.tick()
        except (ET.ParseError, XMLSyntaxError) as exc:
            raise ValueError(f"Invalid or truncated OpenVAS XML: {exc}") from exc

        if not saw_report and seen == 0:
            raise ValueError("Not an OpenVAS / Greenbone report: no <report> and no <result> elements.")
        if seen and not recorded:
            raise ValueError(
                f"None of the {seen} OpenVAS result(s) could be recorded "
                f"({failed} failed, {seen - failed} had no usable host address)."
            )
        unusable = seen - recorded
        self.last_parse_stats = {
            "skipped": unusable,
            "warnings": (
                f"{unusable} of {seen} OpenVAS result(s) not recorded "
                f"({failed} malformed, {unusable - failed} without a usable host address)"
                if unusable else None
            ),
            "summary": f"{recorded} result{'s' if recorded != 1 else ''}",
        }

        clock.apply(scan)
        correlate_scan(self.db, scan.id)
        return scan

    def _process_result(
        self,
        result,  # lxml.etree._Element — compatible with ET.Element API
        scan_id: int,
        project_id: Optional[int],
    ) -> bool:
        """Handle one ``<result>`` element; True when it was recorded.

        Extracted from the previous inline body so the iterparse loop
        stays tidy.  Behaviour is identical to the pre-v2.86.11
        full-tree version — same fields read, same severity mapping,
        same upsert path.
        """
        host_text = self._find_text(result, "host")
        ip_address = extract_first_ip(host_text)
        if not ip_address:
            return False
        # GMP writes the name it resolved inside the result's host element:
        # `<host>10.0.0.5<hostname>web01</hostname></host>`.
        result_hostname = self._find_text(result, "host/hostname")

        port_number, protocol = self._parse_port(self._find_text(result, "port"))
        ports = []
        if port_number:
            ports.append(
                {
                    "port_number": port_number,
                    "protocol": protocol or "tcp",
                    "state": "open",
                }
            )

        host, port_map = persist_host_observation(
            dedup_service=self.dedup_service,
            scan_id=scan_id,
            ip_address=ip_address,
            hostname=result_hostname,
            ports=ports,
            project_id=project_id,
        )

        cvss_score = self._parse_float(
            self._find_text(result, "severity")
            or self._find_text(result, ".//cvss_base")
            or self._find_text(result, ".//cvss_base_score")
        )
        severity = map_numeric_severity(cvss_score)
        if severity.value == "unknown":
            severity = map_text_severity(self._find_text(result, ".//threat"))

        port_id = None
        if port_number:
            port = port_map.get((port_number, protocol or "tcp"))
            port_id = port.id if port else None

        title = self._find_text(result, "name") or "OpenVAS finding"
        plugin_id = None
        nvt = result.find(".//nvt")
        if nvt is not None:
            plugin_id = nvt.get("oid")

        cve_value = self._find_text(result, ".//cve")
        cve_id = None if not cve_value or cve_value.lower() in {"n/a", "none"} else cve_value.split(",")[0].strip()

        # v2.390.0 — what was dropped.  The NVT's refs (URLs, and every CVE
        # past the first) go to `references`; its tags (summary / insight /
        # impact — the write-up) become the description; the result's own
        # <description> is the detection output for THIS host, so it is the
        # per-host scanner output, with the quality of detection.
        references: list = []
        extra_cves: list = []
        if nvt is not None:
            for ref in nvt.findall(".//refs/ref"):
                kind, ref_id = (ref.get("type") or "").lower(), (ref.get("id") or "").strip()
                if not ref_id:
                    continue
                if kind == "cve":
                    # Modern GMP reports carry no <cve> element: the first
                    # CVE ref IS the CVE.  Without this every one landed in
                    # the "Also:" string and cve_id stayed None, so CVE
                    # search and correlation missed every modern OpenVAS
                    # result (review 2026-09-23 C6f; R12 of 09-21).
                    if cve_id is None:
                        cve_id = ref_id.upper()
                    elif ref_id.upper() != cve_id.upper():
                        extra_cves.append(ref_id.upper())
                elif kind in ("url", "cert-bund", "dfn-cert"):
                    references.append(ref_id)
        references = [f"Also: {', '.join(extra_cves)}"] + references if extra_cves else references
        tags = dict(
            part.split("=", 1) for part in (self._find_text(result, ".//nvt/tags") or "").split("|") if "=" in part
        )
        writeup = "\n\n".join(
            f"{label}: {tags[key].strip()}" for key, label in
            (("summary", "Summary"), ("insight", "Insight"), ("impact", "Impact"), ("affected", "Affected"))
            if tags.get(key, "").strip()
        )
        cvss_vector = self._cvss_vector(nvt, tags)
        detection = self._find_text(result, "description")
        qod = self._find_text(result, ".//qod/value")
        evidence = "\n".join(p for p in (detection, f"Quality of detection: {qod}%" if qod else None) if p) or None

        upsert_vulnerability(
            db=self.db,
            host_id=host.id,
            scan_id=scan_id,
            source=VulnerabilitySource.OPENVAS,
            title=title,
            severity=severity,
            plugin_id=plugin_id,
            port_id=port_id,
            description=writeup or detection,
            cvss_score=cvss_score,
            cve_id=cve_id,
            solution=self._find_text(result, ".//solution"),
            references=references or None,
            plugin_output=evidence,
            cvss_vector=cvss_vector,
            # Only what the report states: a vector whose exploit-maturity
            # metric says exploit code exists.  Never inferred from severity,
            # and EPSS is a probability, not a statement that one exists.
            exploitable=vector_states_exploit(cvss_vector) or None,
        )
        return True

    def _cvss_vector(self, nvt, tags: dict) -> Optional[str]:
        """The NVT's CVSS vector: the ``<severities>`` block (GMP 20.08+,
        ``<severity type="cvss_base_v3"><value>CVSS:3.1/…``; a v3 entry wins
        over a v2 one), else the ``cvss_base_vector=`` tag older reports
        carry."""
        if nvt is not None:
            chosen = None
            for severity in nvt.findall(".//severities/severity"):
                value = self._find_text(severity, "value")
                if not value or "/" not in value or ":" not in value:
                    continue
                kind = (severity.get("type") or "").lower()
                if "v3" in kind or "v4" in kind or value.upper().startswith("CVSS:"):
                    return value
                chosen = chosen or value
            if chosen:
                return chosen
        tagged = (tags.get("cvss_base_vector") or "").strip()
        return tagged if "/" in tagged and ":" in tagged else None

    def _process_report_host(self, host_elem, scan_id: int, project_id: Optional[int]) -> bool:
        """A report-level ``<host>``: ``<ip>`` plus ``<detail><name>…</name>
        <value>…</value></detail>`` rows.  ``best_os_txt`` is the operating
        system GVM settled on and ``hostname`` the name it resolved; both are
        recorded on the host.  True when the host was written."""
        ip_address = extract_first_ip(self._find_text(host_elem, "ip"))
        if not ip_address:
            return False
        details: dict = {}
        for detail in host_elem.findall("detail"):
            name = self._find_text(detail, "name")
            value = self._find_text(detail, "value")
            if name and value and name not in details:
                details[name] = value
        os_name = details.get("best_os_txt")
        hostname = details.get("hostname")
        if not os_name and not hostname:
            return False
        host_data = {"os_name": os_name[:255]} if os_name else None
        persist_host_observation(
            dedup_service=self.dedup_service,
            scan_id=scan_id,
            ip_address=ip_address,
            hostname=hostname,
            host_data=host_data,
            project_id=project_id,
        )
        return True

    def _find_text(self, element, path: str) -> Optional[str]:
        child = element.find(path)
        if child is None or child.text is None:
            return None
        value = child.text.strip()
        return value or None

    def _parse_port(self, port_text: Optional[str]) -> tuple[Optional[int], Optional[str]]:
        if not port_text:
            return None, None
        parts = port_text.split("/")
        if not parts or not parts[0].isdigit():
            return None, None
        protocol = parts[1].lower() if len(parts) > 1 else "tcp"
        return int(parts[0]), protocol

    def _parse_float(self, value: Optional[str]) -> Optional[float]:
        if not value:
            return None
        try:
            return float(value.strip())
        except ValueError:
            return None
