"""As-scanned host snapshots (v2.299.0; a service since the 2026-10-01 review).

The Scan Detail page rendered the CURRENT Host rows of everything the scan
had ever seen — current state, current hostname, and every port the host has
today — under headings that read as a record of the scan.  So a host that was
down on the day and is up now showed as up "in" that scan, and ports found
months later appeared in it.  The counts were fixed in v2.298.0; this is the
per-host table behind them.

Deliberately a separate DTO rather than HostSchema with extra fields: the two
answer different questions, and a shared shape is how "current" leaked into
an audit view in the first place.  Note there is no OS here — os_info_updated
records only WHETHER a scan touched the OS, not what it said, so an
as-scanned OS cannot be reconstructed.  Better to omit it than to print
today's value under a historical heading.

One implementation for the page (``GET /scans/{id}/host-snapshots``) and the
agent read (``GET /agent/assist/scans/{id}/hosts``).
"""
import json
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.db import models
from app.schemas.pagination import Paginated
from app.services.host_query_common import escape_like
from app.services.scan_diff_service import ScanNotInProject


def _service_name_at_scan(service_info: Optional[str]) -> Optional[str]:
    """The service name THIS scan recorded for a port, from the
    ``PortScanHistory.service_info`` JSON the dedup path writes per (port,
    scan).  NULL means the scan recorded no service — reported as none, never
    filled from the live Port row (that would leak a later scan's value into
    an as-scanned view)."""
    if not service_info:
        return None
    try:
        data = json.loads(service_info)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("service_name")
    return name if isinstance(name, str) and name else None


class ScanPortSnapshot(BaseModel):
    port_number: int
    protocol: Optional[str] = None
    #: State THIS scan observed. The port's current state may differ.
    state_at_scan: Optional[str] = None
    service_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ScanHostSnapshot(BaseModel):
    host_id: int
    #: Addresses are stable, so this is both the as-scanned and current value.
    ip_address: str
    hostname_at_scan: Optional[str] = None
    state_at_scan: Optional[str] = None
    #: True when this scan is the one that first discovered the host.
    host_created: bool = False
    #: True when this scan changed the display name of a host that already
    #: existed (``host_query_predicates.renamed_in_scan_condition`` — the rows
    #: the scan's ``renamed_hosts`` counts).  ``hostname_at_scan`` is the name
    #: the scan reported, which is not always the name it set.
    hostname_changed: bool = Field(
        False,
        description=(
            "Whether this scan changed the host's display name (a host it created is "
            "not one). The scan's renamed_hosts counts these rows. False for every "
            "scan imported before the fact was recorded."
        ),
    )
    #: Did this scan authenticate to the host (``host_scan_history.credentialed``)?
    #: True / False when the scanner said so (Nessus); null when it did not, and
    #: for every tool with no such notion.  Null is "not stated", never "no".
    credentialed: Optional[bool] = Field(
        None,
        description=(
            "Whether this scan authenticated to the host: true / false when the "
            "scanner said so (Nessus), null when it did not say."
        ),
    )
    observed_port_count: int = 0
    open_port_count: int = 0
    ports: List[ScanPortSnapshot] = Field(default_factory=list)


#: Ports listed per host row. Beyond this the row reports counts only — a scan
#: of a host with 65k open ports must not produce a 65k-element row.
_SNAPSHOT_PORT_CAP = 50


def scan_host_snapshots(
    db: Session,
    project_id: int,
    scan_id: int,
    *,
    state: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
) -> Paginated[ScanHostSnapshot]:
    """What this scan recorded, as it recorded it.

    Every field is read from the observation tables (``HostScanHistory``,
    ``PortScanHistory``), so the response is stable: re-running it after later
    scans, or after a port is remediated, returns the same thing.  Raises
    ``ScanNotInProject`` when the scan is not one of this project's, and a 422
    naming a ``state`` that is not a host state — for both doors (the scan
    page and ``/agent/assist/scans/{id}/hosts``): ``alive`` read as "the scan
    saw no host up" (review 2026-10-23b, AGT-2).
    """
    from app.services.host_query import check_discrete_filters

    check_discrete_filters(state=state)
    state = state.strip() if state and state.strip() else None
    scan = db.query(models.Scan).filter(
        models.Scan.id == scan_id,
        models.Scan.project_id == project_id,
    ).first()
    if not scan:
        raise ScanNotInProject([scan_id])

    base = (
        db.query(models.HostScanHistory, models.Host)
        .join(models.Host, models.Host.id == models.HostScanHistory.host_id)
        .filter(models.HostScanHistory.scan_id == scan_id)
    )
    if state:
        base = base.filter(models.HostScanHistory.state_at_scan == state)
    if search:
        escaped = escape_like(search)
        like = f"%{escaped}%"
        base = base.filter(
            or_(
                models.Host.ip_address.ilike(like),
                models.HostScanHistory.hostname_at_scan.ilike(like),
            )
        )

    total = base.with_entities(func.count(models.HostScanHistory.id)).scalar() or 0
    rows = (
        base.order_by(models.Host.ip_address)
        .offset(skip)
        .limit(limit)
        .all()
    )

    host_ids = [host.id for _hist, host in rows]
    ports_by_host: Dict[int, List[ScanPortSnapshot]] = {}
    # Per-host totals are counted over EVERY observed row; only the first
    # _SNAPSHOT_PORT_CAP rows per host are fetched and turned into snapshots.
    observed_counts: Dict[int, int] = {}
    open_counts: Dict[int, int] = {}
    if host_ids:
        # One query for the page's observed ports.  Joined to Port only for
        # its identity columns (number/protocol); membership, state AND the
        # service come from the observation.  v2.332.0 — service_name used to
        # be read from the live Port row, so a later scan re-fingerprinting a
        # port silently rewrote every older scan's "as scanned" view.  The
        # per-scan value has been written to PortScanHistory.service_info all
        # along; this is its first reader.
        # Counted and cut IN SQL (external review 2026-10-02 R2): the totals
        # are window counts over every observed row, and only the first
        # _SNAPSHOT_PORT_CAP rows of each host come back.  The cap used to be
        # applied in Python, after every row — service_info included — had
        # crossed from Postgres: 100 hosts × 10,000 ports was a million rows
        # read to show 5,000.  Protocol and id break the tie between tcp and
        # udp on one number, so the listed rows are the same on every read.
        per_host = {"partition_by": models.Port.host_id}
        ranked = (
            db.query(
                models.Port.host_id.label("host_id"),
                models.Port.port_number.label("port_number"),
                models.Port.protocol.label("protocol"),
                models.PortScanHistory.state_at_scan.label("state_at_scan"),
                models.PortScanHistory.service_info.label("service_info"),
                func.row_number().over(
                    order_by=(models.Port.port_number, models.Port.protocol, models.Port.id),
                    **per_host,
                ).label("position"),
                func.count().over(**per_host).label("observed"),
                func.sum(
                    case((models.PortScanHistory.state_at_scan == "open", 1), else_=0)
                ).over(**per_host).label("open_count"),
            )
            .select_from(models.PortScanHistory)
            .join(models.Port, models.PortScanHistory.port_id == models.Port.id)
            .filter(
                models.PortScanHistory.scan_id == scan_id,
                models.Port.host_id.in_(host_ids),
            )
            .subquery()
        )
        port_rows = (
            db.query(ranked)
            .filter(ranked.c.position <= _SNAPSHOT_PORT_CAP)
            .order_by(ranked.c.host_id, ranked.c.position)
            .all()
        )
        for row in port_rows:
            host_id = row.host_id
            observed_counts[host_id] = int(row.observed)
            open_counts[host_id] = int(row.open_count or 0)
            number, protocol = row.port_number, row.protocol
            state_at_scan, service_info = row.state_at_scan, row.service_info
            shown = ports_by_host.setdefault(host_id, [])
            shown.append(
                ScanPortSnapshot(
                    port_number=number,
                    protocol=protocol,
                    state_at_scan=state_at_scan,
                    service_name=_service_name_at_scan(service_info),
                )
            )

    items = []
    for hist, host in rows:
        items.append(ScanHostSnapshot(
            host_id=host.id,
            ip_address=host.ip_address,
            hostname_at_scan=hist.hostname_at_scan,
            state_at_scan=hist.state_at_scan,
            host_created=bool(hist.host_created),
            hostname_changed=bool(hist.hostname_changed) and not hist.host_created,
            credentialed=hist.credentialed,
            observed_port_count=observed_counts.get(host.id, 0),
            open_port_count=open_counts.get(host.id, 0),
            ports=ports_by_host.get(host.id, []),
        ))
    return Paginated[ScanHostSnapshot].build(items, total, skip, limit)


class ScanPortObservation(BaseModel):
    """One port as THIS scan recorded it (a ``PortScanHistory`` row)."""
    host_id: int
    ip_address: str
    port_number: int
    protocol: Optional[str] = None
    state_at_scan: Optional[str] = None
    service_name: Optional[str] = None
    service_product: Optional[str] = None
    service_version: Optional[str] = None


def _service_field(service_info: Optional[str], key: str) -> Optional[str]:
    try:
        data = json.loads(service_info) if service_info else None
    except (TypeError, ValueError):
        return None
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def scan_port_observations(
    db: Session,
    project_id: int,
    scan_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
) -> Paginated[ScanPortObservation]:
    """Every port this scan recorded, paged by address, port and protocol
    (comprehensive review 2026-10-16, A7: the Scan page's Ports tab listed
    the CURRENT ports of the first hosts it had loaded, under the scan's
    port total).  ``total`` is the scan's ``total_ports`` — the same rows.
    Raises ``ScanNotInProject`` when the scan is not one of this project's."""
    if not db.query(models.Scan.id).filter(
        models.Scan.id == scan_id, models.Scan.project_id == project_id,
    ).first():
        raise ScanNotInProject([scan_id])
    base = (
        db.query(models.PortScanHistory)
        .join(models.Port, models.PortScanHistory.port_id == models.Port.id)
        .join(models.Host, models.Host.id == models.Port.host_id)
        .filter(models.PortScanHistory.scan_id == scan_id)
    )
    total = base.with_entities(func.count(models.PortScanHistory.id)).scalar() or 0
    rows = (
        base.with_entities(
            models.Host.id, models.Host.ip_address, models.Port.port_number, models.Port.protocol,
            models.PortScanHistory.state_at_scan, models.PortScanHistory.service_name,
            models.PortScanHistory.service_info,
        )
        .order_by(models.Host.ip_address, models.Port.port_number, models.Port.protocol, models.Port.id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    items = [
        ScanPortObservation(
            host_id=host_id, ip_address=ip, port_number=number, protocol=protocol,
            state_at_scan=state, service_name=service_name or _service_name_at_scan(info),
            service_product=_service_field(info, "service_product"),
            service_version=_service_field(info, "service_version"),
        )
        for host_id, ip, number, protocol, state, service_name, info in rows
    ]
    return Paginated[ScanPortObservation].build(items, total, skip, limit)
