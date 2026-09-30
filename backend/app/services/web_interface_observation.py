"""When a web interface was OBSERVED, and which row is the latest (v2.433.0).

``web_interfaces`` keeps one row per scan: the same URL captured by EyeWitness
in August and again in September is two rows.  ``first_seen`` / ``last_seen``
are the database's clock (import time, and every row update), so ranking by
them let an old scan imported late become "the latest".  The observation time
is the scan's own end (else start) time when the tool recorded one, and the
import time otherwise.

One definition for the host inspector's list (``GET /hosts/{id}/web-interfaces``)
and the agent's (``assist_list_host_web_interfaces`` / host detail) — the
agent could not tell current from historical state before this (agent
feedback #24).  The latest-per-(tool, URL) rule mirrors the frontend's
``latestObservations`` keyed ``source|url`` by ``webObservedAt``: newest
observation, the higher id breaking a tie.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, Optional, Set

from sqlalchemy.orm import Session

from app.db import models
from app.services.scan_time import scan_time_for_api


@dataclass(frozen=True)
class Observation:
    observed_at: Optional[datetime]
    basis: str  # "scan" | "import"
    scan_filename: Optional[str]


def observations(db: Session, rows: Iterable) -> Dict[int, Observation]:
    """``{row.id: Observation}`` for web-interface rows — one grouped scan
    lookup, never one per row."""
    rows = list(rows)
    scan_ids = {r.scan_id for r in rows if r.scan_id is not None}
    scans = {
        s.id: s
        for s in (
            db.query(
                models.Scan.id, models.Scan.start_time, models.Scan.end_time,
                models.Scan.time_source, models.Scan.filename,
            )
            .filter(models.Scan.id.in_(scan_ids))
            .all()
            if scan_ids else []
        )
    }
    out: Dict[int, Observation] = {}
    for r in rows:
        s = scans.get(r.scan_id)
        tool_time = (s.end_time or s.start_time) if s else None
        if tool_time is not None:
            out[r.id] = Observation(scan_time_for_api(tool_time, s.time_source), "scan", s.filename)
        else:
            out[r.id] = Observation(r.first_seen, "import", s.filename if s else None)
    return out


def latest_ids_for(db: Session, host_id: int, rows: Iterable) -> Set[int]:
    """Ids of the latest row per (source, url) for the (source, url) groups
    that ``rows`` (a page) contains — ranked in SQL across ALL of the host's
    observations of those URLs, since a newer one may sit on another page.

    The one "latest" rule (v2.433.1 removed an unused Python copy): the
    scan's own end, else start, time, else the import time; the higher id
    breaks a tie.  Scan times are stored zone-less and compare as UTC here,
    as :func:`observations` reads them; a ``tool_clock`` scan's wall-clock
    time has no known zone, so it ranks as if UTC — the same assumption the
    host page makes when it sorts by ``observed_at``.
    """
    from sqlalchemy import and_, func, or_

    keys = {(r.source, r.url) for r in rows}
    if not keys:
        return set()
    W, S = models.WebInterface, models.Scan
    observed = func.coalesce(S.end_time, S.start_time, W.first_seen)
    ranked = (
        db.query(
            W.id.label("id"),
            func.row_number().over(
                partition_by=(W.source, W.url),
                order_by=(observed.desc().nulls_last(), W.id.desc()),
            ).label("rn"),
        )
        .outerjoin(S, S.id == W.scan_id)
        .filter(
            W.host_id == host_id,
            or_(*[and_(W.source == src, W.url == url) for src, url in keys]),
        )
        .subquery()
    )
    return {rid for (rid,) in db.query(ranked.c.id).filter(ranked.c.rn == 1).all()}


