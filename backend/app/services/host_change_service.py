"""Which hosts changed at their most-recent scan.

One derivation, shared by the Hosts-list "Changed" badge and the
investigation queue on My Work (v2.347.0 moved it here from the hosts
router so the read service could use it without importing a route).
"""
from __future__ import annotations

from typing import Iterable, Union

from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import Query, Session, aliased

from app.db import models


def hosts_changed_since_prior_scan(db: Session, host_ids: Union[Iterable[int], Query]) -> set:
    """Host ids that CHANGED at their most-recent scan vs the prior one — a
    host-state flip (HostScanHistory.state_at_scan) or a port first observed
    after the prior scan.  Hosts with <2 scans are never "changed" (the first
    scan is all-new, not a change).  Batched via a window function — no N+1.
    Removed-port detection is intentionally omitted: the dedup keeps ports and
    doesn't track per-scan presence, so "removed" isn't reliably derivable.

    ``host_ids`` is a collection of ids (the Hosts list passes one page) or a
    single-column ``Query`` of host ids (the investigation queue passes its
    candidate set, tens of thousands on a large project).

    v2.374.4 — the comparison runs IN the database and only the changed ids
    come back.  It used to fetch the two newest history rows of every host and
    the newest port of every host, then compare in Python; with the queue's
    candidate list that was ~20k bind parameters twice over and ~80k rows to
    find a handful of changed hosts.
    """
    if not isinstance(host_ids, Query):
        host_ids = list(host_ids)
        if not host_ids:
            return set()

    history = models.HostScanHistory
    ranked = (
        db.query(
            history.host_id.label("hid"),
            history.discovered_at.label("disc"),
            history.state_at_scan.label("state"),
            func.row_number().over(
                partition_by=history.host_id, order_by=history.discovered_at.desc(),
            ).label("rn"),
        )
        .filter(history.host_id.in_(host_ids))
        .cte("ranked_history")
    )
    latest, prior = aliased(ranked), aliased(ranked)
    # A port first observed AFTER the prior scan = added in the latest sweep.
    # (A NULL prior time compares to NULL — not changed, as before.)
    port_added = exists().where(
        and_(models.Port.host_id == prior.c.hid, models.Port.first_seen > prior.c.disc)
    )
    rows = (
        db.query(latest.c.hid)
        .join(prior, and_(prior.c.hid == latest.c.hid, prior.c.rn == 2))
        .filter(
            latest.c.rn == 1,
            # IS DISTINCT FROM: a state going to or from NULL is a flip, which is
            # what Python's `!=` said.
            or_(latest.c.state.is_distinct_from(prior.c.state), port_added),
        )
        .all()
    )
    return {hid for (hid,) in rows}
