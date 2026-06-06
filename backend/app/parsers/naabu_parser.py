from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import (
    correlate_scan,
    ensure_scan,
    extract_first_ip,
    parse_host_port_token,
    persist_host_observation,
)
from app.parsers.streaming_json import iter_json_records
from app.services.host_deduplication_service import HostDeduplicationService


class NaabuParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        project_id = kwargs.get("project_id")
        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name="naabu",
            scan_type="port_scan",
            project_id=project_id,
        )
        suffix = Path(filename).suffix.lower()
        hosts: Dict[str, List[dict]] = {}

        if suffix in (".json", ".jsonl"):
            # Streams large naabu exports via ijson instead of loading
            # the whole file into a 10x-bloated Python object graph.
            # ``.jsonl`` lands here too — the ingestion dispatcher
            # accepts both extensions, the iter_json_records helper
            # auto-detects JSONL via its line-by-line fallback, but the
            # parser previously gated on a strict `== ".json"` and
            # silently demoted JSONL to the text path that parses each
            # JSON line as a `host:port` token — zero hosts ingested.
            rows = iter_json_records(file_path, tool_label="Naabu JSON")
            for row in rows:
                ip_address = extract_first_ip(str(row.get("ip") or row.get("host") or row.get("url") or ""))
                port = row.get("port")
                if not ip_address or port in (None, ""):
                    continue
                hosts.setdefault(ip_address, []).append(
                    {
                        "port_number": int(port),
                        "protocol": str(row.get("protocol") or "tcp").lower(),
                        "state": "open",
                        "service_name": row.get("scheme"),
                    }
                )
        else:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    ip_address, port, scheme = parse_host_port_token(line)
                    if not ip_address or port is None:
                        continue
                    hosts.setdefault(ip_address, []).append(
                        {
                            "port_number": port,
                            "protocol": "tcp",
                            "state": "open",
                            "service_name": scheme,
                        }
                    )

        # Fail closed on 0 records — pre-v2.55.0 this returned a
        # completed `tool_name='naabu'` scan with no hosts, which
        # masked dispatch misroutes (e.g. a JSONL file silently demoted
        # to the text path) as "successful" ingests.
        if not hosts:
            raise ValueError(
                f"Naabu parser found 0 host:port records in {filename}; "
                f"file is empty or not naabu output."
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
