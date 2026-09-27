"""Attack-surface delta between two scans of a project (moved out of the
``/scans/compare`` route in v2.428.0 so the agent read ``GET
/agent/assist/scans/compare`` wraps the same code — one implementation, one
answer).

Reconstructed from HostScanHistory / PortScanHistory (per-scan observations),
so it works across the dedup boundary.
"""
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db import models


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
    # Lists below are capped at row_cap; `counts` carries exact totals.
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


def compute_scan_diff(
    db: Session, project_id: int, a: int, b: int, row_cap: int = _SCAN_DIFF_ROW_CAP,
) -> ScanDiffResponse:
    """Diff two scans of this project: which hosts/ports appeared, vanished,
    or changed state between baseline ``a`` and comparison ``b``.

    Reconstructed from HostScanHistory / PortScanHistory (per-scan
    observations), so it works across the dedup boundary — a host that
    persists across scans is a single Host row, but each scan's view of
    it is preserved in history.  Result lists are capped at ``row_cap``;
    the ``counts`` block carries exact totals.
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

    # --- Host-level diff (presence + state) ---
    a_host_states = dict(
        db.query(models.HostScanHistory.host_id, models.HostScanHistory.state_at_scan)
        .filter(models.HostScanHistory.scan_id == a)
        .all()
    )
    b_host_states = dict(
        db.query(models.HostScanHistory.host_id, models.HostScanHistory.state_at_scan)
        .filter(models.HostScanHistory.scan_id == b)
        .all()
    )
    a_host_ids, b_host_ids = set(a_host_states), set(b_host_states)
    new_host_ids = b_host_ids - a_host_ids
    dropped_host_ids = a_host_ids - b_host_ids
    changed_host_ids = {
        hid
        for hid in (a_host_ids & b_host_ids)
        if (a_host_states.get(hid) or "") != (b_host_states.get(hid) or "")
    }

    # --- Port-level diff (openness transitions) ---
    # Query only the *changed* port ids via NOT IN subqueries rather than
    # loading both scans' full (port_id, state) maps into Python.  Two
    # broad scans can each observe 100k+ ports; materialising both just to
    # set-diff them was the memory hot spot.  ``port_id NOT IN (open-in-X)``
    # captures both "missing in X" and "present-but-not-open in X", matching
    # the prior dict logic.  (NOT IN subquery is portable to the SQLite test
    # backend, unlike FULL OUTER JOIN.)
    a_open_ports_subq = (
        db.query(models.PortScanHistory.port_id)
        .filter(
            models.PortScanHistory.scan_id == a,
            models.PortScanHistory.state_at_scan == "open",
        )
    )
    b_open_ports_subq = (
        db.query(models.PortScanHistory.port_id)
        .filter(
            models.PortScanHistory.scan_id == b,
            models.PortScanHistory.state_at_scan == "open",
        )
    )
    newly_open_ids = [
        pid for (pid,) in (
            db.query(models.PortScanHistory.port_id)
            .filter(
                models.PortScanHistory.scan_id == b,
                models.PortScanHistory.state_at_scan == "open",
                ~models.PortScanHistory.port_id.in_(a_open_ports_subq),
            )
            .all()
        )
    ]
    # v2.332.0 — "open in A, not open in B" is two different facts and only
    # one of them is remediation evidence.  Closed: B has an observation for
    # the port and it is not open.  Not observed: B has no row for the port —
    # B never tested it (different port range, tool or target list), so
    # nothing can be said about it.  Both were previously one "closed" list.
    b_any_ports_subq = (
        db.query(models.PortScanHistory.port_id)
        .filter(models.PortScanHistory.scan_id == b)
    )
    closed_ids = [
        pid for (pid,) in (
            db.query(models.PortScanHistory.port_id)
            .filter(
                models.PortScanHistory.scan_id == a,
                models.PortScanHistory.state_at_scan == "open",
                models.PortScanHistory.port_id.in_(b_any_ports_subq),
                ~models.PortScanHistory.port_id.in_(b_open_ports_subq),
            )
            .all()
        )
    ]
    not_observed_ids = [
        pid for (pid,) in (
            db.query(models.PortScanHistory.port_id)
            .filter(
                models.PortScanHistory.scan_id == a,
                models.PortScanHistory.state_at_scan == "open",
                ~models.PortScanHistory.port_id.in_(b_any_ports_subq),
            )
            .all()
        )
    ]

    counts = ScanDiffCounts(
        new_hosts=len(new_host_ids),
        dropped_hosts=len(dropped_host_ids),
        host_state_changes=len(changed_host_ids),
        newly_open_ports=len(newly_open_ids),
        closed_ports=len(closed_ids),
        not_observed_ports=len(not_observed_ids),
    )

    # Resolve host metadata only for the rows we'll actually return.
    host_ids_needed = set(
        list(new_host_ids)[:cap] + list(dropped_host_ids)[:cap] + list(changed_host_ids)[:cap]
    )
    host_meta = (
        {h.id: h for h in db.query(models.Host).filter(models.Host.id.in_(host_ids_needed))}
        if host_ids_needed
        else {}
    )

    def host_rows(ids) -> List[ScanDiffHostRow]:
        rows = []
        for hid in list(ids)[:cap]:
            h = host_meta.get(hid)
            if h is not None:
                rows.append(ScanDiffHostRow(host_id=hid, ip_address=h.ip_address, hostname=h.hostname))
        return rows

    host_state_change_rows = []
    for hid in list(changed_host_ids)[:cap]:
        h = host_meta.get(hid)
        if h is not None:
            host_state_change_rows.append(ScanDiffHostStateChange(
                host_id=hid,
                ip_address=h.ip_address,
                hostname=h.hostname,
                state_a=a_host_states.get(hid),
                state_b=b_host_states.get(hid),
            ))

    # Resolve port metadata (Port -> Host) only for capped port rows.
    port_ids_needed = (
        set(newly_open_ids[:cap]) | set(closed_ids[:cap]) | set(not_observed_ids[:cap])
    )
    port_meta: Dict[int, tuple] = {}
    if port_ids_needed:
        for pid, pnum, proto, svc, hid, ip in (
            db.query(
                models.Port.id,
                models.Port.port_number,
                models.Port.protocol,
                models.Port.service_name,
                models.Host.id,
                models.Host.ip_address,
            )
            .join(models.Host, models.Port.host_id == models.Host.id)
            .filter(models.Port.id.in_(port_ids_needed))
            .all()
        ):
            port_meta[pid] = (pnum, proto, svc, hid, ip)

    # Per-port state in each scan, ONLY for the capped rows we return.
    # The openness diff above uses NOT-IN subqueries (no full state maps),
    # so these were never built — port_rows referenced undefined
    # a_port_states/b_port_states and raised NameError.  Bounded lookup
    # restores accurate state_a/state_b (e.g. a newly-open port shows its
    # prior non-open state in scan A) without materialising every port.
    a_port_states: Dict[int, str] = {}
    b_port_states: Dict[int, str] = {}
    if port_ids_needed:
        a_port_states = dict(
            db.query(models.PortScanHistory.port_id, models.PortScanHistory.state_at_scan)
            .filter(
                models.PortScanHistory.scan_id == a,
                models.PortScanHistory.port_id.in_(port_ids_needed),
            )
            .all()
        )
        b_port_states = dict(
            db.query(models.PortScanHistory.port_id, models.PortScanHistory.state_at_scan)
            .filter(
                models.PortScanHistory.scan_id == b,
                models.PortScanHistory.port_id.in_(port_ids_needed),
            )
            .all()
        )

    def port_rows(ids) -> List[ScanDiffPortChange]:
        rows = []
        for pid in ids[:cap]:
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
        new_hosts=host_rows(new_host_ids),
        dropped_hosts=host_rows(dropped_host_ids),
        host_state_changes=host_state_change_rows,
        newly_open_ports=port_rows(newly_open_ids),
        closed_ports=port_rows(closed_ids),
        not_observed_ports=port_rows(not_observed_ids),
    )
