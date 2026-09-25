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
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

import re

from app.db import models
from app.db.models_vulnerability import VulnerabilitySeverity, VulnerabilitySource
from app.parsers.parser_utils import (
    correlate_scan,
    ScanHostObservations,
    record_hosts_in_scan,
    resolve_host_cached,
    resolve_port_cached,
    upsert_vulnerability,
)
from app.parsers.streaming_json import iter_json_records
from app.services.cert_fields import parse_cert_not_after, _classify_tls_version
from app.services.dns_name_service import ObservationCache, bind_hostname
from app.services.misconfig_checks import record_misconfig

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
    # v2.387.0 — ``--jsonfile-pretty``: one document whose findings are
    # nested under "scanResult"; the first complete object is the whole
    # file, so the probe below never saw a finding.  Recognised by its
    # top-level vocabulary.
    if text.startswith("{") and '"scanResult"' in snippet and '"Invocation"' in snippet:
        return True
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


def _flatten_pretty(rec: Any):
    """``--jsonfile-pretty`` nests the findings: ``{scanResult: [{targetHost,
    ip, port, protocols: [...], serverDefaults: [...], …}]}``, each finding
    ({id, severity, finding}) without the target, which sits on the parent.
    Yield them in the flat form (``ip`` "host/1.2.3.4", ``port``) the rest of
    the parser reads.  A flat record passes through.

    v2.387.0 — the pretty file was read as one record with no ``ip``, and
    imported as "0 TLS targets", reported as a success.
    """
    if not isinstance(rec, dict) or "targetHost" not in rec and "scanResult" not in rec:
        yield rec
        return
    targets = rec.get("scanResult") if isinstance(rec.get("scanResult"), list) else [rec]
    for target in targets:
        if not isinstance(target, dict):
            continue
        host = str(target.get("targetHost") or "").strip()
        ip = str(target.get("ip") or "").strip()
        where = f"{host}/{ip}" if host and ip and host != ip else (ip or host)
        for section in target.values():
            if not isinstance(section, list):
                continue
            for finding in section:
                if isinstance(finding, dict) and "id" in finding:
                    yield {**finding, "ip": where, "port": target.get("port")}


_RATED = {
    "LOW": VulnerabilitySeverity.LOW,
    "MEDIUM": VulnerabilitySeverity.MEDIUM,
    "HIGH": VulnerabilitySeverity.HIGH,
    "CRITICAL": VulnerabilitySeverity.CRITICAL,
}

# Names for the checks an analyst meets most; any other id reads "TLS check: <id>".
_CHECK_TITLES = {
    "SSLv2": "SSLv2 offered", "SSLv3": "SSLv3 offered",
    "TLS1": "TLS 1.0 offered", "TLS1_1": "TLS 1.1 offered",
    "heartbleed": "Heartbleed", "CCS": "OpenSSL CCS injection", "ticketbleed": "Ticketbleed",
    "ROBOT": "ROBOT (Bleichenbacher oracle)", "secure_renego": "Secure renegotiation not supported",
    "secure_client_renego": "Client-initiated renegotiation", "CRIME_TLS": "CRIME (TLS compression)",
    "BREACH": "BREACH (HTTP compression)", "POODLE_SSL": "POODLE (SSLv3)", "SWEET32": "SWEET32 (64-bit block ciphers)",
    "FREAK": "FREAK (export RSA)", "DROWN": "DROWN", "LOGJAM": "LOGJAM (weak DH)", "BEAST": "BEAST",
    "LUCKY13": "LUCKY13", "RC4": "RC4 ciphers offered", "HSTS": "HSTS not set",
    "cert_chain_of_trust": "Certificate chain not trusted", "cert_expirationStatus": "Certificate expiry",
    "cert_trust": "Certificate name mismatch", "cert_signatureAlgorithm": "Weak certificate signature algorithm",
    "cert_keySize": "Weak certificate key size", "cipherlist_NULL": "NULL ciphers offered",
    "cipherlist_aNULL": "Anonymous ciphers offered", "cipherlist_EXPORT": "Export ciphers offered",
    "cipherlist_LOW": "Low-strength ciphers offered", "cipherlist_3DES_IDEA": "3DES / IDEA ciphers offered",
    "cipherlist_OBSOLETED": "Obsoleted CBC ciphers offered",
}


# Rated rows that are not a weakness of their own: the letter grade and its
# cap reasons (summaries of the checks below), and one row per cipher suite /
# preference (the cipherlist_* family rows already name the weak kinds).
_NOT_WEAKNESSES = ("overall_grade", "grade_cap", "cipher-", "cipher_order", "cipherorder_")


# v2.414.0 — rated checks that are catalog weaknesses (misconfig_checks.py).
_DEPRECATED_PROTOCOL_IDS = ("SSLv2", "SSLv3", "TLS1", "TLS1_1")
_CATALOG_IDS = {"HSTS": "http_missing_hsts", "cert_expirationStatus": "tls_cert_expired"}


def _check_title(check_id: str) -> str:
    return _CHECK_TITLES.get(check_id) or f"TLS check: {check_id}"


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
        self._observed = ScanHostObservations()
        self._port_cache: dict = {}
        self._name_cache = ObservationCache()

    def _record_observations(
        self, findings: List[Dict[str, Any]], host_id: int, scan_id: int,
        port_id: Optional[int], name_id: Optional[int],
    ) -> int:
        """One scanner observation per rated check (severity LOW or worse).
        OK / INFO / WARN are facts or client-side notes, not weaknesses."""
        count = 0
        deprecated = []
        for rec in findings:
            severity = _RATED.get(str(rec.get("severity") or "").upper())
            check_id = str(rec.get("id") or "").strip()
            if severity is None or not check_id or check_id.startswith(_NOT_WEAKNESSES):
                continue
            # v2.414.0 — weaknesses the catalog names for every tool: nmap's
            # ssl-enum-ciphers, Nikto and Nuclei report the same ones.
            if check_id in _DEPRECATED_PROTOCOL_IDS:
                deprecated.append(check_id)
                continue
            catalog = _CATALOG_IDS.get(check_id)
            if catalog == "tls_cert_expired" and "expired" not in str(rec.get("finding") or "").lower():
                catalog = None
            if catalog:
                record_misconfig(
                    self.db, check_id=catalog, host_id=host_id, scan_id=scan_id,
                    source=VulnerabilitySource.TESTSSL, port_id=port_id, name_id=name_id,
                    evidence=f"testssl {check_id}: {rec.get('finding') or ''}",
                )
                count += 1
                continue
            cves = re.findall(r"CVE-\d{4}-\d{4,}", str(rec.get("cve") or ""), re.IGNORECASE)
            upsert_vulnerability(
                db=self.db, host_id=host_id, scan_id=scan_id,
                source=VulnerabilitySource.TESTSSL,
                title=_check_title(check_id),
                severity=severity,
                plugin_id=check_id,
                port_id=port_id,
                description=str(rec.get("finding") or "") or None,
                cve_id=cves[0].upper() if cves else None,
                name_id=name_id,
            )
            count += 1
        if deprecated:
            record_misconfig(
                self.db, check_id="tls_deprecated_protocol", host_id=host_id, scan_id=scan_id,
                source=VulnerabilitySource.TESTSSL, port_id=port_id, name_id=name_id,
                evidence="testssl: offers " + ", ".join(_check_title(i).replace(" offered", "") for i in deprecated),
            )
            count += 1
        return count

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        self._host_cache.clear()
        self._port_cache.clear()
        start = time.time()
        logger.info("Starting testssl parse of %s", filename)

        # Fold the flat finding array into per-(ip, port) targets.
        targets: Dict[Tuple[str, Optional[str], int], Dict[str, Any]] = {}
        record_count = 0
        records = iter_json_records(file_path, array_keys=("scanResult",), tool_label="testssl JSON")
        for rec in (flat for target in records for flat in _flatten_pretty(target)):
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
        if not targets:
            # Findings, but none naming a target: not a result to report as
            # a clean import of nothing.
            raise ValueError("testssl file contained findings but no target address")

        scan = models.Scan(
            filename=filename, scan_type="web_vulnerability_scan", tool_name="testssl",
            project_id=self._project_id,
        )
        self.db.add(scan)
        self.db.flush()

        written = 0
        observations = 0
        # v2.332.0 — a target whose savepoint rolled back was logged and then
        # reported as a clean import ("skipped": 0).  Count it.
        skipped_targets: list = []
        for (ip, hostname, port), t in targets.items():
            # Per-target SAVEPOINT so a single row's integrity failure (e.g. a
            # (scan_id, url, source) collision) rolls back JUST this target
            # instead of poisoning the session — without it, the caught flush
            # error left the transaction in pending_rollback and the next flush
            # raised PendingRollbackError, aborting the whole upload. Mirrors the
            # dnsx parser / persist_host_observation isolation.
            # What this target adds to the per-file caches, so a rollback can
            # evict exactly that (v2.332.2).  A Host/Port row created inside a
            # savepoint that is then rolled back is gone from the database but
            # was still in the cache, so a later target for the same address
            # reused a transient object and failed on a foreign key it could
            # not satisfy.  Entries that were cached BEFORE this target are
            # untouched by the rollback and stay.
            host_was_cached = ip in self._host_cache
            port_key = None
            port_was_cached = True
            sp = self.db.begin_nested()
            try:
                resolved = resolve_host_cached(self.db, self._project_id, ip,
                                               self._host_cache, hostname=hostname)
                host_row = resolved.host
                if host_row is None:
                    sp.rollback()
                    continue
                port_key = (host_row.id, port)
                port_was_cached = port_key in self._port_cache
                port_row = resolve_port_cached(self.db, host_row, port, self._port_cache)
                # weak is True if any weak protocol offered; False if only strong
                # protocols were observed; None when protocols weren't enumerated.
                weak = t["weak"] if t["weak"] is not None else (False if t["strong_seen"] else None)
                # Key the URL by the IP tested, not the hostname: testssl probes a
                # specific IP endpoint, and a hostname resolving to several IPs
                # would otherwise collapse to one URL (and collide on the unique
                # (scan_id, url, source) constraint across its distinct hosts).
                url = f"https://[{ip}]:{port}" if ":" in ip else f"https://{ip}:{port}"
                # Phase 2 — testssl knows the NAME it probed (SNI) even though
                # the URL is keyed by IP; bind it + record the HTTP observation.
                name_id = bind_hostname(
                    self.db, project_id=self._project_id, hostname=hostname,
                    ip_address=ip, scan_id=scan.id, cache=self._name_cache,
                )
                self.db.add(models.WebInterface(
                    scan_id=scan.id, host_id=host_row.id,
                    port_id=port_row.id if port_row else None,
                    project_id=self._project_id, source="testssl",
                    url=url, protocol="https", port=port, ip_address=ip,
                    name_id=name_id,
                    tls_weak_protocol=weak,
                    cert_not_after=t["not_after"],
                    cert_self_signed=t["self_signed"],
                    raw={"findings": t["raw"]},
                ))
                self.db.flush()
                # v2.390.0 — every rated check (LOW … CRITICAL) is a scanner
                # observation on this host:port.  Only three facts were
                # promoted (weak protocol, expiry, self-signed); Heartbleed,
                # ROBOT, SWEET32, missing HSTS … lived in `raw`, unread.
                observations += self._record_observations(
                    t["raw"], host_row.id, scan.id, port_row.id if port_row else None, name_id,
                )
                sp.commit()
                written += 1
                # Only a COMMITTED target is an observation (v2.332.2).  Noting
                # before the commit let a target that then rolled back — the
                # second hostname on one ip:port collides on the URL key — put
                # its hostname on the scan history while being reported as
                # skipped.  A TLS assessment completed a handshake: up now.
                self._observed.note(
                    host_row, created=resolved.created, state="up", hostname=hostname,
                )
            except Exception as exc:
                sp.rollback()
                if not host_was_cached:
                    self._host_cache.pop(ip, None)
                if port_key is not None and not port_was_cached:
                    self._port_cache.pop(port_key, None)
                # The name cache has no per-entry provenance; a DNSName created
                # in this savepoint would be reused transient.  A fresh cache
                # costs re-queries only.
                self._name_cache = ObservationCache()
                logger.warning("testssl: skipping target %s:%s due to %s", ip, port, exc)
                skipped_targets.append(f"{ip}:{port} ({exc})")

        record_hosts_in_scan(self.db, scan.id, self._observed)
        self.db.commit()
        try:
            correlate_scan(self.db, scan.id)
        except Exception as exc:
            logger.warning("testssl scan %s correlation failed: %s", scan.id, exc)

        elapsed = time.time() - start
        logger.info("testssl %s: %d TLS interface(s) written in %.2fs", filename, written, elapsed)
        self.last_parse_stats = {
            "skipped": len(skipped_targets),
            "warnings": (
                f"{len(skipped_targets)} target(s) failed to import: "
                + "; ".join(skipped_targets[:10])
                + (" …" if len(skipped_targets) > 10 else "")
                if skipped_targets
                else None
            ),
            "summary": (
                f"{written} TLS target{'s' if written != 1 else ''}"
                + (f"; {observations} scanner observation{'s' if observations != 1 else ''}" if observations else "")
            ),
            "partial": bool(skipped_targets),
        }
        return scan
