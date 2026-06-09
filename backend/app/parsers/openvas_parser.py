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
import xml.etree.ElementTree as ET
from typing import Optional

from lxml.etree import XMLSyntaxError
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import VulnerabilitySource
from app.parsers.parser_utils import (
    correlate_scan,
    ensure_scan,
    extract_first_ip,
    map_numeric_severity,
    map_text_severity,
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

        try:
            context = iterparse_safe(file_path, events=("end",))
            processed = 0
            for _event, elem in context:
                if strip_namespace(elem.tag) != "result":
                    continue
                # Per-result savepoint so one malformed <result> (a dedup
                # flush failure, over-long field, etc.) is skipped rather than
                # rolling back the entire upload.  Mirrors nmap/gnmap/nessus.
                sp = self.db.begin_nested()
                try:
                    self._process_result(elem, scan.id, project_id)
                    sp.commit()
                    processed += 1
                except Exception as exc:  # noqa: BLE001 — isolate one bad row
                    sp.rollback()
                    logger.warning("Skipping malformed OpenVAS result: %s", exc)
                finally:
                    # Free memory and prune predecessors so the document
                    # doesn't accumulate even after the per-result clear.
                    clear_element(elem)
                if processed and processed % _FLUSH_BATCH_SIZE == 0:
                    self.db.flush()
        except (ET.ParseError, XMLSyntaxError) as exc:
            raise ValueError(f"Invalid or truncated OpenVAS XML: {exc}") from exc

        correlate_scan(self.db, scan.id)
        return scan

    def _process_result(
        self,
        result,  # lxml.etree._Element — compatible with ET.Element API
        scan_id: int,
        project_id: Optional[int],
    ) -> None:
        """Handle one ``<result>`` element.

        Extracted from the previous inline body so the iterparse loop
        stays tidy.  Behaviour is identical to the pre-v2.86.11
        full-tree version — same fields read, same severity mapping,
        same upsert path.
        """
        host_text = self._find_text(result, "host")
        ip_address = extract_first_ip(host_text)
        if not ip_address:
            return

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

        upsert_vulnerability(
            db=self.db,
            host_id=host.id,
            scan_id=scan_id,
            source=VulnerabilitySource.OPENVAS,
            title=title,
            severity=severity,
            plugin_id=plugin_id,
            port_id=port_id,
            description=self._find_text(result, "description"),
            cvss_score=cvss_score,
            cve_id=cve_id,
            solution=self._find_text(result, ".//solution"),
        )

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
