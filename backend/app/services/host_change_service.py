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
from app.services.host_query_predicates import state_stated_condition


def hosts_changed_since_prior_scan(db: Session, host_ids: Union[Iterable[int], Query]) -> set:
    """Host ids that CHANGED at their most-recent scan vs the prior one — a
    host-state flip (HostScanHistory.state_at_scan) or a port first observed
    after the prior scan.  Hosts with <2 scans are never "changed" (the first
    scan is all-new, not a change).  A scan whose history row states no
    liveness (``state_at_scan`` NULL: a DNS import, a web capture with no
    answer) is not one of the two compared.  Batched via a window function —
    no N+1.
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
    return {hid for (hid,) in changed_since_prior_scan_query(db, host_ids).all()}


def changed_since_prior_scan_query(db: Session, host_ids: Union[Iterable[int], Query]) -> Query:
    """The same derivation as :func:`hosts_changed_since_prior_scan`, as a
    single-column query of host ids — for callers that rank in SQL and must
    not bring the set back to Python (the investigation queue, v2.424.1)."""
    history = models.HostScanHistory
    ranked = (
        db.query(
            history.host_id.label("hid"),
            history.discovered_at.label("disc"),
            history.state_at_scan.label("state"),
            func.row_number().over(
                # The id breaks a tie: two history rows of one host written in
                # the same instant otherwise ranked in no fixed order, and
                # "latest" vs "prior" could swap between two reads.
                partition_by=history.host_id,
                order_by=(history.discovered_at.desc(), history.id.desc()),
            ).label("rn"),
        )
        # A history row that states no liveness is not one of the two scans
        # compared: a DNS import records which hosts its answers addressed
        # (``state_at_scan`` NULL — a name answer does not say the host is
        # up), and ranked here it read as a flip from 'up' on every host it
        # named.  The comparison is between the two latest scans that SAID
        # something about the host's state — and 'unknown' says nothing (as
        # dedup reads it): a host a DNS import CREATED is recorded 'unknown',
        # and the next scan that saw it up read as "Changed" (review
        # 2026-10-14, H8; a gnmap Ports-only line is 'unknown' too).
        .filter(history.host_id.in_(host_ids), state_stated_condition(history.state_at_scan))
        .cte("ranked_history")
    )
    latest, prior = aliased(ranked), aliased(ranked)
    # A port first observed AFTER the prior scan = added in the latest sweep.
    # (A NULL prior time compares to NULL — not changed, as before.)
    port_added = exists().where(
        and_(models.Port.host_id == prior.c.hid, models.Port.first_seen > prior.c.disc)
    )
    return (
        db.query(latest.c.hid)
        .join(prior, and_(prior.c.hid == latest.c.hid, prior.c.rn == 2))
        .filter(
            latest.c.rn == 1,
            # Both states are stated (rows with none are not ranked).
            or_(latest.c.state != prior.c.state, port_added),
        )
    )
