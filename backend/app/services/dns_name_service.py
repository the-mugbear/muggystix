"""Named assets: normalisation, observation recording, the display-name rule,
current-binding derivation, and domain-scope membership (v2.322.0; hardened
v2.323.0 after external review).

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
(``apply_hostname_candidate``), and the one place "currently resolves to" is
defined (``current_binding_condition``) — host coverage, the by-host view and
the name detail all consume that single rule, so they cannot disagree about
whether an old address still counts.

Identity (v2.323.0): a wildcard pattern keeps its ``*.`` prefix in the
canonical name, so ``*.example.com`` and ``example.com`` are two assets.

Concurrency (v2.323.0): name and observation inserts are INSERT … ON CONFLICT
DO NOTHING, so two API workers importing an overlapping list cannot fail on
the unique key — the loser reads the winner's row.

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
from sqlalchemy.orm import Session, aliased
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
WILDCARD_PREFIX = "*."


def normalize_fqdn(raw: Optional[str]) -> Tuple[str, str]:
    """Return ``(fqdn, kind)`` for ``raw``: lowercase, no trailing dot, IDN
    labels as punycode, ``kind`` in {'fqdn', 'wildcard'}.  A wildcard keeps
    its ``*.`` prefix in ``fqdn`` — the pattern and its base domain are two
    different assets and must never share a row.

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
    if s.startswith(WILDCARD_PREFIX):
        kind = "wildcard"
        s = s[len(WILDCARD_PREFIX):]
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
    if kind == "wildcard":
        fqdn = WILDCARD_PREFIX + fqdn
    if len(fqdn) > _MAX_FQDN:
        raise InvalidName("longer than 253 characters")
    return fqdn, kind


def wildcard_base(fqdn: str) -> str:
    """``*.example.com`` -> ``example.com``; a concrete name is returned as is."""
    return fqdn[len(WILDCARD_PREFIX):] if fqdn.startswith(WILDCARD_PREFIX) else fqdn


def is_wildcard_pattern(raw: Optional[str]) -> bool:
    return (raw or "").strip().startswith(WILDCARD_PREFIX)


def hostname_from_url(url: Optional[str]) -> Optional[str]:
    """The URL's hostname when it is a NAME (not an IP literal), else None."""
    if not url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    try:
        ipaddress.ip_address(host.strip("[]"))
        return None
    except ValueError:
        return host


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


def _insert_ignore(db: Session, table, values: dict):
    """INSERT … ON CONFLICT DO NOTHING for the session's dialect (Postgres in
    production, SQLite in the test fallback).  Returns the executed result;
    ``rowcount`` is 1 when the row landed, 0 when a concurrent writer won."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return db.execute(insert(table).values(**values).on_conflict_do_nothing())


def _insert_ignore_many(db: Session, table, rows: List[dict]):
    """Multi-row INSERT … ON CONFLICT DO NOTHING (one statement per call)."""
    if not rows:
        return None
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return db.execute(insert(table).values(rows).on_conflict_do_nothing())


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
    """Fetch the project's row for an already-normalised ``fqdn``, creating it
    if absent.  Race-safe: the insert is ON CONFLICT DO NOTHING, so when two
    workers create the same name at once both end up holding the one row."""
    key = (project_id, fqdn)
    if cache is not None and key in cache.names:
        return cache.names[key]
    q = db.query(models.DNSName).filter(models.DNSName.project_id == project_id, models.DNSName.fqdn == fqdn)
    row = q.first()
    if row is None:
        ts = observed_at or _now()
        result = _insert_ignore(db, models.DNSName.__table__, {
            "project_id": project_id, "fqdn": fqdn, "kind": kind,
            "first_seen": ts, "last_seen": ts, "created_by_id": created_by_id,
        })
        row = q.first()
        if row is None:  # pragma: no cover — only if the winner deleted it mid-flight
            raise RuntimeError(f"dns name {fqdn!r} vanished between insert and read")
        if result.rowcount and journal is not None:
            journal.append(("name", key))
    if cache is not None:
        cache.names[key] = row
    return row


def _touch_seen(name: models.DNSName, observed_at: Optional[datetime]) -> None:
    ts = observed_at or _now()
    if name.first_seen is None or _cmp_ts(ts, name.first_seen) < 0:
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


def observation_key(
    name_id: Optional[int], record_type: str, value: str, resolver_name: Optional[str], scan_id: Optional[int],
    exec_result_id: Optional[int] = None,
) -> Tuple:
    """The identity the DB enforces (see the partial unique indexes created in
    migrations a7d3e5f91c26 / c3f7a9d2e4b8): scan-bound rows are unique on
    (name, kind, value, resolver-or-empty, scan); imports (no scan) on
    (name, IMPORT, value); result-bound rows (TESTED) on (name, kind, value,
    exec_result).  Orphaned rows (their scan was deleted) carry no uniqueness."""
    return (name_id, record_type, value, resolver_name or "", scan_id, exec_result_id)


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
    check_exists: bool = True,
    exec_result_id: Optional[int] = None,
) -> Optional[int]:
    """Persist one observation about ``name``.  Returns the new row's id, or
    None when an identical observation (same name, kind, value, resolver, scan)
    already exists — re-ingesting the same answer is a no-op by design.

    ``name`` is the RAW string from the tool; it is normalised here and the
    raw form is kept on ``DNSRecord.domain`` for provenance.  A string that
    isn't a usable name (an IP where a name was expected, garbage) is still
    stored as a name-less legacy row so no upload loses data; it just can't
    bind to a named asset.

    ``check_exists=False`` skips the pre-insert SELECT when the caller has
    already seeded ``cache`` with the existing keys (the import path) — the
    DB's unique index still refuses a duplicate, and the insert is ON
    CONFLICT DO NOTHING so a race just reports "already there".
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
    key = observation_key(name_id, record_type, value, resolver_name, scan_id, exec_result_id)
    if cache is not None and key in cache.observations:
        return None

    if check_exists or name_id is None:
        exists_q = db.query(models.DNSRecord.id).filter(
            models.DNSRecord.record_type == record_type,
            models.DNSRecord.value == value,
            func.coalesce(models.DNSRecord.resolver_name, "") == (resolver_name or ""),
            models.DNSRecord.scan_id.is_(None) if scan_id is None
            else models.DNSRecord.scan_id == scan_id,
            models.DNSRecord.exec_result_id.is_(None) if exec_result_id is None
            else models.DNSRecord.exec_result_id == exec_result_id,
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

    ts = observed_at or _now()
    result = _insert_ignore(db, models.DNSRecord.__table__, {
        "project_id": project_id, "scan_id": scan_id, "name_id": name_id, "exec_result_id": exec_result_id,
        "domain": (name or "").strip(), "record_type": record_type, "value": value,
        "ttl": ttl, "resolver_name": resolver_name, "observed_at": ts, "created_at": ts,
    })
    if cache is not None:
        cache.observations.add(key)
    if not result.rowcount:
        return None  # a concurrent writer landed the identical observation first
    if name_row is not None:
        _touch_seen(name_row, observed_at)
    if journal is not None:
        journal.append(("obs", key))
    return result.inserted_primary_key[0] if result.inserted_primary_key else -1


def bind_url_name(
    db: Session,
    *,
    project_id: Optional[int],
    url: Optional[str],
    ip_address: Optional[str],
    scan_id: Optional[int],
    record_type: str = "HTTP",
    observed_at: Optional[datetime] = None,
    cache: Optional[ObservationCache] = None,
) -> Optional[int]:
    """Phase 2 (v2.323.0): a web tool reached ``url`` at ``ip_address``.
    Records the name→address observation (HTTP by default) and returns the
    name id for the caller to stamp on its row (web_interfaces.name_id,
    vulnerabilities.name_id).  None when the URL names an IP literal or is
    not a usable name — nothing is invented.
    """
    return bind_hostname(
        db, project_id=project_id, hostname=hostname_from_url(url), ip_address=ip_address,
        scan_id=scan_id, record_type=record_type, observed_at=observed_at, cache=cache,
    )


def bind_hostname(
    db: Session,
    *,
    project_id: Optional[int],
    hostname: Optional[str],
    ip_address: Optional[str],
    scan_id: Optional[int],
    record_type: str = "HTTP",
    observed_at: Optional[datetime] = None,
    cache: Optional[ObservationCache] = None,
) -> Optional[int]:
    """``bind_url_name`` for a bare hostname (testssl keys its URL by IP but
    knows the name it probed; nikto reports the host it scanned).  Returns
    the name id or None."""
    if project_id is None or not hostname:
        return None
    try:
        fqdn, kind = normalize_fqdn(hostname)
    except InvalidName:
        return None
    name_row = get_or_create_name(db, project_id, fqdn, kind, observed_at=observed_at, cache=cache)
    if ip_address:
        record_observation(
            db, project_id=project_id, name=hostname, record_type=record_type, value=ip_address,
            scan_id=scan_id, observed_at=observed_at, cache=cache,
        )
    return name_row.id


# --------------------------------------------------------------------------
# "Currently resolves to" — ONE rule
# --------------------------------------------------------------------------
def _obs_ts(r) -> ColumnElement:
    return func.coalesce(r.observed_at, r.created_at)


def current_binding_condition(r) -> ColumnElement:
    """Predicate on a ``DNSRecord`` (alias ``r``): this A/AAAA observation is
    part of the name's CURRENT address batch.

    The batch is the most recent scan that produced any A/AAAA for the name
    (every answer from that scan), or — for imports/orphans with no scan —
    the answers sharing the newest observation timestamp.  Everything older
    is history: still evidence, never coverage.  TTL plays no part; it is
    cache freshness, not proof of when an address stopped serving a name.

    Host coverage (``host_reachable_via_in_scope_name_condition``), the
    by-host view and ``address_state_for_names`` all use this, so an address
    a name moved away from cannot linger as "reachable" anywhere.
    """
    r2 = aliased(models.DNSRecord)
    latest = (
        select(r2.scan_id, _obs_ts(r2).label("ts"))
        .where(r2.name_id == r.name_id, r2.record_type.in_(DNS_RESOLVING_TYPES))
        .order_by(_obs_ts(r2).desc(), r2.id.desc())
        .limit(1)
        .correlate(r)
    )
    latest_scan = latest.with_only_columns(r2.scan_id).scalar_subquery()
    latest_ts = latest.with_only_columns(_obs_ts(r2)).scalar_subquery()
    return and_(
        r.record_type.in_(DNS_RESOLVING_TYPES),
        or_(
            and_(r.scan_id.isnot(None), r.scan_id == latest_scan),
            and_(latest_scan.is_(None), _obs_ts(r) == latest_ts),
        ),
    )


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
    """Predicate on ``Host`` rows: some in-scope name CURRENTLY resolves to
    this host's address (``current_binding_condition`` — a historical answer
    does not count, or an address a name moved away from would stay covered).

    This is the third coverage state — neither subnet-in-scope nor out of
    scope.  It deliberately does NOT feed host_subnet_mappings: an approved
    name resolving to a shared address does not approve the address's other
    names or services.
    """
    r = aliased(models.DNSRecord)
    n = models.DNSName
    return (
        select(r.id)
        .join(n, n.id == r.name_id)
        .where(
            r.project_id == project_id,
            r.value == models.Host.ip_address,
            current_binding_condition(r),
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
    Race-safe insert (ON CONFLICT DO NOTHING on (scope, domain)).
    Returns ``(added, updated, invalid)``."""
    existing = {d.domain: d for d in db.query(models.ScopeDomain).filter(models.ScopeDomain.scope_id == scope.id).all()}
    added = updated = 0
    invalid: List[str] = []
    for raw, include_sub, description in entries:
        try:
            fqdn, kind = normalize_fqdn(raw)
        except InvalidName as exc:
            invalid.append(f"{raw!r}: {exc}")
            continue
        domain = wildcard_base(fqdn)
        if kind == "wildcard":
            include_sub = True
        row = existing.get(domain)
        if row is None:
            result = _insert_ignore(db, models.ScopeDomain.__table__, {
                "scope_id": scope.id, "domain": domain, "include_subdomains": bool(include_sub),
                "description": (description or None), "created_by_id": created_by_id,
            })
            row = (
                db.query(models.ScopeDomain)
                .filter(models.ScopeDomain.scope_id == scope.id, models.ScopeDomain.domain == domain)
                .first()
            )
            existing[domain] = row
            if result.rowcount:
                added += 1
                continue
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


def scope_domain_name_counts(db: Session, project_id: int, domains: Sequence[models.ScopeDomain]) -> Dict[int, int]:
    """Concrete names each scope-domain entry covers, set-based: one query
    for the exact matches and one LIKE-join for the include-subdomains
    entries — never one COUNT per row."""
    if not domains:
        return {}
    n, sd = models.DNSName, models.ScopeDomain
    ids = [d.id for d in domains]
    counts: Dict[int, int] = {d.id: 0 for d in domains}
    exact = (
        db.query(sd.id, func.count(n.id))
        .join(n, and_(n.project_id == project_id, n.kind == "fqdn", n.fqdn == sd.domain))
        .filter(sd.id.in_(ids))
        .group_by(sd.id)
        .all()
    )
    for sid, cnt in exact:
        counts[sid] += int(cnt)
    sub_ids = [d.id for d in domains if d.include_subdomains]
    if sub_ids:
        sub = (
            db.query(sd.id, func.count(n.id))
            .join(n, and_(
                n.project_id == project_id, n.kind == "fqdn",
                n.fqdn.like(_escaped_like_suffix(sd.domain), escape="\\"),
            ))
            .filter(sd.id.in_(sub_ids))
            .group_by(sd.id)
            .all()
        )
        for sid, cnt in sub:
            counts[sid] += int(cnt)
    return counts


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------
MAX_IMPORT_NAMES = 50_000
_CHUNK = 1000


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

    Query budget: normalise everything first, then ONE query per 1000 names
    for the existing name rows and ONE per 1000 for the existing IMPORT
    observations; a no-change re-import of N names is ~2·N/1000 statements.
    New names cost an insert + read-back + observation insert each.
    """
    if len(raw_names) > MAX_IMPORT_NAMES:
        raise ValueError(f"at most {MAX_IMPORT_NAMES:,} names per import")
    if declare_scope and scope is None:
        raise ValueError("declare_scope requires a scope")

    # 1. Normalise + dedupe the batch.
    invalid: List[str] = []
    wanted: Dict[str, Tuple[str, str]] = {}  # fqdn -> (raw as first supplied, kind)
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
        wanted.setdefault(fqdn, (raw_s, kind))

    # 2. Seed the cache with what already exists, in bounded batches.
    cache = ObservationCache()
    fqdns = list(wanted)
    for i in range(0, len(fqdns), _CHUNK):
        chunk = fqdns[i:i + _CHUNK]
        for row in (
            db.query(models.DNSName)
            .filter(models.DNSName.project_id == project_id, models.DNSName.fqdn.in_(chunk))
            .all()
        ):
            cache.names[(project_id, row.fqdn)] = row
    existing_before = {fqdn for (_, fqdn) in cache.names}
    known_ids = [row.id for row in cache.names.values()]
    for i in range(0, len(known_ids), _CHUNK):
        chunk = known_ids[i:i + _CHUNK]
        for name_id, value in (
            db.query(models.DNSRecord.name_id, models.DNSRecord.value)
            .filter(
                models.DNSRecord.name_id.in_(chunk),
                models.DNSRecord.record_type == DNS_OBS_IMPORT,
                models.DNSRecord.scan_id.is_(None),
            )
            .all()
        ):
            cache.observations.add(observation_key(name_id, DNS_OBS_IMPORT, value, None, None))

    # 3. Bulk-create the missing names (ON CONFLICT DO NOTHING per chunk),
    #    read their ids back, then bulk-insert the IMPORT observations that
    #    aren't already known.  A first-time import of N names is therefore
    #    ~3 statements per 1000, not ~4 per name.
    now = _now()
    missing = [f for f in wanted if f not in existing_before]
    for i in range(0, len(missing), _CHUNK):
        chunk = missing[i:i + _CHUNK]
        _insert_ignore_many(db, models.DNSName.__table__, [
            {"project_id": project_id, "fqdn": f, "kind": wanted[f][1],
             "first_seen": now, "last_seen": now, "created_by_id": created_by_id}
            for f in chunk
        ])
        for row in (
            db.query(models.DNSName)
            .filter(models.DNSName.project_id == project_id, models.DNSName.fqdn.in_(chunk))
            .all()
        ):
            cache.names[(project_id, row.fqdn)] = row

    obs_rows: List[dict] = []
    created = existing = wildcards = observations = 0
    scope_entries: List[Tuple[str, bool, Optional[str]]] = []
    for fqdn, (raw_s, kind) in wanted.items():
        name_row = cache.names.get((project_id, fqdn))
        if name_row is None:  # pragma: no cover — insert + read-back above guarantees it
            continue
        key = observation_key(name_row.id, DNS_OBS_IMPORT, raw_s, None, None)
        if key not in cache.observations:
            cache.observations.add(key)
            obs_rows.append({
                "project_id": project_id, "scan_id": None, "name_id": name_row.id, "exec_result_id": None,
                "domain": raw_s, "record_type": DNS_OBS_IMPORT, "value": raw_s,
                "ttl": None, "resolver_name": None, "observed_at": now, "created_at": now,
            })
            observations += 1
            _touch_seen(name_row, now)
        if fqdn in existing_before:
            existing += 1
        else:
            created += 1
        if kind == "wildcard":
            wildcards += 1
        if declare_scope:
            scope_entries.append((fqdn, include_subdomains, None))
    for i in range(0, len(obs_rows), _CHUNK):
        _insert_ignore_many(db, models.DNSRecord.__table__, obs_rows[i:i + _CHUNK])

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
    resolving observations into CURRENT and PREVIOUS addresses — using
    ``current_binding_condition`` as the one rule, so this view can never
    disagree with host coverage."""
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
        db.query(
            r.name_id, r.value, r.record_type, _obs_ts(r), current_binding_condition(r).label("is_current"),
        )
        .filter(r.name_id.in_(list(name_ids)), r.record_type.in_(DNS_RESOLVING_TYPES))
        .all()
    )
    for nid, ip, rtype, when, is_current in rows:
        state = out[nid]
        bucket = state.current if is_current else state.previous
        entry = bucket.get(ip)
        if entry is None:
            bucket[ip] = {
                "ip_address": ip, "record_type": rtype,
                "first_observed": when, "last_observed": when, "observations": 1,
            }
        else:
            entry["observations"] += 1
            if when is not None:
                if entry["first_observed"] is None or _cmp_ts(when, entry["first_observed"]) < 0:
                    entry["first_observed"] = when
                if entry["last_observed"] is None or _cmp_ts(when, entry["last_observed"]) > 0:
                    entry["last_observed"] = when
    # An address that is current must not also be listed as previous.
    for state in out.values():
        for ip in list(state.previous):
            if ip in state.current:
                cur = state.current[ip]
                prev = state.previous.pop(ip)
                cur["observations"] += prev["observations"]
                if prev["first_observed"] is not None and (
                    cur["first_observed"] is None or _cmp_ts(prev["first_observed"], cur["first_observed"]) < 0
                ):
                    cur["first_observed"] = prev["first_observed"]
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
    """How many distinct names CURRENTLY resolve to each address — the
    shared-address (load balancer / vhost) signal."""
    ips = list({ip for ip in ips if ip})
    if not ips:
        return {}
    r = models.DNSRecord
    return {
        ip: int(cnt)
        for ip, cnt in db.query(r.value, func.count(func.distinct(r.name_id)))
        .filter(r.project_id == project_id, r.value.in_(ips), current_binding_condition(r))
        .group_by(r.value)
        .all()
    }


def resolving_exists_condition() -> ColumnElement:
    """Predicate on DNSName: at least one A/AAAA observation."""
    r = models.DNSRecord
    return select(r.id).where(r.name_id == models.DNSName.id, r.record_type.in_(DNS_RESOLVING_TYPES)).exists()


def shared_address_condition(project_id: int) -> ColumnElement:
    """Predicate on DNSName: some address it currently resolves to is also
    currently resolved to by another name in the project."""
    r1 = aliased(models.DNSRecord)
    r2 = aliased(models.DNSRecord)
    return (
        select(r1.id)
        .where(
            r1.name_id == models.DNSName.id,
            current_binding_condition(r1),
            select(r2.id).where(
                r2.project_id == project_id,
                r2.value == r1.value,
                r2.name_id != r1.name_id,
                current_binding_condition(r2),
            ).exists(),
        )
        .exists()
    )
