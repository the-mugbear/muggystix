from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import correlate_scan, ensure_scan, extract_first_ip, persist_host_observation
from app.parsers.streaming_json import iter_json_records
from app.services.host_deduplication_service import HostDeduplicationService


class AmassParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        tool_name = "subfinder" if "subfinder" in filename.lower() else "amass"
        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name=tool_name,
            scan_type="subdomain_discovery",
            project_id=self._project_id,
        )

        suffix = Path(filename).suffix.lower()
        records_added = set()

        if suffix in (".json", ".jsonl"):
            # Streams large amass / subfinder exports rather than
            # materialising the entire object graph.  Accept both
            # ``.json`` and ``.jsonl`` — the dispatcher routes both
            # extensions here, and ``iter_json_records`` handles both
            # shapes; gating on strict `== ".json"` previously demoted
            # JSONL to the bare-hostname text path and dropped every
            # record's address list.
            rows = iter_json_records(file_path, tool_label="Amass JSON")
            for row in rows:
                hostname = row.get("name") or row.get("host") or row.get("domain")
                if not hostname:
                    continue
                addresses = row.get("addresses") or row.get("ips") or []
                if isinstance(addresses, list):
                    for address in addresses:
                        ip_address = extract_first_ip(str(address))
                        self._record_hostname(scan.id, records_added, hostname, ip_address)
                ip_address = extract_first_ip(str(row.get("ip") or ""))
                self._record_hostname(scan.id, records_added, hostname, ip_address)
        else:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    cleaned = line.strip()
                    if not cleaned or cleaned.startswith("#"):
                        continue
                    parts = cleaned.split()
                    hostname = parts[0]
                    ip_address = extract_first_ip(cleaned)
                    self._record_hostname(scan.id, records_added, hostname, ip_address)

        # Fail closed on 0 records — pre-v2.55.0 this returned a
        # completed `tool_name='amass'` scan with no records when the
        # JSONL gating bug demoted JSON input to the bare-hostname text
        # path or when the file simply wasn't amass output.
        if not records_added:
            raise ValueError(
                f"Amass parser found 0 hostname records in {filename}; "
                f"file is empty or not amass/subfinder output."
            )

        correlate_scan(self.db, scan.id)
        return scan

    def _record_hostname(
        self,
        scan_id: int,
        records_added: set[tuple[str, str]],
        hostname: str,
        ip_address: str | None,
    ) -> None:
        if not ip_address:
            return
        key = (hostname, ip_address)
        if key in records_added:
            return
        self.db.add(
            models.DNSRecord(
                domain=hostname,
                record_type="A",
                value=ip_address,
            )
        )
        persist_host_observation(
            dedup_service=self.dedup_service,
            scan_id=scan_id,
            ip_address=ip_address,
            hostname=hostname,
            ports=[],
            project_id=self._project_id,
            isolate=True,
        )
        records_added.add(key)
