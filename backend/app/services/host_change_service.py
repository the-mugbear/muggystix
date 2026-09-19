"""Which hosts changed at their most-recent scan.

One derivation, shared by the Hosts-list "Changed" badge and the
investigation queue on My Work (v2.347.0 moved it here from the hosts
router so the read service could use it without importing a route).
"""
from __future__ import annotations

from datetime import timezone
from typing import Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import models


def hosts_changed_since_prior_scan(db: Session, host_ids: List[int]) -> set:
    """Host ids that CHANGED at their most-recent scan vs the prior one — a
    host-state flip (HostScanHistory.state_at_scan) or a port first observed
    after the prior scan.  Hosts with <2 scans are never "changed" (the first
    scan is all-new, not a change).  Batched via a window function — no N+1.
    Removed-port detection is intentionally omitted: the dedup keeps ports and
    doesn't track per-scan presence, so "removed" isn't reliably derivable.
    """
    if not host_ids:
        return set()
    rn = func.row_number().over(
        partition_by=models.HostScanHistory.host_id,
        order_by=models.HostScanHistory.discovered_at.desc(),
    ).label("rn")
    sub = (
        db.query(
            models.HostScanHistory.host_id.label("hid"),
            models.HostScanHistory.discovered_at.label("disc"),
            models.HostScanHistory.state_at_scan.label("state"),
            rn,
        )
        .filter(models.HostScanHistory.host_id.in_(host_ids))
        .subquery()
    )
    latest: Dict[int, tuple] = {}
    prior: Dict[int, tuple] = {}
    for hid, disc, state, rn_ in db.query(sub.c.hid, sub.c.disc, sub.c.state, sub.c.rn).filter(sub.c.rn <= 2).all():
        (latest if rn_ == 1 else prior)[hid] = (disc, state)

    changed: set = set()
    # State flip between the two most recent scans.
    for hid, (_pdisc, pstate) in prior.items():
        if hid in latest and latest[hid][1] != pstate:
            changed.add(hid)
    # A port first observed AFTER the prior scan = added in the latest sweep.
    prior_time = {hid: prior[hid][0] for hid in prior}
    if prior_time:
        maxfs = dict(
            db.query(models.Port.host_id, func.max(models.Port.first_seen))
            .filter(models.Port.host_id.in_(list(prior_time)))
            .group_by(models.Port.host_id)
            .all()
        )
        for hid, ptime in prior_time.items():
            mfs = maxfs.get(hid)
            if mfs is None or ptime is None:
                continue
            mfs = mfs if mfs.tzinfo else mfs.replace(tzinfo=timezone.utc)
            pt = ptime if ptime.tzinfo else ptime.replace(tzinfo=timezone.utc)
            if mfs > pt:
                changed.add(hid)
    return changed
