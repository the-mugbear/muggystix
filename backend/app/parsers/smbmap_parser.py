from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import correlate_scan, extract_first_ip, ensure_scan, persist_host_observation
from app.parsers.streaming_json import iter_json_records
from app.services.host_deduplication_service import HostDeduplicationService


HOST_PATTERN = re.compile(r"^\[\+\]\s+((?:\d{1,3}\.){3}\d{1,3})", re.IGNORECASE)


class SMBMapParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        project_id = kwargs.get("project_id")
        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name="smbmap",
            scan_type="smb_enumeration",
            project_id=project_id,
        )
        suffix = Path(filename).suffix.lower()
        hosts: Dict[str, List[dict]] = {}

        if suffix in (".json", ".jsonl"):
            # Both extensions land here; iter_json_records auto-detects
            # JSONL.  Strict `== ".json"` previously demoted JSONL to
            # the text-grep path and ingested zero hosts.
            rows = iter_json_records(
                file_path,
                array_keys=("hosts",),
                tool_label="SMBMap JSON",
            )
            for row in rows:
                ip_address = extract_first_ip(str(row.get("ip") or row.get("host") or ""))
                if not ip_address:
                    continue
                hosts.setdefault(ip_address, []).append(
                    {"port_number": 445, "protocol": "tcp", "state": "open", "service_name": "smb"}
                )
        else:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    match = HOST_PATTERN.match(line.strip())
                    if not match:
                        continue
                    ip_address = extract_first_ip(match.group(1))
                    if not ip_address:
                        continue
                    hosts.setdefault(ip_address, []).append(
                        {"port_number": 445, "protocol": "tcp", "state": "open", "service_name": "smb"}
                    )

        # Fail closed on 0 records — pre-v2.55.0 this returned a
        # completed `tool_name='smbmap'` scan with no hosts, masking
        # JSONL dispatch misroutes and non-smbmap text files.
        if not hosts:
            raise ValueError(
                f"SMBMap parser found 0 hosts in {filename}; "
                f"file is empty or not smbmap output."
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
