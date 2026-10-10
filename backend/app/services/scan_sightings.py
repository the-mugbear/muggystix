"""Which scans reported a row, and what happens to the row when one of them
is deleted.

``scripts_v2``, ``host_scripts_v2``, ``host_attributes`` and
``vulnerabilities`` hold one row per thing, overwritten by every scan that
reports it.  The row names the scan that FIRST recorded it (``scan_id``; a
vulnerability also the last, ``last_seen_scan_id``); the complete answer is a
sighting row per (thing, scan) in ``script_sightings``,
``host_script_sightings``, ``host_attribute_sightings`` and
``vulnerability_sightings``.

* Writers call ``see`` / ``see_many`` on every insert AND every
  re-observation, inside the savepoint that holds the row, so a record that
  is rolled back leaves no sighting.
* ``release_scan`` is the ONE step run before a scan row is deleted, by the
  automatic cleanup (``ingestion_service.delete_partial_scan``) and by the
  hand delete (``DELETE /scans/{id}``).  A table that merges a thing with the
  scan that saw it gets a sighting table, a kind here and a line there.

Nothing else reads the sighting tables — no page, list or filter — except
the delete paths' guards and the hand delete's preview, which count with the
conditions exposed here (``removed_with_scan``, ``kept_without_scan``,
``another_scan_reported``).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.db import models
from app.db.models_confidence import HostConfidence, PortConfidence
from app.db.models_findings import Finding, FindingVulnerability
from app.db.models_proposals import AgentProposal
from app.db.models_vulnerability import (
    HostAttribute,
    HostAttributeSighting,
    Vulnerability,
    VulnerabilitySighting,
)

SCRIPT = "script"
HOST_SCRIPT = "host_script"
HOST_ATTRIBUTE = "host_attribute"
VULNERABILITY = "vulnerability"


class _Kind(NamedTuple):
    sighting: Any       # the sighting model
    thing_column: str   # its column naming the thing
    thing: Any          # the model of the thing itself


_KINDS: Dict[str, _Kind] = {
    SCRIPT: _Kind(models.ScriptSighting, "script_id", models.Script),
    HOST_SCRIPT: _Kind(models.HostScriptSighting, "host_script_id", models.HostScript),
    HOST_ATTRIBUTE: _Kind(HostAttributeSighting, "host_attribute_id", HostAttribute),
    VULNERABILITY: _Kind(VulnerabilitySighting, "vulnerability_id", Vulnerability),
}

# Pairs per INSERT: two bind parameters each, far below the driver's limit.
_PAIRS_PER_STATEMENT = 5000


def _is_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def see_many(db: Session, kind: str, pairs: Iterable[Tuple[Optional[int], Optional[int]]]) -> int:
    """Record that each ``(thing id, scan id)`` pair was reported: one
    ``INSERT … ON CONFLICT DO NOTHING`` per ``_PAIRS_PER_STATEMENT`` pairs, so
    a repeat within the scan is one sighting.  A pair without a scan (a
    backfill with no scan to name) or without a stored row records nothing.
    Runs in the caller's transaction or savepoint.  Returns how many pairs
    were sent."""
    spec = _KINDS[kind]
    rows: List[Dict[str, int]] = [
        {spec.thing_column: thing_id, "scan_id": scan_id}
        for thing_id, scan_id in dict.fromkeys(pairs)
        if _is_id(thing_id) and _is_id(scan_id)
    ]
    if not rows:
        return 0
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    table = spec.sighting.__table__
    for start in range(0, len(rows), _PAIRS_PER_STATEMENT):
        db.execute(insert(table).values(rows[start:start + _PAIRS_PER_STATEMENT]).on_conflict_do_nothing())
    return len(rows)


def see(db: Session, kind: str, thing_id: Optional[int], scan_id: Optional[int]) -> None:
    """Record that ``scan_id`` reported one row (see ``see_many``)."""
    see_many(db, kind, ((thing_id, scan_id),))


def only_this_scan_saw(scan_id: int):
    """Condition on ``Vulnerability``: ``scan_id`` first recorded the row and
    nothing says another scan saw it — no other sighting, and no other scan
    named as the last to see it.  A scan delete removes these rows
    (``removed_with_scan``) unless someone's work refers to them
    (``kept_without_scan``)."""
    other = aliased(VulnerabilitySighting)
    return and_(
        Vulnerability.scan_id == scan_id,
        or_(Vulnerability.last_seen_scan_id.is_(None), Vulnerability.last_seen_scan_id == scan_id),
        ~exists().where(other.vulnerability_id == Vulnerability.id, other.scan_id != scan_id),
    )


def work_refers_to_it():
    """Condition on ``Vulnerability``: a finding or a proposal refers to the
    row — a person's or an agent's work, which a scan delete never removes.
    The port such a row sits on stays with it: the port step's guard
    (``ingestion_service._port_is_only_this_attempts``) asks this too."""
    return or_(
        exists().where(Finding.vuln_id == Vulnerability.id),
        exists().where(FindingVulnerability.vuln_id == Vulnerability.id),
        exists().where(AgentProposal.vulnerability_id == Vulnerability.id),
    )


def removed_with_scan(scan_id: int):
    """Condition on ``Vulnerability``: the rows ``release_scan`` deletes —
    only this scan reported them and no finding or proposal refers to them.
    The deletion preview counts with it."""
    return and_(only_this_scan_saw(scan_id), ~work_refers_to_it())


def kept_without_scan(scan_id: int):
    """Condition on ``Vulnerability``: only this scan reported the row, and a
    finding or a proposal refers to it — it stays, with no scan behind it."""
    return and_(only_this_scan_saw(scan_id), work_refers_to_it())


def another_scan_reported(kind: str, scan_id: int, thing: Any = None):
    """``EXISTS``: a scan other than ``scan_id`` has a sighting of the row
    (``thing`` — the kind's model, or an alias of it).  For a caller that
    must know, BEFORE ``release_scan`` has moved the first-recorder pointers,
    whether a row is this scan's alone."""
    spec = _KINDS[kind]
    other = aliased(spec.sighting)
    row = spec.thing if thing is None else thing
    return exists().where(getattr(other, spec.thing_column) == row.id, other.scan_id != scan_id)


def _another_sighting(spec: _Kind, scan_id: int, *, newest: bool = False):
    """``(exists, scan id)`` over the sightings of ``spec.thing`` by a scan
    other than ``scan_id``: whether there is one, and the earliest (or
    newest) of them by ``seen_at``, then scan id."""
    other = aliased(spec.sighting)
    of_this_row = [getattr(other, spec.thing_column) == spec.thing.id, other.scan_id != scan_id]
    order = (other.seen_at.desc(), other.scan_id.desc()) if newest else (other.seen_at, other.scan_id)
    which = (
        select(other.scan_id).where(*of_this_row).order_by(*order).limit(1)
        .correlate(spec.thing).scalar_subquery()
    )
    return exists().where(*of_this_row), which


def _release_first_recorded(db: Session, spec: _Kind, scan_id: int) -> Tuple[int, int]:
    """Scripts, host scripts, host attributes: delete the rows only this scan
    reported, and hand the others it first recorded to the earliest scan that
    also reported them.

    A row this scan has no sighting of is not touched, nor is one that names
    another scan as its first recorder (the pointer is a witness too)."""
    thing, sighting = spec.thing, spec.sighting
    seen_elsewhere, earliest = _another_sighting(spec, scan_id)
    mine = select(getattr(sighting, spec.thing_column)).where(sighting.scan_id == scan_id)
    deleted = db.execute(
        delete(thing)
        .where(thing.id.in_(mine))
        .where(or_(thing.scan_id == scan_id, thing.scan_id.is_(None)))
        .where(~seen_elsewhere)
        .execution_options(synchronize_session=False)
    ).rowcount
    moved = db.execute(
        update(thing)
        .where(thing.scan_id == scan_id, seen_elsewhere)
        .values(scan_id=earliest)
        .execution_options(synchronize_session=False)
    ).rowcount
    return deleted, moved


def _release_vulnerabilities(db: Session, scan_id: int) -> Tuple[int, int]:
    """Move the cached first / last pointers that name this scan to the
    earliest / latest OTHER sighting, then delete the rows no other scan
    saw, unless a finding or a proposal refers to them: those stay, and the
    foreign keys clear their pointers when the scan row goes."""
    spec = _KINDS[VULNERABILITY]
    seen_elsewhere, earliest = _another_sighting(spec, scan_id)
    _, latest = _another_sighting(spec, scan_id, newest=True)
    moved = db.execute(
        update(Vulnerability)
        .where(Vulnerability.scan_id == scan_id, seen_elsewhere)
        .values(scan_id=earliest)
        .execution_options(synchronize_session=False)
    ).rowcount
    # A row with no sighting by another scan whose "last seen by" names one
    # (a row written before sightings were recorded for it): the pointer is
    # the record of that scan having seen it.
    moved += db.execute(
        update(Vulnerability)
        .where(
            Vulnerability.scan_id == scan_id,
            Vulnerability.last_seen_scan_id.isnot(None),
            Vulnerability.last_seen_scan_id != scan_id,
        )
        .values(scan_id=Vulnerability.last_seen_scan_id)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.execute(
        update(Vulnerability)
        .where(Vulnerability.last_seen_scan_id == scan_id, seen_elsewhere)
        .values(last_seen_scan_id=latest)
        .execution_options(synchronize_session=False)
    )
    deleted = db.execute(
        delete(Vulnerability)
        .where(removed_with_scan(scan_id))
        .execution_options(synchronize_session=False)
    ).rowcount
    return deleted, moved


def _rehome_confidence(db: Session, scan_id: int) -> int:
    """``host_confidence`` / ``port_confidence`` are "the current winner", not
    observations (their ``scan_id`` moves when a scan's value wins, and
    cascades): a row this scan holds goes to the newest LATER scan with
    history on its host / port, and is deleted with the scan otherwise."""
    moved = 0
    for model, parent, history in (
        (HostConfidence, "host_id", models.HostScanHistory),
        (PortConfidence, "port_id", models.PortScanHistory),
    ):
        other = aliased(history)
        later = [
            getattr(other, parent) == getattr(model, parent),
            other.scan_id != scan_id,
            other.scan_id > scan_id,
        ]
        target = select(func.max(other.scan_id)).where(*later).correlate(model).scalar_subquery()
        moved += db.execute(
            update(model)
            .where(model.scan_id == scan_id, exists().where(*later))
            .values(scan_id=target)
            .execution_options(synchronize_session=False)
        ).rowcount
    return moved


def _hand_over_introductions(db: Session, scan_id: int) -> None:
    """A host or port this scan CREATED and another scan has seen is now
    introduced by the first of those scans (``host_created`` /
    ``port_created``), so the Scans page does not count it as "already known"
    to every scan left."""
    HH, PH = models.HostScanHistory, models.PortScanHistory
    for history, parent_name, flag_name in ((HH, "host_id", "host_created"), (PH, "port_id", "port_created")):
        mine, other = aliased(history), aliased(history)
        heirs = (
            select(func.min(other.id))
            .join(mine, getattr(mine, parent_name) == getattr(other, parent_name))
            .where(mine.scan_id == scan_id, getattr(mine, flag_name).is_(True), other.scan_id != scan_id)
            .group_by(getattr(other, parent_name))
        )
        db.execute(
            update(history).where(history.id.in_(heirs)).values({flag_name: True})
            .execution_options(synchronize_session=False)
        )


def release_scan(db: Session, scan_id: int) -> Dict[str, int]:
    """Let go of everything ``scan_id`` reported, before its row is deleted.

    * Scripts, host scripts and host attributes ONLY this scan reported are
      deleted; the ones another scan also reported stay, and where this scan
      was their first recorder that becomes the earliest other scan that
      reported them.
    * Vulnerabilities: ``scan_id`` / ``last_seen_scan_id`` naming this scan
      move to the earliest / latest other sighting.  The rows no other scan
      saw are deleted (``removed_with_scan``), except one a finding or a
      proposal refers to, which is kept without a scan.
    * Confidence rows go to a later scan that observed their host / port, and
      the hosts and ports this scan introduced are handed to the first other
      scan that saw them.

    The scan's own sighting rows go with the scan row (CASCADE).  Statements
    only, no rows loaded; the caller holds whatever lock it needs and commits.
    Returns ``deleted`` (scripts / host scripts / attributes), ``rehomed``
    (rows whose first recorder changed) and ``observations`` (vulnerabilities
    deleted)."""
    deleted = rehomed = 0
    for name in (SCRIPT, HOST_SCRIPT, HOST_ATTRIBUTE):
        gone, moved = _release_first_recorded(db, _KINDS[name], scan_id)
        deleted += gone
        rehomed += moved
    observations, moved = _release_vulnerabilities(db, scan_id)
    rehomed += moved
    rehomed += _rehome_confidence(db, scan_id)
    _hand_over_introductions(db, scan_id)
    return {"deleted": deleted, "rehomed": rehomed, "observations": observations}
