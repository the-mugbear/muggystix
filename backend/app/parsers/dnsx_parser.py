"""
dnsx (ProjectDiscovery) JSON / JSONL parser — v2.88.0.

Closes #44 — operators wanted "resolve this list of IPs against this
list of DNS servers, store the answers, see which resolver said
what."  Rather than build an in-app resolver (which would force
BlueStick's host to have outbound DNS, breaking restricted-network
deployments), we lean on dnsx the way the rest of the stack leans on
nmap / masscan / httpx / etc. — the operator runs the tool terminal-
side, BlueStick parses and persists the output.

Example operator invocation::

    dnsx -j -resp -l ips.txt -r resolvers.txt -ptr -a -aaaa \\
         -cname -mx -ns -txt -o dnsx-output.json

Output shape (one JSON object per line for ``-j`` mode)::

    {"host":"example.com","a":["93.184.216.34"],"resolver":["1.1.1.1:53"],
     "status_code":"NOERROR","ttl":86400,"timestamp":"..."}
    {"host":"10.0.0.5","ptr":["mail.internal"],"resolver":["8.8.8.8:53"]}
    {"host":"example.com","mx":["10 mail.example.com"],"resolver":["..."]}

For each record we walk every supported DNS record-type field present
and persist one ``DNSRecord`` row per (record_type, domain, value,
resolver_name) tuple.  PTR records additionally feed the host
inventory via ``persist_host_observation`` so a successful reverse
lookup auto-populates ``Host.hostname`` — mirroring the existing
``DNSParser._parse_csv_file`` behaviour for the PTR case.

v2.89.0 (#44.1) — resolver attribution is now first-class: the
``DNSRecord.resolver_name`` column stores the DNS server that
produced each row.  The same A record answered by 1.1.1.1 AND
8.8.8.8 now produces two rows (one per resolver) so the analytical
query "show me records resolver A returned that resolver B didn't"
is a one-line filter.  The per-ingest summary (resolver hit counts,
NXDOMAIN tally) still rides on ``last_parse_stats.warnings`` for the
IngestionJob row, but it's now derivable from the column data too.
"""
from __future__ import annotations

import ipaddress
import logging
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import (
    correlate_scan,
    ensure_scan,
    persist_host_observation,
)
from app.parsers.streaming_json import iter_json_records
from app.services.host_deduplication_service import HostDeduplicationService

logger = logging.getLogger(__name__)


# Record-type fields dnsx surfaces and the canonical record_type
# string we persist.  Keep this list and the detector in
# content_detection.looks_like_dnsx in sync.
_RECORD_TYPE_FIELDS = (
    ("a", "A"),
    ("aaaa", "AAAA"),
    ("cname", "CNAME"),
    ("mx", "MX"),
    ("ns", "NS"),
    ("txt", "TXT"),
    ("soa", "SOA"),
    # ptr handled separately so we can also update Host.hostname.
)


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _flatten_resolver(raw: Any) -> Optional[str]:
    """dnsx writes ``resolver`` as either a string ("1.1.1.1:53") or a
    list of strings; normalize to one displayable value (the first)."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list) and raw and isinstance(raw[0], str):
        return raw[0]
    return None


def _stringify_value(raw: Any) -> Optional[str]:
    """Most dnsx record fields are arrays of plain strings.  SOA is the
    odd one out — it's typically an object.  We render it as a single
    string for the ``DNSRecord.value`` column (no separate fields)."""
    if isinstance(raw, str):
        cleaned = raw.strip()
        return cleaned or None
    if isinstance(raw, dict):
        # SOA: {"name": "...", "ns": "...", "mbox": "...", ...}
        parts = [f"{k}={v}" for k, v in raw.items() if v not in (None, "")]
        return ", ".join(parts) if parts else None
    return None


class DnsxParser:
    """Parser for dnsx JSON / JSONL output."""

    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)
        self._project_id: Optional[int] = None
        # Tracked for the parser-warning summary.
        self._resolvers_seen: Counter[str] = Counter()
        self._status_codes_seen: Counter[str] = Counter()
        # last_parse_stats — surfaced by the ingestion service on the
        # IngestionJob row so the recon detail page can show "this
        # upload skipped N records" + warnings.
        self.last_parse_stats: Dict[str, Any] = {}

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        start = time.time()
        logger.info("Starting dnsx parse of %s", filename)

        scan = ensure_scan(
            self.db,
            filename=filename,
            tool_name="dnsx",
            scan_type="dns_resolution",
            project_id=self._project_id,
        )

        records_written = 0
        ptr_hosts_updated = 0
        skipped_records = 0
        # Dedup tuple — (record_type, domain, value, resolver_name).
        # v2.89.0 (#44.1): the 4-tuple keeps the same answer from two
        # different resolvers as two distinct rows (which is the whole
        # point of the resolver_name column), while still folding
        # exact duplicates from the SAME resolver into one row.  Pre-
        # #44.1 the 3-tuple collapsed multi-resolver duplicates and
        # the resolver info was lost to a parser-warning summary.
        seen: set[tuple[str, str, str, str | None]] = set()

        for row in iter_json_records(file_path, tool_label="dnsx JSON"):
            if not isinstance(row, dict):
                skipped_records += 1
                continue
            status = row.get("status_code")
            if isinstance(status, str):
                self._status_codes_seen[status] += 1
                # NOERROR is the only one with answers to persist.
                # NXDOMAIN / SERVFAIL / REFUSED carry no payload, but we
                # count them so the operator can see resolution failures
                # in the parser-warning summary.
                if status != "NOERROR" and not self._has_any_record_field(row):
                    continue
            resolver = _flatten_resolver(row.get("resolver"))
            if resolver:
                self._resolvers_seen[resolver] += 1
            host = (row.get("host") or "").strip()
            if not host:
                skipped_records += 1
                continue

            ttl = row.get("ttl") if isinstance(row.get("ttl"), int) else None

            for field_key, record_type in _RECORD_TYPE_FIELDS:
                values = row.get(field_key)
                if not isinstance(values, list):
                    continue
                for raw in values:
                    value_str = _stringify_value(raw)
                    if not value_str:
                        continue
                    if self._persist_record(
                        seen, host, record_type, value_str, ttl, resolver,
                    ):
                        records_written += 1

            ptr_values = row.get("ptr")
            if isinstance(ptr_values, list):
                # For PTR, ``host`` is the queried IP and the value is
                # the discovered hostname.  Persist the DNS record under
                # the *hostname* (matching the canonical "PTR maps
                # in-addr.arpa -> name" semantic the existing CSV
                # parser uses for value=ip / domain=hostname) AND
                # update the host inventory so a successful reverse
                # lookup populates Host.hostname.
                if not _is_valid_ip(host):
                    # Some dnsx flag combos emit ptr-of-hostname (forward
                    # lookup style); just persist as a generic PTR
                    # record with host as the domain.
                    for raw in ptr_values:
                        value_str = _stringify_value(raw)
                        if not value_str:
                            continue
                        if self._persist_record(
                            seen, host, "PTR", value_str, ttl, resolver,
                        ):
                            records_written += 1
                else:
                    for raw in ptr_values:
                        hostname = _stringify_value(raw)
                        if not hostname:
                            continue
                        if self._persist_record(
                            seen, hostname, "PTR", host, ttl, resolver,
                        ):
                            records_written += 1
                        if self._update_host_hostname(scan.id, host, hostname):
                            ptr_hosts_updated += 1

        if records_written == 0:
            raise ValueError(
                f"dnsx parser found 0 valid DNS records in {filename}; "
                f"file is empty, every record was a resolution failure, "
                f"or the file isn't dnsx -json output."
            )

        # Best-effort scope correlation for any PTR-created hosts.
        try:
            correlate_scan(self.db, scan.id)
        except Exception as exc:  # pragma: no cover — best effort
            logger.warning("dnsx: scope correlation failed for scan %s: %s", scan.id, exc)

        # Resolver / status summary lands as a parser-warning so the
        # operator can see per-resolver totals in the IngestionJob
        # row without a schema migration.
        warning_parts: List[str] = []
        if self._resolvers_seen:
            warning_parts.append(
                "Resolvers: "
                + ", ".join(f"{nm}={ct}" for nm, ct in self._resolvers_seen.most_common())
            )
        non_noerror = {
            code: ct
            for code, ct in self._status_codes_seen.items()
            if code != "NOERROR"
        }
        if non_noerror:
            warning_parts.append(
                "Resolution failures: "
                + ", ".join(f"{code}={ct}" for code, ct in non_noerror.items())
            )
        if ptr_hosts_updated:
            warning_parts.append(f"PTR populated Host.hostname for {ptr_hosts_updated} host(s)")
        warnings = " | ".join(warning_parts) if warning_parts else None

        self.last_parse_stats = {
            "skipped": skipped_records,
            "warnings": warnings,
        }

        elapsed = time.time() - start
        logger.info(
            "dnsx parse complete - filename=%s records=%d ptr_hosts=%d skipped=%d elapsed=%.2fs",
            filename, records_written, ptr_hosts_updated, skipped_records, elapsed,
        )
        return scan

    # ------------------------------------------------------------------
    def _has_any_record_field(self, row: Dict[str, Any]) -> bool:
        for field_key, _ in _RECORD_TYPE_FIELDS:
            if isinstance(row.get(field_key), list) and row[field_key]:
                return True
        if isinstance(row.get("ptr"), list) and row["ptr"]:
            return True
        return False

    def _persist_record(
        self,
        seen: set[tuple[str, str, str, Optional[str]]],
        domain: str,
        record_type: str,
        value: str,
        ttl: Optional[int],
        resolver_name: Optional[str],
    ) -> bool:
        key = (record_type, domain, value, resolver_name)
        if key in seen:
            return False
        seen.add(key)
        self.db.add(
            models.DNSRecord(
                project_id=self._project_id,
                domain=domain,
                record_type=record_type,
                value=value,
                ttl=ttl,
                resolver_name=resolver_name,
            )
        )
        return True

    def _update_host_hostname(self, scan_id: int, ip_address: str, hostname: str) -> bool:
        """Mirror DNSParser's PTR special-case: a successful reverse
        lookup populates Host.hostname when the row exists, or creates
        a new host with ``state='unknown'`` when it doesn't.
        Returns True if the inventory was touched.
        """
        existing = (
            self.db.query(models.Host)
            .filter(
                models.Host.ip_address == ip_address,
                models.Host.project_id == self._project_id,
            )
            .first()
        )
        if existing:
            if not existing.hostname or existing.hostname != hostname:
                existing.hostname = hostname
                return True
            return False
        host_data = {"hostname": hostname, "state": "unknown"}
        self.dedup_service.find_or_create_host(
            ip_address, scan_id, host_data, project_id=self._project_id,
        )
        return True
