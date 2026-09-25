from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_confidence import NetexecResult
from app.db.models_vulnerability import VulnerabilitySource
from app.parsers.netexec_parser import writable_share
from app.services.misconfig_checks import record_misconfig
from app.parsers.parser_utils import correlate_scan, extract_first_ip, ensure_scan, persist_host_observation
from app.parsers.streaming_json import iter_json_records
from app.services.host_deduplication_service import HostDeduplicationService


# "[+] 10.0.0.5:445  Name: …" (older) and, since 1.10,
# "[+] IP: 172.30.77.10:445\tName: …\tStatus: NULL Session" (v2.387.0 — the
# "IP:" label made every real SMBMap 1.10 report fail with "0 hosts").
HOST_PATTERN = re.compile(r"^\[\+\]\s+(?:IP:\s*)?((?:\d{1,3}\.){3}\d{1,3})(?::(\d{1,5}))?", re.IGNORECASE)
STATUS_PATTERN = re.compile(r"Status:\s*([^\t\r\n]+)", re.IGNORECASE)
NAME_PATTERN = re.compile(r"Name:\s*([^\t\r\n]+)", re.IGNORECASE)


def _session(status: Optional[str]) -> Dict[str, Any]:
    """What SMBMap's Status says about authentication, in the fields the
    weak-auth condition reads: a NULL session is a blank identity, a guest
    session the guest account; "Authenticated" is a login whose account the
    report does not name."""
    s = (status or "").strip().lower()
    if not s:
        return {}
    if "null" in s:
        return {"auth_success": True, "username": ""}
    if "guest" in s:
        return {"auth_success": True, "username": "guest"}
    if "authenticated" in s:
        return {"auth_success": True}
    return {}


class SMBMapParser:
    """SMBMap text or JSON → the host, its SMB port, and (v2.390.0) its share
    table: name, permissions, comment, and the session it was read with.

    The shares were dropped — the parser recorded only "445/tcp open", the one
    thing SMBMap is not run for.  They are stored as a ``netexec_results`` row
    with ``tool='smbmap'`` beside NetExec's, which the inspector's SMB card
    already renders.
    """

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
        # ip → {port, name, status, shares}
        hosts: Dict[str, Dict[str, Any]] = {}

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
                entry = hosts.setdefault(ip_address, {"port": 445, "name": None, "status": None, "shares": []})
                entry["name"] = entry["name"] or row.get("name")
                entry["status"] = entry["status"] or row.get("status")
                for share in row.get("shares") or []:
                    if isinstance(share, dict) and share.get("name"):
                        entry["shares"].append({
                            "name": str(share["name"]),
                            "permissions": share.get("permissions") or None,
                            "remark": share.get("comment") or share.get("remark") or None,
                        })
        else:
            # Text mode splits on "\r" as well as "\n", so the progress spinner
            # ("[\] Checking for open ports...\r\r\r") becomes separate lines.
            current: Optional[Dict[str, Any]] = None
            with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    match = HOST_PATTERN.match(line.strip())
                    if match:
                        ip_address = extract_first_ip(match.group(1))
                        if not ip_address:
                            current = None
                            continue
                        current = hosts.setdefault(ip_address, {"port": 445, "name": None, "status": None, "shares": []})
                        if match.group(2):
                            current["port"] = int(match.group(2))
                        status = STATUS_PATTERN.search(line)
                        name = NAME_PATTERN.search(line)
                        current["status"] = status.group(1).strip() if status else current["status"]
                        current["name"] = name.group(1).strip() if name else current["name"]
                        continue
                    # The share table: tab-separated rows under the host line,
                    # "\tDisk\tPermissions\tComment", a rule, then one per share.
                    if current is None or not line.startswith("\t"):
                        continue
                    cells = [c.strip() for c in line.rstrip("\r\n").split("\t")[1:]]
                    if not cells or not cells[0] or cells[0].lower() == "disk" or set(cells[0]) <= {"-"}:
                        continue
                    current["shares"].append({
                        "name": cells[0],
                        "permissions": cells[1] if len(cells) > 1 and cells[1] else None,
                        "remark": cells[2] if len(cells) > 2 and cells[2] else None,
                    })

        # Fail closed on 0 records — pre-v2.55.0 this returned a
        # completed `tool_name='smbmap'` scan with no hosts, masking
        # JSONL dispatch misroutes and non-smbmap text files.
        if not hosts:
            raise ValueError(
                f"SMBMap parser found 0 hosts in {filename}; "
                f"file is empty or not smbmap output."
            )

        for ip_address, entry in hosts.items():
            persisted = persist_host_observation(
                dedup_service=self.dedup_service,
                scan_id=scan.id,
                ip_address=ip_address,
                ports=[{"port_number": entry["port"], "protocol": "tcp", "state": "open", "service_name": "smb"}],
                project_id=project_id,
            )
            host = persisted[0] if persisted else None
            if host is None:
                continue
            name = entry["name"] if entry["name"] and entry["name"] != ip_address else None
            session = _session(entry["status"])
            line = f"SMBMap {ip_address}:{entry['port']} Status: {entry['status'] or 'unknown'}"
            self.db.add(NetexecResult(
                scan_id=scan.id,
                host_id=host.id,
                tool="smbmap",
                protocol="smb",
                port=entry["port"],
                hostname=name,
                shares=entry["shares"] or None,
                writable_share=writable_share(entry["shares"]) if entry["shares"] else None,
                raw_output=line[:10000],
                **session,
            ))
            # v2.412.0 — a NULL or guest session is a catalog observation.
            if session.get("username") in ("", "guest"):
                self.db.flush()
                record_misconfig(
                    self.db, check_id="smb_null_session", host_id=host.id, scan_id=scan.id,
                    source=VulnerabilitySource.SMBMAP, port_number=entry["port"], evidence=line,
                )
        self.db.flush()

        correlate_scan(self.db, scan.id)
        return scan
