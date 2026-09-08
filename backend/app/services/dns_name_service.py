"""Named assets: normalisation, observation recording, the display-name rule,
and domain-scope membership (v2.322.0).

Why this module exists
----------------------
``Host`` is keyed on (project, IP).  That is right for an address and wrong
for a name: an FQDN behind NAT or a load balancer resolves to a rotating or
shared address, and forcing it into the host table meant either inventing a
placeholder IP or dropping the name.  ``DNSName`` is the name's own row;
``DNSRecord`` rows are immutable per-scan observations that link a name to
addresses and tools.  This module is the single write path for both, so every
parser normalises the same way, dedups the same way, and maintains the same
first/last-seen bookkeeping.

It is also the one place the DISPLAY hostname on ``Host`` is decided
(``apply_hostname_candidate``).  Before this, the dedup service kept the first
name and logged every later disagreement as a conflict, the dnsx parser had
its own overwrite flag, and the CSV parser overwrote unconditionally.  A load
balancer with forty vhosts produced forty spurious conflicts and one arbitrary
winner.  Now a differing name becomes an observation (a relationship), and the
display name changes only when a better-ranked source says so.

Nothing here resolves anything.  Every observation arrives from an upload.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.db import models
from app.db.models import (
    DNS_OBS_IMPORT,
    DNS_RESOLVING_TYPES,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Display-name precedence
# --------------------------------------------------------------------------
# Higher wins.  'operator' is a human correction (agent PATCH / UI); 'ptr' is
# authoritative reverse DNS; 'scanner' is what a scanner reported for the
# address (nmap user target, nessus host-fqdn, netexec SMB name, httpx host);
# 'forward' is a forward-resolved vhost name, the weakest — many names share
# one address, so import order must never let one of them win by accident.
HOSTNAME_SOURCE_RANK: Dict[str, int] = {
    "operator": 4,
    "ptr": 3,
    "scanner": 2,
    "forward": 1,
}
# Rows written before hostname_source existed came overwhelmingly from
# scanners; ranking them as such preserves the pre-existing behaviour (PTR
# could overwrite, forward could not).
LEGACY_SOURCE_RANK = HOSTNAME_SOURCE_RANK["scanner"]
HOSTNAME_SOURCES = tuple(HOSTNAME_SOURCE_RANK)


def apply_hostname_candidate(host: models.Host, candidate: Optional[str], source: str) -> bool:
    """Set ``host.hostname`` from ``candidate`` if ``source`` outranks the
    current display name's provenance.  Returns True when the name changed.

    Same-name candidates never "change" the host but may upgrade its
    recorded provenance (a scanner name later confirmed by PTR is now a PTR
    name).  An operator correction always wins, including over a previous
    operator correction — humans are allowed to change their minds.
    """
    if source not in HOSTNAME_SOURCE_RANK:
        raise ValueError(f"unknown hostname source {source!r}")
    candidate = (candidate or "").strip() or None
    if not candidate:
        return False
    rank = HOSTNAME_SOURCE_RANK[source]
    current_rank = HOSTNAME_SOURCE_RANK.get(host.hostname_source or "", LEGACY_SOURCE_RANK)

    if not host.hostname:
        host.hostname = candidate
        host.hostname_source = source
        return True
    if host.hostname == candidate:
        if rank > current_rank:
            host.hostname_source = source
        return False
    if rank > current_rank or source == "operator":
        host.hostname = candidate
        host.hostname_source = source
        return True
    return False


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
class InvalidName(ValueError):
    """The supplied string is not a usable DNS name (empty, an IP literal,
    a bad label, too long)."""


# RFC 1035 labels plus underscore — real zones carry ``_dmarc`` / ``_sip._tcp``
# and TXT/SRV owners, and a PTR can legitimately return one.
_LABEL_RE = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")
_MAX_FQDN = 253


def normalize_fqdn(raw: Optional[str]) -> Tuple[str, str]:
    """Return ``(fqdn, kind)`` for ``raw``: lowercase, no trailing dot, IDN
    labels as punycode, ``kind`` in {'fqdn', 'wildcard'}.

    Tolerates the things operators paste — a URL, a ``host:port`` — by
    taking the hostname out of them.  Rejects IP literals: an address is a
    Host, never a name.  Raises ``InvalidName`` with a reason.
    """
    s = (raw or "").strip()
    if not s:
        raise InvalidName("empty")
    if "://" in s:
        s = urlsplit(s).hostname or ""
        if not s:
            raise InvalidName("URL carries no hostname")
    s = s.strip().lower().rstrip(".")
    # host:port — but not an IPv6 literal, which has more than one colon.
    if s.count(":") == 1:
        head, _, port = s.rpartition(":")
        if port.isdigit():
            s = head
    s = s.strip("[]")
    if not s:
        raise InvalidName("empty")
    try:
        ipaddress.ip_address(s)
    except ValueError:
        pass
    else:
        raise InvalidName("is an IP address, not a name")

    kind = "fqdn"
    if s.startswith("*."):
        kind = "wildcard"
        s = s[2:]
    if "*" in s:
        raise InvalidName("wildcard only allowed as a leading '*.' label")

    labels: List[str] = []
    for label in s.split("."):
        if not label:
            raise InvalidName("empty label")
        if label.isascii():
            if not _LABEL_RE.match(label):
                raise InvalidName(f"bad label {label!r}")
            labels.append(label)
        else:
            try:
                import idna  # lazy — only IDN input pays the import

                labels.append(idna.encode(label, uts46=True).decode("ascii"))
            except Exception as exc:  # noqa: BLE001 — idna raises several types
                raise InvalidName(f"invalid internationalised label {label!r}: {exc}") from exc
    fqdn = ".".join(labels)
    if len(fqdn) > _MAX_FQDN:
        raise InvalidName("longer than 253 characters")
    return fqdn, kind


def is_wildcard_pattern(raw: Optional[str]) -> bool:
    return (raw or "").strip().startswith("*.")


# --------------------------------------------------------------------------
# Observation recording
# --------------------------------------------------------------------------
class ObservationCache:
    """Per-upload memo so a parser doesn't re-query the name row or the
    observation-exists check for every repeated answer.  ``journal`` support
    lets a parser that isolates rows in SAVEPOINTs forget what a rolled-back
    row added (the rows are gone from the DB; the cache must agree)."""

    def __init__(self) -> None:
        self.names: Dict[Tuple[int, str], models.DNSName] = {}
        self.observations: Set[Tuple] = set()

    def forget(self, journal: Iterable[Tuple[str, object]]) -> None:
        for kind, key in journal:
            if kind == "name":
                self.names.pop(key, None)  # type: ignore[arg-type]
            elif kind == "obs":
                self.observations.discard(key)  # type: ignore[arg-type]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_name(
    db: Session,
    project_id: int,
    fqdn: str,
    kind: str = "fqdn",
    *,
    observed_at: Optional[datetime] = None,
    created_by_id: Optional[int] = None,
    cache: Optional[ObservationCache] = None,
    journal: Optional[List[Tuple[str, object]]] = None,
) -> models.DNSName:
    """Fetch the project's row for an already-normalised ``fqdn``, creating
    it (and flushing, since the session runs autoflush=False) if absent."""
    key = (project_id, fqdn)
    if cache is not None and key in cache.names:
        return cache.names[key]
    row = (
        db.query(models.DNSName)
        .filter(models.DNSName.project_id == project_id, models.DNSName.fqdn == fqdn)
        .first()
    )
    if row is None:
        ts = observed_at or _now()
        row = models.DNSName(
            project_id=project_id,
            fqdn=fqdn,
            kind=kind,
            first_seen=ts,
            last_seen=ts,
            created_by_id=created_by_id,
        )
        db.add(row)
        db.flush()
        if journal is not None:
            journal.append(("name", key))
    if cache is not None:
        cache.names[key] = row
    return row


def _touch_seen(name: models.DNSName, observed_at: Optional[datetime]) -> None:
    ts = observed_at or _now()
    if name.first_seen is None or (ts is not None and _cmp_ts(ts, name.first_seen) < 0):
        name.first_seen = ts
    if name.last_seen is None or _cmp_ts(ts, name.last_seen) > 0:
        name.last_seen = ts


def _cmp_ts(a: datetime, b: datetime) -> int:
    """Compare two datetimes tolerating a naive one (SQLite returns naive)."""
    if a.tzinfo is None and b.tzinfo is not None:
        a = a.replace(tzinfo=timezone.utc)
    elif b.tzinfo is None and a.tzinfo is not None:
        b = b.replace(tzinfo=timezone.utc)
    return (a > b) - (a < b)


def record_observation(
    db: Session,
    *,
    project_id: Optional[int],
    name: str,
    record_type: str,
    value: str,
    scan_id: Optional[int] = None,
    resolver_name: Optional[str] = None,
    ttl: Optional[int] = None,
    observed_at: Optional[datetime] = None,
    created_by_id: Optional[int] = None,
    cache: Optional[ObservationCache] = None,
    journal: Optional[List[Tuple[str, object]]] = None,
) -> Optional[models.DNSRecord]:
    """Persist one observation about ``name``.  Returns the new row, or None
    when an identical observation (same name, kind, value, resolver, scan)
    already exists — re-ingesting the same answer is a no-op by design.

    ``name`` is the RAW string from the tool; it is normalised here and the
    raw form is kept on ``DNSRecord.domain`` for provenance.  A string that
    isn't a usable name (an IP where a name was expected, garbage) is still
    stored as a name-less legacy row so no upload loses data; it just can't
    bind to a named asset.
    """
    record_type = (record_type or "").strip().upper()
    value = (value or "").strip()
    if not record_type or not value:
        return None

    name_row: Optional[models.DNSName] = None
    if project_id is not None:
        try:
            fqdn, kind = normalize_fqdn(name)
        except InvalidName as exc:
            logger.debug("dns observation %s %r: not a name (%s); stored unbound", record_type, name, exc)
        else:
            name_row = get_or_create_name(
                db, project_id, fqdn, kind,
                observed_at=observed_at, created_by_id=created_by_id,
                cache=cache, journal=journal,
            )

    name_id = name_row.id if name_row is not None else None
    key = (name_id, record_type, value, resolver_name, scan_id)
    if cache is not None and key in cache.observations:
        return None

    exists_q = db.query(models.DNSRecord.id).filter(
        models.DNSRecord.record_type == record_type,
        models.DNSRecord.value == value,
        models.DNSRecord.resolver_name.is_(None) if resolver_name is None
        else models.DNSRecord.resolver_name == resolver_name,
        models.DNSRecord.scan_id.is_(None) if scan_id is None
        else models.DNSRecord.scan_id == scan_id,
    )
    if name_id is None:
        # Legacy shape: no name to key on, fall back to the raw domain string.
        exists_q = exists_q.filter(
            models.DNSRecord.name_id.is_(None),
            models.DNSRecord.domain == name,
            models.DNSRecord.project_id.is_(None) if project_id is None
            else models.DNSRecord.project_id == project_id,
        )
    else:
        exists_q = exists_q.filter(models.DNSRecord.name_id == name_id)
    if exists_q.first() is not None:
        if cache is not None:
            cache.observations.add(key)
        return None

    row = models.DNSRecord(
        project_id=project_id,
        scan_id=scan_id,
        name_id=name_id,
        domain=(name or "").strip(),
        record_type=record_type,
        value=value,
        ttl=ttl,
        resolver_name=resolver_name,
        observed_at=observed_at or _now(),
    )
    db.add(row)
    # autoflush is off — flush so an in-scan repeat finds this row (and so
    # the unique index reports a collision here, not at commit).
    db.flush()
    if name_row is not None:
        _touch_seen(name_row, observed_at)
    if cache is not None:
        cache.observations.add(key)
    if journal is not None:
        journal.append(("obs", key))
    return row


# --------------------------------------------------------------------------
# Domain scope
# --------------------------------------------------------------------------
def _escaped_like_suffix(domain_col: ColumnElement) -> ColumnElement:
    """``'%.' || domain`` with LIKE metacharacters in the domain escaped.
    After normalisation a domain cannot contain ``%``; ``_`` can occur."""
    return literal("%.").concat(func.replace(domain_col, "_", "\\_"))


def scope_domain_match_condition(project_id: int, fqdn_col: ColumnElement) -> ColumnElement:
    """SQL predicate: ``fqdn_col`` is covered by some ScopeDomain on the
    project — exactly, or as a descendant when include_subdomains is set."""
    sd = models.ScopeDomain
    return (
        select(sd.id)
        .join(models.Scope, models.Scope.id == sd.scope_id)
        .where(
            models.Scope.project_id == project_id,
            or_(
                sd.domain == fqdn_col,
                and_(
                    sd.include_subdomains.is_(True),
                    fqdn_col.like(_escaped_like_suffix(sd.domain), escape="\\"),
                ),
            ),
        )
        .exists()
    )


def name_in_scope_condition(project_id: int) -> ColumnElement:
    """Predicate on ``DNSName`` rows: the name is in domain scope.  Wildcard
    patterns never count as in scope by themselves."""
    return and_(
        models.DNSName.kind == "fqdn",
        scope_domain_match_condition(project_id, models.DNSName.fqdn),
    )


def host_reachable_via_in_scope_name_condition(project_id: int) -> ColumnElement:
    """Predicate on ``Host`` rows: some in-scope name has an A/AAAA
    observation whose value is this host's address.

    This is the third coverage state — neither subnet-in-scope nor out of
    scope.  It deliberately does NOT feed host_subnet_mappings: an approved
    name resolving to a shared address does not approve the address's other
    names or services.
    """
    r = models.DNSRecord
    n = models.DNSName
    return (
        select(r.id)
        .join(n, n.id == r.name_id)
        .where(
            r.project_id == project_id,
            r.value == models.Host.ip_address,
            r.record_type.in_(DNS_RESOLVING_TYPES),
            n.project_id == project_id,
            name_in_scope_condition(project_id),
        )
        .exists()
    )


def domain_matches(fqdn: str, domain: str, include_subdomains: bool) -> bool:
    """Python twin of scope_domain_match_condition, for single checks."""
    if fqdn == domain:
        return True
    return bool(include_subdomains) and fqdn.endswith("." + domain)


def upsert_scope_domains(
    db: Session,
    scope: models.Scope,
    entries: Iterable[Tuple[str, bool, Optional[str]]],
    *,
    created_by_id: Optional[int] = None,
) -> Tuple[int, int, List[str]]:
    """Add ``(raw_domain, include_subdomains, description)`` entries to a
    scope.  Existing rows widen (exact -> subdomains) but never narrow.
    Returns ``(added, updated, invalid)``."""
    existing = {d.domain: d for d in db.query(models.ScopeDomain).filter(models.ScopeDomain.scope_id == scope.id).all()}
    added = updated = 0
    invalid: List[str] = []
    for raw, include_sub, description in entries:
        try:
            domain, kind = normalize_fqdn(raw)
        except InvalidName as exc:
            invalid.append(f"{raw!r}: {exc}")
            continue
        if kind == "wildcard":
            include_sub = True
        row = existing.get(domain)
        if row is None:
            row = models.ScopeDomain(
                scope_id=scope.id, domain=domain, include_subdomains=bool(include_sub),
                description=(description or None), created_by_id=created_by_id,
            )
            db.add(row)
            db.flush()
            existing[domain] = row
            added += 1
        else:
            changed = False
            if include_sub and not row.include_subdomains:
                row.include_subdomains = True
                changed = True
            if description and not row.description:
                row.description = description
                changed = True
            if changed:
                updated += 1
    return added, updated, invalid


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------
MAX_IMPORT_NAMES = 50_000


def import_names(
    db: Session,
    *,
    project_id: int,
    raw_names: Sequence[str],
    created_by_id: Optional[int],
    declare_scope: bool = False,
    include_subdomains: bool = False,
    scope: Optional[models.Scope] = None,
) -> Dict[str, object]:
    """Operator-supplied FQDN list -> DNSName rows + one IMPORT observation
    each.  Idempotent: re-importing creates nothing new.  Names are NOT
    resolved and NOT turned into hosts.  ``declare_scope`` additionally adds
    each concrete name (exactly, or with descendants when
    ``include_subdomains``) and each wildcard (as its base domain with
    descendants) to ``scope`` — a separate, explicit decision.
    """
    if len(raw_names) > MAX_IMPORT_NAMES:
        raise ValueError(f"at most {MAX_IMPORT_NAMES:,} names per import")
    if declare_scope and scope is None:
        raise ValueError("declare_scope requires a scope")

    cache = ObservationCache()
    # Preload the project's names so a 50k-line re-import is one query plus
    # one existence check per line, not two round-trips per line.
    for row in db.query(models.DNSName).filter(models.DNSName.project_id == project_id).all():
        cache.names[(project_id, row.fqdn)] = row
    created = existing = wildcards = observations = 0
    invalid: List[str] = []
    seen_fqdn: Set[str] = set()
    scope_entries: List[Tuple[str, bool, Optional[str]]] = []
    now = _now()

    for raw in raw_names:
        raw_s = (raw or "").strip()
        if not raw_s or raw_s.startswith("#"):
            continue
        try:
            fqdn, kind = normalize_fqdn(raw_s)
        except InvalidName as exc:
            if len(invalid) < 50:
                invalid.append(f"{raw_s[:120]!r}: {exc}")
            continue
        if fqdn in seen_fqdn:
            continue
        seen_fqdn.add(fqdn)
        before = (project_id, fqdn) in cache.names
        row = record_observation(
            db, project_id=project_id, name=raw_s, record_type=DNS_OBS_IMPORT, value=raw_s,
            observed_at=now, created_by_id=created_by_id, cache=cache,
        )
        if row is not None:
            observations += 1
        if before:
            existing += 1
        else:
            created += 1
        if kind == "wildcard":
            wildcards += 1
        if declare_scope:
            scope_entries.append((("*." + fqdn) if kind == "wildcard" else fqdn, include_subdomains, None))

    scope_added = scope_updated = 0
    scope_invalid: List[str] = []
    if declare_scope and scope is not None and scope_entries:
        scope_added, scope_updated, scope_invalid = upsert_scope_domains(
            db, scope, scope_entries, created_by_id=created_by_id,
        )

    return {
        "names_created": created,
        "names_existing": existing,
        "wildcards": wildcards,
        "observations_recorded": observations,
        "invalid": invalid,
        "invalid_count": len(invalid),
        "scope_domains_added": scope_added,
        "scope_domains_updated": scope_updated,
        "scope_invalid": scope_invalid,
    }


# --------------------------------------------------------------------------
# Derived address state (never stored)
# --------------------------------------------------------------------------
class AddressState:
    """Per-name derived view over its A/AAAA observations."""

    __slots__ = ("current", "previous", "evidence")

    def __init__(self) -> None:
        # ip -> dict(ip_address, record_type, first_observed, last_observed, observations)
        self.current: Dict[str, dict] = {}
        self.previous: Dict[str, dict] = {}
        self.evidence: Dict[str, int] = {}


def address_state_for_names(db: Session, project_id: int, name_ids: Sequence[int]) -> Dict[int, AddressState]:
    """Compute, for each name id: evidence counts by kind, and the split of
    resolving observations into CURRENT (the latest observation batch — the
    most recent scan that produced any A/AAAA for the name, or the newest
    observation when that batch has no scan) and PREVIOUS (every other
    distinct address).  TTL plays no part: it is cache freshness, not proof
    of when an address stopped serving a name.
    """
    out: Dict[int, AddressState] = {nid: AddressState() for nid in name_ids}
    if not name_ids:
        return out
    r = models.DNSRecord

    for nid, rtype, cnt in (
        db.query(r.name_id, r.record_type, func.count(r.id))
        .filter(r.name_id.in_(list(name_ids)))
        .group_by(r.name_id, r.record_type)
        .all()
    ):
        out[nid].evidence[rtype] = int(cnt)

    rows = (
        db.query(r.name_id, r.value, r.record_type, r.scan_id, r.observed_at, r.created_at, r.id)
        .filter(r.name_id.in_(list(name_ids)), r.record_type.in_(DNS_RESOLVING_TYPES))
        .all()
    )
    by_name: Dict[int, List[tuple]] = {}
    for row in rows:
        by_name.setdefault(row[0], []).append(row)

    for nid, obs in by_name.items():
        def ts(o):  # observed_at, falling back to created_at, then id order
            return (o[4] or o[5] or datetime.min.replace(tzinfo=timezone.utc)), o[6]
        obs.sort(key=ts, reverse=True)
        latest = obs[0]
        if latest[3] is not None:
            batch = [o for o in obs if o[3] == latest[3]]
        else:
            batch = [o for o in obs if (o[4] or o[5]) == (latest[4] or latest[5])]
        current_ips = {o[1] for o in batch}
        state = out[nid]
        for o in obs:
            ip = o[1]
            bucket = state.current if ip in current_ips else state.previous
            when = o[4] or o[5]
            entry = bucket.get(ip)
            if entry is None:
                bucket[ip] = {
                    "ip_address": ip, "record_type": o[2],
                    "first_observed": when, "last_observed": when, "observations": 1,
                }
            else:
                entry["observations"] += 1
                if when is not None:
                    if entry["first_observed"] is None or _cmp_ts(when, entry["first_observed"]) < 0:
                        entry["first_observed"] = when
                    if entry["last_observed"] is None or _cmp_ts(when, entry["last_observed"]) > 0:
                        entry["last_observed"] = when
    return out


def hosts_for_addresses(db: Session, project_id: int, ips: Iterable[str]) -> Dict[str, int]:
    ips = list({ip for ip in ips if ip})
    if not ips:
        return {}
    return {
        ip: hid
        for hid, ip in db.query(models.Host.id, models.Host.ip_address)
        .filter(models.Host.project_id == project_id, models.Host.ip_address.in_(ips))
        .all()
    }


def names_per_address(db: Session, project_id: int, ips: Iterable[str]) -> Dict[str, int]:
    """How many distinct names have a resolving observation at each address —
    the shared-address (load balancer / vhost) signal."""
    ips = list({ip for ip in ips if ip})
    if not ips:
        return {}
    r = models.DNSRecord
    return {
        ip: int(cnt)
        for ip, cnt in db.query(r.value, func.count(func.distinct(r.name_id)))
        .filter(r.project_id == project_id, r.record_type.in_(DNS_RESOLVING_TYPES), r.value.in_(ips))
        .group_by(r.value)
        .all()
    }


def resolving_exists_condition() -> ColumnElement:
    """Predicate on DNSName: at least one A/AAAA observation."""
    r = models.DNSRecord
    return select(r.id).where(r.name_id == models.DNSName.id, r.record_type.in_(DNS_RESOLVING_TYPES)).exists()


def shared_address_condition(project_id: int) -> ColumnElement:
    """Predicate on DNSName: some address it resolves to is also resolved to
    by another name in the project."""
    r1 = models.DNSRecord
    from sqlalchemy.orm import aliased
    r2 = aliased(models.DNSRecord)
    return (
        select(r1.id)
        .where(
            r1.name_id == models.DNSName.id,
            r1.record_type.in_(DNS_RESOLVING_TYPES),
            select(r2.id).where(
                r2.project_id == project_id,
                r2.record_type.in_(DNS_RESOLVING_TYPES),
                r2.value == r1.value,
                r2.name_id != r1.name_id,
            ).exists(),
        )
        .exists()
    )
