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

from typing import Any, Dict, Iterator, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import case, cast, func, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Session

from app.db import models


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
    port_count_rows = dict(
        db.query(models.Port.host_id, func.count(models.Port.id))
        .filter(models.Port.host_id.in_(host_ids), models.Port.state == "open")
        .group_by(models.Port.host_id)
        .all()
    )
    service_rows = (
        db.query(models.Port.host_id, models.Port.service_name)
        .filter(
            models.Port.host_id.in_(host_ids),
            models.Port.state == "open",
            models.Port.service_name.isnot(None),
        )
        .distinct()
        .all()
    )
    services_by_host: Dict[int, List[str]] = {}
    for host_id, svc in service_rows:
        services_by_host.setdefault(host_id, []).append(svc)

    port_rows = (
        db.query(models.Port)
        .filter(models.Port.host_id.in_(host_ids), models.Port.state == "open")
        .order_by(models.Port.host_id, models.Port.port_number)
        .all()
    )
    ports_by_host: Dict[int, List[ScopePortBrief]] = {}
    for p in port_rows:
        ports_by_host.setdefault(p.host_id, []).append(
            ScopePortBrief(
                port=p.port_number,
                protocol=p.protocol or "tcp",
                state=p.state or "open",
                service=p.service_name,
                product=p.service_product,
                version=p.service_version,
                tunnel=p.service_tunnel,
            )
        )

    return [
        ScopeHostBrief(
            host_id=h.id,
            ip_address=h.ip_address,
            hostname=h.hostname,
            open_port_count=port_count_rows.get(h.id, 0),
            services=sorted(services_by_host.get(h.id, [])),
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


# Common HTTP/HTTPS port → scheme, the fallback used when a port carries no
# observed service name.
_WEB_PORT_SCHEMES: Dict[int, str] = {
    80: "http", 8080: "http", 8000: "http", 81: "http",
    443: "https", 8443: "https", 4443: "https",
}


def web_targets_from_hosts(hosts: List[ScopeHostBrief]) -> List[WebTarget]:
    """Derive http/https URLs from per-host open ports.

    The observed service beats the port-number guess: a port whose service
    was seen as TLS-wrapped http is https regardless of its number; the port
    table is only the fallback for ports nothing probed.
    """
    targets: List[WebTarget] = []
    for h in hosts:
        for p in h.open_ports:
            svc = (p.service or "").lower()
            tunnel = (getattr(p, "tunnel", None) or "").lower()
            scheme = None
            if svc:
                if tunnel == "ssl" or "https" in svc or "ssl/http" in svc:
                    scheme = "https"
                elif "http" in svc:
                    scheme = "http"
            if scheme is None:
                scheme = _WEB_PORT_SCHEMES.get(p.port)
            if scheme is None:
                continue
            default_port = 443 if scheme == "https" else 80
            url = (
                f"{scheme}://{h.ip_address}/"
                if p.port == default_port
                else f"{scheme}://{h.ip_address}:{p.port}/"
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
    """One IP per line — an ``-iL`` target file."""
    for brief in iter_scope_hosts(db, scope_id):
        if brief.ip_address:
            yield brief.ip_address + "\n"


def iter_scope_web_targets(db: Session, scope_id: int) -> Iterator[str]:
    """One URL per line — a ``-l`` / ``-f`` web target file."""
    for brief in iter_scope_hosts(db, scope_id):
        for target in web_targets_from_hosts([brief]):
            yield target.url + "\n"
