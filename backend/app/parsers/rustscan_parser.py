from __future__ import annotations

import re
from typing import Dict, List

from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import correlate_scan, ensure_scan, extract_first_ip, persist_host_observation
from app.services.host_deduplication_service import HostDeduplicationService


OPEN_LINE_PATTERN = re.compile(r"open\s+((?:\d{1,3}\.){3}\d{1,3}):(\d+)", re.IGNORECASE)
LIST_PATTERN = re.compile(r"((?:\d{1,3}\.){3}\d{1,3}).*?\[([0-9,\s]+)\]")


class RustScanParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        project_id = kwargs.get("project_id")
        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name="rustscan",
            scan_type="port_scan",
            project_id=project_id,
        )
        hosts: Dict[str, List[dict]] = {}

        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                match = OPEN_LINE_PATTERN.search(line)
                if match:
                    ip_address = extract_first_ip(match.group(1))
                    port = int(match.group(2))
                    if ip_address:
                        hosts.setdefault(ip_address, []).append(
                            {"port_number": port, "protocol": "tcp", "state": "open"}
                        )
                    continue

                list_match = LIST_PATTERN.search(line)
                if list_match:
                    ip_address = extract_first_ip(list_match.group(1))
                    if not ip_address:
                        continue
                    for port_text in list_match.group(2).split(","):
                        port_text = port_text.strip()
                        if port_text.isdigit():
                            hosts.setdefault(ip_address, []).append(
                                {
                                    "port_number": int(port_text),
                                    "protocol": "tcp",
                                    "state": "open",
                                }
                            )

        for ip_address, ports in hosts.items():
            persist_host_observation(
                dedup_service=self.dedup_service,
                scan_id=scan.id,
                ip_address=ip_address,
                ports=ports,
                project_id=project_id,
            )

        correlate_scan(self.db, scan.id)
        return scan
