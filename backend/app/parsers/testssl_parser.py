"""Parser for testssl.sh JSON output (``--jsonfile`` / ``--jsonfile-pretty``).

testssl.sh emits a flat JSON array of finding objects, one per check::

    {"id": "SSLv3",      "ip": "web/1.2.3.4", "port": "443", "severity": "OK",   "finding": "not offered"}
    {"id": "TLS1",       "ip": "web/1.2.3.4", "port": "443", "severity": "LOW",  "finding": "offered (deprecated)"}
    {"id": "cert_notAfter", "ip": "web/1.2.3.4", "port": "443", "finding": "2025-01-01 12:00"}

We fold that per (ip, port) target into a single ``WebInterface`` row, promoting
the two things the posture surface can act on: the weak-TLS protocol flag
(``tls_weak_protocol`` — SSLv2/SSLv3/TLS1.0/1.1 offered) and certificate expiry /
self-signed state. Everything else stays in the ``raw`` blob for reference.

Mirrors the httpx parser's lifecycle (Scan row, cached host/port resolution,
HostScanHistory, correlation) so a testssl ingest contributes to recon summaries
and the encryption-&-trust systemic condition like any other web scan.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.db import models
from app.parsers.parser_utils import (
    correlate_scan,
    record_hosts_in_scan,
    resolve_host_cached,
    resolve_port_cached,
)
from app.parsers.streaming_json import iter_json_records
from app.services.cert_fields import parse_cert_not_after, _classify_tls_version

logger = logging.getLogger(__name__)

# testssl protocol-check ids → the version token _classify_tls_version reads.
_PROTOCOL_IDS = {
    "SSLv2": "sslv2", "SSLv3": "sslv3",
    "TLS1": "tls10", "TLS1_1": "tls11", "TLS1_2": "tls12", "TLS1_3": "tls13",
}


def looks_like_testssl(sample: bytes, filename: str) -> bool:
    """Content detection for testssl.sh JSON. Its findings carry the distinctive
    ``id`` + ``finding`` + ``severity`` trio (and a protocol/cert id vocabulary)
    that no other JSON probe emits — so this never cross-matches httpx (url+tech)
    or whatweb (target+plugins)."""
    if "testssl" in filename.lower():
        return True
    import json
    text = sample.decode("utf-8", errors="replace") if isinstance(sample, (bytes, bytearray)) else sample
    text = text.lstrip()
    # A JSON array of findings, or a single finding object.
    snippet = text[:20000]
    try:
        # Only need the first object; tolerate a leading '['.
        start = snippet.find("{")
        if start == -1:
            return False
        depth = 0
        end = start
        for i, ch in enumerate(snippet[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        obj = json.loads(snippet[start:end])
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(obj, dict):
        return False
    keys = set(obj)
    has_core = {"id", "finding"} <= keys and "severity" in keys
    id_val = str(obj.get("id", ""))
    testssl_id = id_val in _PROTOCOL_IDS or id_val.startswith("cert") or id_val in {
        "protocols", "engine_problem", "service", "pre_128cipher",
    }
    return has_core and (testssl_id or "id" in keys and "ip" in keys and "finding" in keys and "url" not in keys)


def _split_ip(raw_ip: str) -> Tuple[str, Optional[str]]:
    """testssl serialises the target as ``hostname/1.2.3.4`` (or a bare IP).
    Return ``(ip, hostname)``."""
    s = (raw_ip or "").strip()
    if "/" in s:
        left, _, right = s.rpartition("/")
        ip = right.strip() or left.strip()
        hostname = left.strip() if left.strip() and left.strip() != ip else None
        return ip, hostname
    return s, None


def _is_offered(finding: str) -> bool:
    f = (finding or "").lower()
    return "offered" in f and "not offered" not in f


class TestsslParser:
    """Parser for testssl.sh JSON output."""

    def __init__(self, db: Session):
        self.db = db
        self._project_id: Optional[int] = None
        self._host_cache: dict = {}
        self._port_cache: dict = {}

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        self._host_cache.clear()
        self._port_cache.clear()
        start = time.time()
        logger.info("Starting testssl parse of %s", filename)

        # Fold the flat finding array into per-(ip, port) targets.
        targets: Dict[Tuple[str, Optional[str], int], Dict[str, Any]] = {}
        record_count = 0
        for rec in iter_json_records(file_path, tool_label="testssl JSON"):
            if not isinstance(rec, dict):
                continue
            record_count += 1
            ip, hostname = _split_ip(str(rec.get("ip", "")))
            if not ip:
                continue
            try:
                port = int(str(rec.get("port") or "443").strip() or 443)
            except ValueError:
                port = 443
            key = (ip, hostname, port)
            t = targets.setdefault(key, {"weak": None, "strong_seen": False,
                                         "not_after": None, "self_signed": None, "raw": []})
            t["raw"].append(rec)
            rid = str(rec.get("id", ""))
            finding = str(rec.get("finding", ""))

            if rid in _PROTOCOL_IDS:
                cls = _classify_tls_version(_PROTOCOL_IDS[rid])
                if _is_offered(finding):
                    if cls is True:
                        t["weak"] = True
                    elif cls is False:
                        t["strong_seen"] = True
            elif rid == "cert_notAfter":
                t["not_after"] = parse_cert_not_after(finding)
            elif rid in ("cert_chain_of_trust", "cert_selfSigned", "cert_certificatePolicies_eV"):
                fl = finding.lower()
                if "self" in fl and "sign" in fl:
                    t["self_signed"] = True

        if record_count == 0:
            raise ValueError("testssl file contained no parseable findings")

        scan = models.Scan(
            filename=filename, scan_type="web_vulnerability_scan", tool_name="testssl",
            created_at=datetime.utcnow(), project_id=self._project_id,
        )
        self.db.add(scan)
        self.db.flush()

        written = 0
        host_ids_seen: set = set()
        for (ip, hostname, port), t in targets.items():
            # Per-target SAVEPOINT so a single row's integrity failure (e.g. a
            # (scan_id, url, source) collision) rolls back JUST this target
            # instead of poisoning the session — without it, the caught flush
            # error left the transaction in pending_rollback and the next flush
            # raised PendingRollbackError, aborting the whole upload. Mirrors the
            # dnsx parser / persist_host_observation isolation.
            sp = self.db.begin_nested()
            try:
                host_row = resolve_host_cached(self.db, self._project_id, ip,
                                               self._host_cache, hostname=hostname)
                if host_row is None:
                    sp.rollback()
                    continue
                port_row = resolve_port_cached(self.db, host_row, port, self._port_cache)
                # weak is True if any weak protocol offered; False if only strong
                # protocols were observed; None when protocols weren't enumerated.
                weak = t["weak"] if t["weak"] is not None else (False if t["strong_seen"] else None)
                # Key the URL by the IP tested, not the hostname: testssl probes a
                # specific IP endpoint, and a hostname resolving to several IPs
                # would otherwise collapse to one URL (and collide on the unique
                # (scan_id, url, source) constraint across its distinct hosts).
                url = f"https://{ip}:{port}"
                self.db.add(models.WebInterface(
                    scan_id=scan.id, host_id=host_row.id,
                    port_id=port_row.id if port_row else None,
                    project_id=self._project_id, source="testssl",
                    url=url, protocol="https", port=port, ip_address=ip,
                    tls_weak_protocol=weak,
                    cert_not_after=t["not_after"],
                    cert_self_signed=t["self_signed"],
                    raw={"findings": t["raw"]},
                ))
                self.db.flush()
                sp.commit()
                written += 1
                host_ids_seen.add(host_row.id)
            except Exception as exc:
                sp.rollback()
                logger.warning("testssl: skipping target %s:%s due to %s", ip, port, exc)

        record_hosts_in_scan(self.db, scan.id, host_ids_seen)
        self.db.commit()
        try:
            correlate_scan(self.db, scan.id)
        except Exception as exc:
            logger.warning("testssl scan %s correlation failed: %s", scan.id, exc)

        elapsed = time.time() - start
        logger.info("testssl %s: %d TLS interface(s) written in %.2fs", filename, written, elapsed)
        self.last_parse_stats = {
            "skipped": 0,
            "warnings": None,
            "summary": f"{written} TLS target{'s' if written != 1 else ''}",
        }
        return scan
