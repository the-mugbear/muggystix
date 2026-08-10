"""RDAP network-registration parser.

Ingests the output of ``scripts/rdap-lookup.py`` — one JSON object per line,
each the RDAP response for a netblock, wrapped with the query that produced
it. Registration data tells you who a block is *registered to*, which is what
turns "this host is in the declared scope" (a claim about a spreadsheet) into
"this host belongs to the client" (a claim about the world).

**Why a parser and not an HTTP client.** BlueStick's model is that the
operator runs a tool and uploads its output; the backend's only egress is the
narrow, SSRF-filtered set in ``url_validator``. Keeping RDAP on that side of
the line means no new egress surface, no registry rate-limiting inside the
request path, and air-gapped deployments keep working — the operator runs the
lookup wherever they have connectivity and uploads the file. It also makes
lookups replayable and auditable: the artifact is kept like any other scan.

RDAP is used rather than WHOIS deliberately. It is structured JSON over HTTPS
with an IANA bootstrap registry telling you which server owns a prefix, where
WHOIS is unstructured text over port 43 — a different egress class entirely,
for data RDAP already returns parsed.
"""
from __future__ import annotations

import ipaddress
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_attribution import (
    AttributionSource,
    HostNetworkAttribution,
    NetworkAttribution,
)

logger = logging.getLogger(__name__)


def _first(seq, default=None):
    for item in seq or []:
        return item
    return default


def _vcard_org(entity: Dict[str, Any]) -> Optional[str]:
    """Pull an organisation name out of an RDAP entity's jCard.

    jCard is a positional array format: each property is
    ``[name, params, type, value]``. The org is usually ``fn`` (formatted
    name) on a registrant/organisation entity, occasionally ``org``.
    """
    vcard = entity.get("vcardArray")
    if not isinstance(vcard, list) or len(vcard) < 2:
        return None
    props = vcard[1]
    if not isinstance(props, list):
        return None
    fn = None
    for prop in props:
        if not isinstance(prop, list) or len(prop) < 4:
            continue
        key, value = prop[0], prop[3]
        if not isinstance(value, str) or not value.strip():
            continue
        if key == "org":
            return value.strip()
        if key == "fn" and fn is None:
            fn = value.strip()
    return fn


def _org_from_entities(entities: Iterable[Dict[str, Any]]) -> Optional[str]:
    """Prefer an entity that actually claims to be the registrant/owner.

    Registries return several entities per block (registrant, abuse, technical,
    noc). Taking the first would frequently attribute a block to its abuse
    desk, so roles are ranked and only fall back to any-entity-with-a-name.
    """
    ranked = ("registrant", "owner", "administrative", "technical")
    by_role: Dict[str, str] = {}
    fallback: Optional[str] = None
    for ent in entities or []:
        if not isinstance(ent, dict):
            continue
        name = _vcard_org(ent)
        if not name:
            continue
        for role in ent.get("roles") or []:
            if isinstance(role, str) and role.lower() in ranked:
                by_role.setdefault(role.lower(), name)
        if fallback is None:
            fallback = name
    for role in ranked:
        if role in by_role:
            return by_role[role]
    return fallback


def _cidr_from_record(rec: Dict[str, Any]) -> Optional[str]:
    """Derive the CIDR this RDAP record describes.

    Prefer an explicit ``cidr0_cidrs`` block (RFC 9083 extension); fall back to
    the start/end range, which every registry returns. A range that isn't
    CIDR-aligned collapses to the supernet covering it — attribution is about
    "which registration covers this address", so a slightly wider block is the
    right answer rather than dropping the record.
    """
    cidrs = rec.get("cidr0_cidrs")
    if isinstance(cidrs, list) and cidrs:
        first = cidrs[0]
        if isinstance(first, dict):
            prefix = first.get("v4prefix") or first.get("v6prefix")
            length = first.get("length")
            if prefix and length is not None:
                return f"{prefix}/{length}"

    start, end = rec.get("startAddress"), rec.get("endAddress")
    if start and end:
        try:
            nets = list(
                ipaddress.summarize_address_range(
                    ipaddress.ip_address(str(start)), ipaddress.ip_address(str(end)),
                )
            )
            if len(nets) == 1:
                return str(nets[0])
            if nets:
                # Non-aligned range: widen to the covering supernet.
                return str(
                    ipaddress.ip_network(
                        f"{nets[0].network_address}/{min(n.prefixlen for n in nets)}",
                        strict=False,
                    )
                )
        except (ValueError, TypeError):
            return None
    handle = rec.get("handle")
    if isinstance(handle, str) and "/" in handle:
        try:
            return str(ipaddress.ip_network(handle, strict=False))
        except ValueError:
            return None
    return None


def _asn_from_record(rec: Dict[str, Any]) -> Optional[int]:
    """RDAP has no standard ASN field for IP objects, so accept the shapes the
    common tooling emits (and what our own script attaches)."""
    for key in ("asn", "autnum", "originAS"):
        value = rec.get(key)
        if value is None:
            continue
        if isinstance(value, int):
            return value
        text = str(value).upper().lstrip("AS").split(",")[0].strip()
        if text.isdigit():
            return int(text)
    return None


class RdapParser:
    """Parse RDAP NDJSON into NetworkAttribution rows."""

    def __init__(self, db: Session):
        self.db = db
        self.last_parse_stats: Optional[dict] = None

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        project_id = kwargs.get("project_id")
        scan = models.Scan(
            filename=filename,
            scan_type="rdap",
            tool_name="rdap",
            project_id=project_id,
        )
        self.db.add(scan)
        self.db.flush()

        written = 0
        skipped = 0
        warnings: list[str] = []

        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    skipped += 1
                    if len(warnings) < 5:
                        warnings.append(f"line {lineno}: invalid JSON ({exc})")
                    continue
                if not isinstance(record, dict):
                    skipped += 1
                    continue
                if self._ingest(record, project_id, scan.id):
                    written += 1
                else:
                    skipped += 1
                    if len(warnings) < 5:
                        q = record.get("query") or record.get("handle") or "?"
                        warnings.append(f"line {lineno}: no usable network range for {q}")

        self.db.commit()

        correlated = self.correlate_hosts(project_id)

        self.last_parse_stats = {
            "skipped": skipped,
            "warnings": " | ".join(warnings) if warnings else None,
            "summary": (
                f"{written} netblock{'s' if written != 1 else ''} attributed"
                f"; {correlated} host{'s' if correlated != 1 else ''} matched"
            ),
            "partial": False,
        }
        return scan

    def _ingest(self, record: Dict[str, Any], project_id: int, scan_id: int) -> bool:
        # The script wraps each response as {"query": ..., "rdap": {...}}; a raw
        # RDAP object is accepted too so a hand-saved response still ingests.
        rdap = record.get("rdap") if isinstance(record.get("rdap"), dict) else record
        cidr = _cidr_from_record(rdap)
        if not cidr:
            return False
        try:
            cidr = str(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            return False

        org = _org_from_entities(rdap.get("entities") or [])
        country = rdap.get("country")
        registry = (
            rdap.get("port43")
            or _first(rdap.get("rdapConformance") or [])
            or record.get("registry")
        )
        asn = _asn_from_record(rdap) or _asn_from_record(record)

        existing = (
            self.db.query(NetworkAttribution)
            .filter(
                NetworkAttribution.project_id == project_id,
                NetworkAttribution.cidr == cidr,
                NetworkAttribution.source == AttributionSource.RDAP,
            )
            .first()
        )
        row = existing or NetworkAttribution(
            project_id=project_id, cidr=cidr, source=AttributionSource.RDAP,
        )
        # A re-lookup should refresh, not accumulate — registration changes and
        # the newest answer is the one an operator should be shown.
        row.asn = asn
        row.as_name = (record.get("as_name") or rdap.get("name") or None)
        row.org_name = org
        row.country = (str(country)[:8] if country else None)
        row.registry = (str(registry)[:32] if registry else None)
        row.handle = (str(rdap.get("handle"))[:64] if rdap.get("handle") else None)
        row.raw = rdap
        row.looked_up_at = _parse_time(record.get("queried_at")) or datetime.now(timezone.utc)
        if existing is None:
            self.db.add(row)
        self.db.flush()
        return True

    def correlate_hosts(self, project_id: Optional[int]) -> int:
        """Attach project hosts to the blocks that cover them.

        Uses the same ``IPTrie`` the scope-subnet correlation uses — one pass
        over the project's hosts against a trie of attribution blocks, rather
        than a containment query per host.
        """
        if project_id is None:
            return 0
        from app.services.attribution_correlation import correlate_project_attributions

        return correlate_project_attributions(self.db, project_id)


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
