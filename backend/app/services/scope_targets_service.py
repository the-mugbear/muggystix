"""Scope read helpers for the agent surface.

An agent reads a scope — its hosts and the open ports the inventory holds for
them — runs its own tools, and uploads the output.  These helpers turn the
inventory into the agent-facing shapes:

* ``iter_scope_hosts`` — the per-host rollup (ip, hostname, open port count,
  services, open ports) for every host in a scope, streamed in bounded
  memory so a large scope can be redirected to a file rather than read whole.
* ``web_targets_from_hosts`` — derive http/https URLs from a host's open web
  ports, so a caller does not have to walk the service strings itself.

Read-only.  Bounding a host to a scope is subnet membership
(``scope_host_ids``).  Was ``recon_summary_service`` until v2.433.1, when
recon runs were gone and the module stopped importing from the API layer: the
shapes below lived in ``agent_schemas`` only because this module used them.
"""
from __future__ import annotations

import ipaddress
from datetime import timezone
from typing import Any, Dict, Iterator, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import case, cast, func, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Session

from app.db import models
from app.services import dns_name_service as names
from app.services import web_interface_observation


class ScopePortBrief(BaseModel):
    """One open port on a host."""
    port: int
    protocol: str = "tcp"
    state: str = "open"
    service: Optional[str] = None
    product: Optional[str] = None
    version: Optional[str] = None
    #: v2.314.0 — nmap's `tunnel` attribute, "ssl" when the service runs inside
    #: TLS. `service` alone cannot say so: nmap's XML reports `ssl/http` as
    #: name="http" tunnel="ssl", so an agent reading only `service` would call
    #: an HTTPS service on a non-standard port plain HTTP.
    tunnel: Optional[str] = None
    #: How the service name was arrived at, when the tool said (nmap's
    #: ``method``): "table" is a guess from the port number, "probed" an
    #: identification.  None for tools that do not say.
    method: Optional[str] = None


class ScopeHostBrief(BaseModel):
    """One host in a scope with its open-port detail (a ``hosts.ndjson`` line)."""
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    open_port_count: int = 0
    services: List[str] = Field(default_factory=list)
    open_ports: List[ScopePortBrief] = Field(default_factory=list)


class WebTarget(BaseModel):
    """A web target derived from an open HTTP/HTTPS port."""
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    port: int
    protocol: str  # "http" | "https"
    url: str


def scope_host_ids(scope_id: int):
    """Host ids mapped into the scope through subnet correlation.

    A host is in a scope iff one of its HostSubnetMapping rows points at a
    subnet of that scope.  A ``Select`` (``Column.in_`` takes it directly).
    """
    return (
        select(models.HostSubnetMapping.host_id)
        .join(models.Subnet, models.Subnet.id == models.HostSubnetMapping.subnet_id)
        .where(models.Subnet.scope_id == scope_id)
        .distinct()
    )


def _ip_order_by(db: Session) -> List[Any]:
    """Natural IP ordering on Postgres (via inet), lexical elsewhere.

    Unparseable addresses sort last; ``id`` is the final tiebreak so paging
    and streaming are deterministic.
    """
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return [models.Host.ip_address.asc(), models.Host.id.asc()]

    parseable = func.pg_input_is_valid(models.Host.ip_address, "inet")
    return [
        parseable.desc(),
        case((parseable, cast(models.Host.ip_address, INET)), else_=None).asc(),
        models.Host.ip_address.asc(),
        models.Host.id.asc(),
    ]


def _scope_hosts_query(db: Session, scope_id: int):
    """IP-ordered ``Host`` query for every host in a scope."""
    return (
        db.query(models.Host)
        .filter(models.Host.id.in_(scope_host_ids(scope_id)))
        .order_by(*_ip_order_by(db))
    )


def scope_host_count(db: Session, scope_id: int) -> int:
    """Number of inventory hosts in the scope — constant-time in host count."""
    return (
        db.query(func.count())
        .select_from(scope_host_ids(scope_id).subquery())
        .scalar()
        or 0
    )


def _briefs_for_hosts(db: Session, host_rows: List[Any]) -> List[ScopeHostBrief]:
    """Attach open-port detail to an already-ordered page of Host rows.

    Preserves ``host_rows`` order exactly, so the caller owns ordering.
    """
    if not host_rows:
        return []

    host_ids = [h.id for h in host_rows]
    # ONE read of the open ports: the count and the service names are derived
    # from these rows (they were a count query and a DISTINCT query over the
    # same rows, three reads a chunk).  Protocol and id break the tie between
    # tcp and udp on one port number, so the file is the same on every read.
    port_rows = (
        db.query(models.Port)
        .filter(models.Port.host_id.in_(host_ids), models.Port.state == "open")
        .order_by(
            models.Port.host_id, models.Port.port_number,
            models.Port.protocol, models.Port.id,
        )
        .all()
    )
    ports_by_host: Dict[int, List[ScopePortBrief]] = {}
    services_by_host: Dict[int, set] = {}
    for p in port_rows:
        if p.service_name is not None:
            services_by_host.setdefault(p.host_id, set()).add(p.service_name)
        ports_by_host.setdefault(p.host_id, []).append(
            ScopePortBrief(
                port=p.port_number,
                protocol=p.protocol or "tcp",
                state=p.state or "open",
                service=p.service_name,
                product=p.service_product,
                version=p.service_version,
                tunnel=p.service_tunnel,
                method=p.service_method,
            )
        )

    return [
        ScopeHostBrief(
            host_id=h.id,
            ip_address=h.ip_address,
            hostname=h.hostname,
            open_port_count=len(ports_by_host.get(h.id, ())),
            services=sorted(services_by_host.get(h.id, ())),
            open_ports=ports_by_host.get(h.id, []),
        )
        for h in host_rows
    ]


def iter_scope_hosts(
    db: Session, scope_id: int, *, chunk_size: int = 500,
) -> Iterator[ScopeHostBrief]:
    """Stream every host in the scope, IP-ordered, in bounded memory.

    Only the ordered id list is held whole (8 bytes a row); host and port
    rows are fetched a chunk at a time, so peak memory is one chunk
    regardless of scope size.
    """
    host_ids = [
        row[0] for row in
        _scope_hosts_query(db, scope_id).with_entities(models.Host.id).all()
    ]
    for start in range(0, len(host_ids), chunk_size):
        page = host_ids[start:start + chunk_size]
        rows = (
            db.query(models.Host)
            .filter(models.Host.id.in_(page))
            .order_by(*_ip_order_by(db))
            .all()
        )
        for brief in _briefs_for_hosts(db, rows):
            yield brief
        for row in rows:
            db.expunge(row)


# Common HTTP/HTTPS port → scheme, the fallback used when nothing identified
# the port's service (see ``web_targets_from_hosts``).
_WEB_PORT_SCHEMES: Dict[int, str] = {
    80: "http", 8080: "http", 8000: "http", 81: "http",
    443: "https", 8443: "https", 4443: "https",
}


#: Service names that say "nothing was identified".
_UNIDENTIFIED_SERVICES = frozenset({"", "unknown"})


def _url_host(address: str) -> str:
    """An address as a URL's host: an IPv6 literal goes in brackets (RFC 3986)
    — without them ``https://2001:db8::1:8443/`` cannot be told from an
    address ending ``:8443``.  ``ip_address`` itself stays as stored."""
    if ":" in address and not address.startswith("["):
        return f"[{address}]"
    return address


def web_targets_from_hosts(hosts: List[ScopeHostBrief]) -> List[WebTarget]:
    """Derive http/https URLs from per-host open ports.

    A port is a web target when its service was named as HTTP; TLS around it
    (``tunnel``) then makes it https whatever its number.  TLS alone does not:
    imaps, ldaps and smtps are TLS-wrapped too, and were exported as https
    URLs (external review 2026-10-02 H2).

    The port table is the fallback for ports nothing identified: no service
    name, "unknown", or a name that is only the scanner's guess from the port
    number (nmap's ``method="table"`` — it calls 81 ``hosts2-ns`` and 4443
    ``pharos`` without having asked).  A service identified as something else
    (ssh on 443) is not a web target.
    """
    targets: List[WebTarget] = []
    for h in hosts:
        for p in h.open_ports:
            svc = (p.service or "").strip().lower()
            tunnel = (getattr(p, "tunnel", None) or "").lower()
            method = (getattr(p, "method", None) or "").lower()
            scheme = None
            if "http" in svc:
                scheme = "https" if tunnel == "ssl" or "https" in svc else "http"
            elif svc in _UNIDENTIFIED_SERVICES or method == "table":
                scheme = _WEB_PORT_SCHEMES.get(p.port)
            if scheme is None:
                continue
            default_port = 443 if scheme == "https" else 80
            authority = _url_host(h.ip_address)
            url = (
                f"{scheme}://{authority}/"
                if p.port == default_port
                else f"{scheme}://{authority}:{p.port}/"
            )
            targets.append(WebTarget(
                host_id=h.host_id,
                ip_address=h.ip_address,
                hostname=h.hostname,
                port=p.port,
                protocol=scheme,
                url=url,
            ))
    return targets


def iter_scope_hosts_ndjson(db: Session, scope_id: int) -> Iterator[str]:
    """One host object per line — stream to a file and process locally."""
    for brief in iter_scope_hosts(db, scope_id):
        yield brief.model_dump_json() + "\n"


def iter_scope_live_hosts(db: Session, scope_id: int) -> Iterator[str]:
    """One IP per line — an ``-iL`` target file.

    Reads the address column alone, in the scope's order: this file used to
    go through ``iter_scope_hosts`` and so loaded every host entity and every
    open port of the scope to print one column (external review 2026-10-02
    R1)."""
    addresses = _scope_hosts_query(db, scope_id).with_entities(models.Host.ip_address).all()
    for (address,) in addresses:
        if address:
            yield address + "\n"


def iter_scope_web_targets(db: Session, scope_id: int) -> Iterator[str]:
    """One URL per line — a ``-l`` / ``-f`` web target file."""
    for brief in iter_scope_hosts(db, scope_id):
        for target in web_targets_from_hosts([brief]):
            yield target.url + "\n"


# ---------------------------------------------------------------------------
# Named targets — the scope's NAME scope, one record per in-scope name
# ---------------------------------------------------------------------------
#
# The files above are subnet scope and stay that way: an IP-only file cannot
# say "only these names on that address", and widening it to the addresses
# in-scope names resolve to would authorise every other name and service on a
# shared address.  ``named-targets.ndjson`` is the separate answer: one
# record per name the scope's domain rules cover, with the addresses it
# CURRENTLY resolves to (``dns_name_service.address_state_for_names`` — the
# one "latest A/AAAA batch" rule host coverage uses), whether each address is
# ALSO in this scope's subnet scope, and the web interfaces reached AS that
# name (``WebInterface.name_id`` — the URL's host, i.e. the Host header / SNI
# the tool used).  A name is here only because a declared rule covers it:
# a certificate SAN or a co-hosted name that no rule covers never appears.


class NamedTargetRule(BaseModel):
    """The scope-domain rule that puts a name in scope (the most specific)."""
    domain: str
    include_subdomains: bool
    match: str  # "exact" | "subdomain"


class NamedTargetAddress(BaseModel):
    """An address the name currently resolves to."""
    ip_address: str
    record_type: str  # A | AAAA
    last_observed: Optional[Any] = None
    host_id: Optional[int] = None
    #: True when the address is ALSO in this scope's subnet scope.  False
    #: means the name is authorised on this address, the address is not:
    #: test the name (Host header / SNI), never the whole address.
    in_subnet_scope: bool = False


class NamedTargetWeb(BaseModel):
    """The latest observation of one URL reached as this name, per tool."""
    interface_id: int
    url: str
    scheme: Optional[str] = None
    port: Optional[int] = None
    ip_address: Optional[str] = None
    #: Whether ``ip_address`` is one the name currently resolves to — an
    #: interface captured before the name moved is history, not a target.
    at_current_address: bool = False
    source: str
    status_code: Optional[int] = None
    title: Optional[str] = None
    observed_at: Optional[Any] = None


class NamedTarget(BaseModel):
    """One ``named-targets.ndjson`` line."""
    name: str
    name_id: Optional[int] = None
    scope_rule: NamedTargetRule
    addresses: List[NamedTargetAddress] = Field(default_factory=list)
    unresolved: bool = False
    reason: Optional[str] = None
    web: List[NamedTargetWeb] = Field(default_factory=list)


def _best_rule(fqdn: str, domains: List[Any]) -> Optional[NamedTargetRule]:
    """The most specific covering rule: an exact match first, then the
    longest covering domain.  ``domain_matches`` is the Python twin of the
    SQL cover rule used to select the names."""
    best = None
    for domain, include_sub in domains:
        if not names.domain_matches(fqdn, domain, include_sub):
            continue
        key = (fqdn == domain, len(domain))
        if best is None or key > best[0]:
            best = (key, domain, bool(include_sub))
    if best is None:
        return None
    _key, domain, include_sub = best
    return NamedTargetRule(
        domain=domain, include_subdomains=include_sub,
        match="exact" if fqdn == domain else "subdomain",
    )


def _subnet_scope_for_addresses(
    db: Session, scope_id: int, host_by_ip: Dict[str, int], ips: List[str], networks_cache: Dict[str, Any],
) -> Dict[str, bool]:
    """Is each address in the scope's subnet scope?  A host row answers by its
    subnet mapping (the rule ``hosts.ndjson`` uses, so the two files agree);
    an address with no host row by CIDR containment."""
    out: Dict[str, bool] = {}
    host_ids = [host_by_ip[ip] for ip in ips if ip in host_by_ip]
    mapped = set()
    if host_ids:
        mapped = {
            hid for (hid,) in db.execute(
                scope_host_ids(scope_id).where(models.HostSubnetMapping.host_id.in_(host_ids))
            ).all()
        }
    for ip in ips:
        if ip in host_by_ip:
            out[ip] = host_by_ip[ip] in mapped
            continue
        if "networks" not in networks_cache:
            nets = []
            for (cidr,) in db.query(models.Subnet.cidr).filter(models.Subnet.scope_id == scope_id).all():
                try:
                    nets.append(ipaddress.ip_network(cidr, strict=False))
                except ValueError:
                    continue
            networks_cache["networks"] = nets
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            out[ip] = False
            continue
        out[ip] = any(addr.version == n.version and addr in n for n in networks_cache["networks"])
    return out


def _ts_key(ts: Any):
    """Sort key tolerating a naive datetime (SQLite) beside an aware one."""
    if ts is None:
        return (0, None)
    if getattr(ts, "tzinfo", None) is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (1, ts)


def _web_for_names(db: Session, project_id: int, name_ids: List[int]) -> Dict[int, List[Any]]:
    """Latest web-interface row per (name, tool, URL) — the inspector's
    latest-per-(source, url) rule (newest observation, higher id breaking a
    tie), with observation time from ``web_interface_observation`` (scan
    time, else import time).  Columns only: ``raw`` / ``page_text`` can be
    large and are not part of a target list."""
    if not name_ids:
        return {}
    w = models.WebInterface
    rows = (
        db.query(
            w.id, w.scan_id, w.first_seen, w.name_id, w.source, w.url, w.protocol,
            w.port, w.ip_address, w.status_code, w.title,
        )
        .filter(w.project_id == project_id, w.name_id.in_(name_ids))
        .all()
    )
    observed = web_interface_observation.observations(db, rows)
    latest: Dict[tuple, Any] = {}
    for r in rows:
        key = (r.name_id, r.source, r.url)
        ts = observed[r.id].observed_at
        cur = latest.get(key)
        if cur is None or (_ts_key(ts), r.id) > (_ts_key(cur[1]), cur[0].id):
            latest[key] = (r, ts)
    out: Dict[int, List[Any]] = {}
    for (name_id, _src, _url), (r, ts) in sorted(
        latest.items(), key=lambda kv: (kv[0][0], kv[0][2], kv[0][1]),
    ):
        out.setdefault(name_id, []).append((r, ts))
    return out


def iter_scope_named_targets(
    db: Session, scope: models.Scope, *, chunk_size: int = 500,
) -> Iterator[NamedTarget]:
    """Every name the scope's domain rules cover, fqdn-ordered, streamed in
    bounded memory (only the ordered name ids are held whole), then one
    record per declared domain the inventory holds no name for.

    Selection is the declared rule and nothing else: wildcard patterns
    (``kind != 'fqdn'``) and names only a certificate or a shared address
    connect to are not in scope and are not listed.
    """
    project_id = scope.project_id
    sd = models.ScopeDomain
    domains = [
        (d, bool(sub)) for d, sub in
        db.query(sd.domain, sd.include_subdomains).filter(sd.scope_id == scope.id).order_by(sd.id).all()
    ]
    if not domains:
        return

    n = models.DNSName
    covered = (
        select(sd.id)
        .where(sd.scope_id == scope.id, names.scope_domain_covers_condition(sd, n.fqdn))
        .exists()
    )
    name_ids = [
        row[0] for row in
        db.query(n.id)
        .filter(n.project_id == project_id, n.kind == "fqdn", covered)
        .order_by(n.fqdn.asc(), n.id.asc())
        .all()
    ]
    networks_cache: Dict[str, Any] = {}
    seen_fqdns = set()

    for start in range(0, len(name_ids), chunk_size):
        page = name_ids[start:start + chunk_size]
        rows = (
            db.query(n.id, n.fqdn)
            .filter(n.id.in_(page))
            .order_by(n.fqdn.asc(), n.id.asc())
            .all()
        )
        states = names.address_state_for_names(db, project_id, page)
        ips = sorted({ip for st in states.values() for ip in st.current})
        host_by_ip = names.hosts_for_addresses(db, project_id, ips)
        in_subnet = _subnet_scope_for_addresses(db, scope.id, host_by_ip, ips, networks_cache)
        web = _web_for_names(db, project_id, page)

        for nid, fqdn in rows:
            seen_fqdns.add(fqdn)
            rule = _best_rule(fqdn, domains)
            if rule is None:  # the SQL cover rule and its Python twin disagree
                continue
            state = states.get(nid)
            current = state.current if state else {}
            addresses = [
                NamedTargetAddress(
                    ip_address=ip,
                    record_type=entry["record_type"],
                    last_observed=entry["last_observed"],
                    host_id=host_by_ip.get(ip),
                    in_subnet_scope=in_subnet.get(ip, False),
                )
                for ip, entry in sorted(current.items())
            ]
            reason = None
            if not addresses:
                other = {
                    k: v for k, v in (state.evidence if state else {}).items()
                    if k not in models.DNS_RESOLVING_TYPES
                }
                reason = "no A/AAAA observation for this name"
                if other:
                    reason += " (other evidence, not a resolution: " + ", ".join(
                        f"{k}×{v}" for k, v in sorted(other.items())
                    ) + ")"
            yield NamedTarget(
                name=fqdn,
                name_id=nid,
                scope_rule=rule,
                addresses=addresses,
                unresolved=not addresses,
                reason=reason,
                web=[
                    NamedTargetWeb(
                        interface_id=r.id, url=r.url, scheme=r.protocol, port=r.port,
                        ip_address=r.ip_address,
                        at_current_address=bool(r.ip_address and r.ip_address in current),
                        source=r.source, status_code=r.status_code, title=r.title,
                        observed_at=ts,
                    )
                    for r, ts in web.get(nid, [])
                ],
            )

    # A declared domain the inventory holds no name for — listed so the
    # operator sees it, never guessed at.
    for domain, include_sub in domains:
        if domain in seen_fqdns:
            continue
        seen_fqdns.add(domain)
        reason = "declared in scope; no observation of this name in the inventory"
        if include_sub:
            reason += " (the rule also covers its subdomains; any observed are their own records)"
        yield NamedTarget(
            name=domain,
            name_id=None,
            scope_rule=NamedTargetRule(domain=domain, include_subdomains=include_sub, match="exact"),
            unresolved=True,
            reason=reason,
        )


def iter_scope_named_targets_ndjson(db: Session, scope: models.Scope) -> Iterator[str]:
    """One in-scope name per line — stream to a file and process locally."""
    for record in iter_scope_named_targets(db, scope):
        yield record.model_dump_json() + "\n"
