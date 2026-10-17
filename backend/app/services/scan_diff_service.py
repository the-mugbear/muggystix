"""Attack-surface delta between two scans of a project (moved out of the
``/scans/compare`` route in v2.428.0 so the agent read ``GET
/agent/assist/scans/compare`` wraps the same code — one implementation, one
answer).

Reconstructed from HostScanHistory / PortScanHistory (per-scan observations),
so it works across the dedup boundary.

2.510.1 (external review 2026-10-16, A5): every count is SQL and every list a
LIMITed, address-ordered sample — the two scans' host maps and every changed
port id were loaded into Python only to count them and keep ``row_cap``
(unordered: ``list(set)[:cap]``).  The comparisons are correlated
``EXISTS`` / ``NOT EXISTS``, never ``NOT IN (subquery)``.
"""
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import and_, case, cast, distinct, exists, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Session, aliased

from app.db import models
from app.services import host_query_dsl as dsl
from app.services import host_query_predicates as P


class ScanNotInProject(LookupError):
    """One or both scan ids are not scans of this project."""

    def __init__(self, missing: List[int]):
        self.missing = missing
        super().__init__(", ".join(str(x) for x in missing))


class ScanDiffSide(BaseModel):
    scan_id: int
    filename: str
    tool_name: Optional[str] = None
    scan_type: Optional[str] = None
    created_at: Optional[datetime] = None
    total_hosts: int = 0
    up_hosts: int = 0
    total_ports: int = 0
    open_ports: int = 0


class ScanDiffHostRow(BaseModel):
    host_id: int
    ip_address: str
    hostname: Optional[str] = None


class ScanDiffHostStateChange(BaseModel):
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    state_a: Optional[str] = None
    state_b: Optional[str] = None


class ScanDiffPortChange(BaseModel):
    host_id: int
    ip_address: str
    port_number: int
    protocol: Optional[str] = None
    service_name: Optional[str] = None
    state_a: Optional[str] = None
    state_b: Optional[str] = None


class ScanDiffCounts(BaseModel):
    new_hosts: int = 0
    dropped_hosts: int = 0
    host_state_changes: int = 0
    newly_open_ports: int = 0
    # v2.332.0 — split.  ``closed_ports`` is open in A and observed NOT open
    # in B (B tested it and got closed/filtered).  ``not_observed_ports`` is
    # open in A with no observation in B at all (different port range, tool,
    # or target list).  They used to be one number labelled "closed", which
    # presented "we didn't look" as remediation evidence.
    closed_ports: int = 0
    not_observed_ports: int = 0


class ScanDiffResponse(BaseModel):
    scan_a: ScanDiffSide
    scan_b: ScanDiffSide
    counts: ScanDiffCounts
    # Lists below are capped at row_cap (the first by address); `counts`
    # carries exact totals.
    row_cap: int
    new_hosts: List[ScanDiffHostRow] = Field(default_factory=list)
    dropped_hosts: List[ScanDiffHostRow] = Field(default_factory=list)
    host_state_changes: List[ScanDiffHostStateChange] = Field(default_factory=list)
    newly_open_ports: List[ScanDiffPortChange] = Field(default_factory=list)
    closed_ports: List[ScanDiffPortChange] = Field(default_factory=list)
    not_observed_ports: List[ScanDiffPortChange] = Field(default_factory=list)


_SCAN_DIFF_ROW_CAP = 500


def _scan_side_stats(db: Session, scan: models.Scan) -> ScanDiffSide:
    """Per-scan observed host/port totals from the history tables."""
    host_row = (
        db.query(
            func.count(models.HostScanHistory.host_id),
            func.sum(case((models.HostScanHistory.state_at_scan == "up", 1), else_=0)),
        )
        .filter(models.HostScanHistory.scan_id == scan.id)
        .one()
    )
    port_row = (
        db.query(
            func.count(models.PortScanHistory.port_id),
            func.sum(case((models.PortScanHistory.state_at_scan == "open", 1), else_=0)),
        )
        .filter(models.PortScanHistory.scan_id == scan.id)
        .one()
    )
    return ScanDiffSide(
        scan_id=scan.id,
        filename=scan.filename,
        tool_name=scan.tool_name,
        scan_type=scan.scan_type,
        created_at=scan.created_at,
        total_hosts=int(host_row[0] or 0),
        up_hosts=int(host_row[1] or 0),
        total_ports=int(port_row[0] or 0),
        open_ports=int(port_row[1] or 0),
    )


def _address_order(db: Session):
    """Hosts by address as an address (``10.0.0.2`` before ``10.0.0.10``) —
    as the Hosts list orders them; text order on a database that has no
    ``inet``."""
    if db.get_bind().dialect.name == "postgresql":
        return cast(models.Host.ip_address, INET)
    return models.Host.ip_address


def compute_scan_diff(
    db: Session, project_id: int, a: int, b: int, row_cap: int = _SCAN_DIFF_ROW_CAP,
) -> ScanDiffResponse:
    """Diff two scans of this project: which hosts/ports appeared, vanished,
    or changed state between baseline ``a`` and comparison ``b``.

    Reconstructed from HostScanHistory / PortScanHistory (per-scan
    observations), so it works across the dedup boundary — a host that
    persists across scans is a single Host row, but each scan's view of
    it is preserved in history.  Each list is the first ``row_cap`` by
    address (then port); the ``counts`` block carries exact totals.  Every
    count and list is one SQL statement; nothing scan-sized is loaded.
    """
    scans = {
        s.id: s
        for s in db.query(models.Scan).filter(
            models.Scan.project_id == project_id,
            models.Scan.id.in_([a, b]),
        )
    }
    scan_a = scans.get(a)
    scan_b = scans.get(b)
    if scan_a is None or scan_b is None:
        raise ScanNotInProject([x for x in (a, b) if x not in scans])

    cap = row_cap
    Host = models.Host
    HSH = models.HostScanHistory
    PSH = models.PortScanHistory
    address = _address_order(db)

    # --- Hosts seen by one scan and not the other ---
    def host_presence(present_in: int, absent_in: int):
        # The Hosts language's own `scan:<present> AND NOT scan:<absent>` — the
        # list "View all on Hosts" opens, so the count IS that list's total
        # (review 2026-10-16, C1: the button opened every host of the scan).
        condition = and_(
            Host.project_id == project_id,
            P.scan_predicate([present_in]),
            dsl.negate(P.scan_predicate([absent_in])),
        )
        count = db.query(func.count(Host.id)).filter(condition).scalar() or 0
        sample = (
            db.query(Host.id, Host.ip_address, Host.hostname)
            .filter(condition)
            .order_by(address, Host.id)
            .limit(cap)
            .all()
        )
        return int(count), [ScanDiffHostRow(host_id=h, ip_address=ip, hostname=name) for h, ip, name in sample]

    new_count, new_rows = host_presence(b, a)
    dropped_count, dropped_rows = host_presence(a, b)

    # State changes: a host both scans saw, with a different state.  A
    # missing state reads as "" (as before).
    ha, hb = aliased(HSH), aliased(HSH)
    changed = and_(
        ha.scan_id == a, hb.scan_id == b, hb.host_id == ha.host_id,
        func.coalesce(ha.state_at_scan, "") != func.coalesce(hb.state_at_scan, ""),
    )
    changed_count = (
        db.query(func.count(distinct(ha.host_id))).select_from(ha).join(hb, hb.host_id == ha.host_id)
        .filter(changed).scalar()
    ) or 0
    changed_rows = [
        ScanDiffHostStateChange(host_id=h, ip_address=ip, hostname=name, state_a=sa, state_b=sb)
        for h, ip, name, sa, sb in (
            db.query(Host.id, Host.ip_address, Host.hostname,
                     func.max(ha.state_at_scan), func.max(hb.state_at_scan))
            .select_from(ha)
            .join(hb, hb.host_id == ha.host_id)
            .join(Host, Host.id == ha.host_id)
            .filter(changed)
            .group_by(Host.id, Host.ip_address, Host.hostname)
            .order_by(address, Host.id)
            .limit(cap)
            .all()
        )
    ]

    # --- Ports: openness transitions ---
    def port_seen(scan_id: int, port_col, open_only: bool):
        other = aliased(PSH)
        conds = [other.scan_id == scan_id, other.port_id == port_col]
        if open_only:
            conds.append(other.state_at_scan == "open")
        return exists().where(*conds)

    def port_change(scan_id: int, *extra):
        """(count, the first ``cap`` port ids by address and port) of the
        ports open in ``scan_id`` meeting ``extra``."""
        row = aliased(PSH)
        condition = and_(row.scan_id == scan_id, row.state_at_scan == "open", *[e(row) for e in extra])
        count = (
            db.query(func.count(distinct(row.port_id))).select_from(row).filter(condition).scalar()
        ) or 0
        ids = [
            pid for (pid,) in (
                db.query(models.Port.id)
                .select_from(row)
                .join(models.Port, models.Port.id == row.port_id)
                .join(Host, Host.id == models.Port.host_id)
                .filter(condition)
                .group_by(models.Port.id, Host.ip_address, Host.id, models.Port.port_number, models.Port.protocol)
                .order_by(address, Host.id, models.Port.port_number, models.Port.protocol, models.Port.id)
                .limit(cap)
                .all()
            )
        ]
        return int(count), ids

    # Open in B, not open in A (absent from A, or present there not open).
    newly_open_count, newly_open_ids = port_change(b, lambda r: ~port_seen(a, r.port_id, open_only=True))
    # v2.332.0 — "open in A, not open in B" is two different facts and only
    # one of them is remediation evidence.  Closed: B observed the port and
    # it is not open.  Not observed: B has no row for it — nothing can be said.
    closed_count, closed_ids = port_change(
        a,
        lambda r: port_seen(b, r.port_id, open_only=False),
        lambda r: ~port_seen(b, r.port_id, open_only=True),
    )
    not_observed_count, not_observed_ids = port_change(a, lambda r: ~port_seen(b, r.port_id, open_only=False))

    counts = ScanDiffCounts(
        new_hosts=new_count,
        dropped_hosts=dropped_count,
        host_state_changes=changed_count,
        newly_open_ports=newly_open_count,
        closed_ports=closed_count,
        not_observed_ports=not_observed_count,
    )

    # Port metadata and each scan's state, for the sampled rows only.
    port_ids_needed = set(newly_open_ids) | set(closed_ids) | set(not_observed_ids)
    port_meta: Dict[int, tuple] = {}
    a_port_states: Dict[int, str] = {}
    b_port_states: Dict[int, str] = {}
    if port_ids_needed:
        for pid, pnum, proto, svc, hid, ip in (
            db.query(
                models.Port.id, models.Port.port_number, models.Port.protocol, models.Port.service_name,
                Host.id, Host.ip_address,
            )
            .join(Host, models.Port.host_id == Host.id)
            .filter(models.Port.id.in_(port_ids_needed))
            .all()
        ):
            port_meta[pid] = (pnum, proto, svc, hid, ip)
        for states, scan_id in ((a_port_states, a), (b_port_states, b)):
            states.update(
                db.query(PSH.port_id, PSH.state_at_scan)
                .filter(PSH.scan_id == scan_id, PSH.port_id.in_(port_ids_needed))
                .all()
            )

    def port_rows(ids) -> List[ScanDiffPortChange]:
        rows = []
        for pid in ids:
            meta = port_meta.get(pid)
            if meta is None:
                continue
            pnum, proto, svc, hid, ip = meta
            rows.append(ScanDiffPortChange(
                host_id=hid,
                ip_address=ip,
                port_number=pnum,
                protocol=proto,
                service_name=svc,
                state_a=a_port_states.get(pid),
                state_b=b_port_states.get(pid),
            ))
        return rows

    return ScanDiffResponse(
        scan_a=_scan_side_stats(db, scan_a),
        scan_b=_scan_side_stats(db, scan_b),
        counts=counts,
        row_cap=cap,
        new_hosts=new_rows,
        dropped_hosts=dropped_rows,
        host_state_changes=changed_rows,
        newly_open_ports=port_rows(newly_open_ids),
        closed_ports=port_rows(closed_ids),
        not_observed_ports=port_rows(not_observed_ids),
    )
