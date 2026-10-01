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
from sqlalchemy import func, or_
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
    ``ScanNotInProject`` when the scan is not one of this project's.
    """
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
    # Per-host totals counted for EVERY observed row; snapshot objects (and
    # the service_info JSON decode) built only for the first _SNAPSHOT_PORT_CAP
    # per host (v2.332.1).  The cap used to be applied after constructing a
    # snapshot for every row, so a page of broad-scan hosts materialised and
    # parsed thousands of objects it then threw away.
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
        port_rows = (
            db.query(
                models.Port.host_id,
                models.Port.port_number,
                models.Port.protocol,
                models.PortScanHistory.state_at_scan,
                models.PortScanHistory.service_info,
            )
            .select_from(models.PortScanHistory)
            .join(models.Port, models.PortScanHistory.port_id == models.Port.id)
            .filter(
                models.PortScanHistory.scan_id == scan_id,
                models.Port.host_id.in_(host_ids),
            )
            .order_by(models.Port.port_number)
            .all()
        )
        for host_id, number, protocol, state_at_scan, service_info in port_rows:
            observed_counts[host_id] = observed_counts.get(host_id, 0) + 1
            if state_at_scan == "open":
                open_counts[host_id] = open_counts.get(host_id, 0) + 1
            shown = ports_by_host.setdefault(host_id, [])
            if len(shown) >= _SNAPSHOT_PORT_CAP:
                continue
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
            credentialed=hist.credentialed,
            observed_port_count=observed_counts.get(host.id, 0),
            open_port_count=open_counts.get(host.id, 0),
            ports=ports_by_host.get(host.id, []),
        ))
    return Paginated[ScanHostSnapshot].build(items, total, skip, limit)
