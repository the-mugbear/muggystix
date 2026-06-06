from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import VulnerabilitySource
from app.parsers.streaming_json import iter_json_records
from app.parsers.parser_utils import (
    correlate_scan,
    ensure_scan,
    extract_first_ip,
    map_text_severity,
    persist_host_observation,
    upsert_vulnerability,
)
from app.services.host_deduplication_service import HostDeduplicationService


TARGET_IP_PATTERN = re.compile(r"Target IP:\s*((?:\d{1,3}\.){3}\d{1,3})", re.IGNORECASE)
TARGET_HOST_PATTERN = re.compile(r"Target Host(?:name)?:\s*([^\s]+)", re.IGNORECASE)
TARGET_PORT_PATTERN = re.compile(r"Target Port:\s*(\d+)", re.IGNORECASE)
FINDING_PATTERN = re.compile(r"^\+\s+(.*)")


class NiktoParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name="nikto",
            scan_type="web_vulnerability_scan",
            project_id=self._project_id,
        )
        suffix = Path(filename).suffix.lower()

        if suffix == ".json":
            self._parse_json(file_path, scan)
        elif suffix == ".csv":
            self._parse_csv(file_path, scan)
        else:
            self._parse_text(file_path, scan)

        correlate_scan(self.db, scan.id)
        return scan

    def _parse_json(self, file_path: str, scan: models.Scan) -> None:
        # Stream the records — Nikto JSON exports for large scopes are
        # dominated by per-finding ``description``/``msg`` bodies and
        # can easily reach several hundred MB.
        entries = iter_json_records(
            file_path,
            array_keys=("vulnerabilities", "findings"),
            tool_label="Nikto JSON",
        )
        for entry in entries:
            ip_address = extract_first_ip(str(entry.get("ip") or entry.get("targetip") or entry.get("host") or ""))
            if not ip_address:
                continue
            hostname = entry.get("hostname") or entry.get("host")
            port = self._coerce_port(entry.get("port")) or 80
            self._record_finding(
                scan=scan,
                ip_address=ip_address,
                hostname=hostname,
                port=port,
                title=str(entry.get("msg") or entry.get("id") or "Nikto finding"),
                description=entry.get("description") or entry.get("msg"),
                plugin_id=str(entry.get("id") or entry.get("osvdb") or "") or None,
                cve_id=entry.get("cve"),
                severity=map_text_severity(entry.get("severity")) if entry.get("severity") else map_text_severity("low"),
            )

    def _parse_csv(self, file_path: str, scan: models.Scan) -> None:
        with open(file_path, "r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                ip_address = extract_first_ip(str(row.get("ip") or row.get("targetip") or row.get("host") or ""))
                if not ip_address:
                    continue
                port = self._coerce_port(row.get("port")) or 80
                self._record_finding(
                    scan=scan,
                    ip_address=ip_address,
                    hostname=row.get("hostname") or row.get("host"),
                    port=port,
                    title=str(row.get("msg") or row.get("id") or "Nikto finding"),
                    description=row.get("description") or row.get("msg"),
                    plugin_id=row.get("id") or row.get("osvdb"),
                    cve_id=row.get("cve"),
                    severity=map_text_severity(row.get("severity")) if row.get("severity") else map_text_severity("low"),
                )

    def _parse_text(self, file_path: str, scan: models.Scan) -> None:
        current_ip: Optional[str] = None
        current_hostname: Optional[str] = None
        current_port = 80

        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                ip_match = TARGET_IP_PATTERN.search(line)
                if ip_match:
                    current_ip = extract_first_ip(ip_match.group(1))
                host_match = TARGET_HOST_PATTERN.search(line)
                if host_match:
                    current_hostname = host_match.group(1).strip()
                port_match = TARGET_PORT_PATTERN.search(line)
                if port_match:
                    current_port = int(port_match.group(1))

                finding_match = FINDING_PATTERN.match(line.strip())
                if finding_match and current_ip:
                    message = finding_match.group(1).strip()
                    if message.lower().startswith("target"):
                        continue
                    plugin_id = None
                    if ":" in message:
                        plugin_id = message.split(":", 1)[0].strip()
                    self._record_finding(
                        scan=scan,
                        ip_address=current_ip,
                        hostname=current_hostname,
                        port=current_port,
                        title=message,
                        description=message,
                        plugin_id=plugin_id,
                        cve_id=None,
                        severity=map_text_severity("low"),
                    )

    def _record_finding(
        self,
        *,
        scan: models.Scan,
        ip_address: str,
        hostname: Optional[str],
        port: int,
        title: str,
        description: Optional[str],
        plugin_id: Optional[str],
        cve_id: Optional[str],
        severity,
    ) -> None:
        host, port_map = persist_host_observation(
            dedup_service=self.dedup_service,
            scan_id=scan.id,
            ip_address=ip_address,
            hostname=hostname,
            ports=[{"port_number": port, "protocol": "tcp", "state": "open", "service_name": "http"}],
            project_id=self._project_id,
        )
        persisted_port = port_map.get((port, "tcp"))
        upsert_vulnerability(
            db=self.db,
            host_id=host.id,
            scan_id=scan.id,
            source=VulnerabilitySource.NIKTO,
            title=title,
            severity=severity,
            plugin_id=plugin_id,
            port_id=persisted_port.id if persisted_port else None,
            description=description,
            cve_id=cve_id,
        )

    def _coerce_port(self, value: object) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(str(value))
        except ValueError:
            return None
