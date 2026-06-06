"""
SOC-correlation activity surface — v2.56.0.

Answers the analyst question "what tools were running across our
projects at 14:32:15 UTC?" without making the caller iterate the
project list.  Two modes share one query shape:

    GET /api/v1/activity/scans-at?ts=<iso>&tolerance_seconds=300&project_ids=<csv>
    GET /api/v1/activity/scans-between?from=<iso>&to=<iso>&project_ids=<csv>

Auth scoping: the endpoint computes the set of projects the current
user can see (global ADMIN → all unarchived; otherwise via
ProjectMembership) and intersects it with the optional ``project_ids``
filter.  IDs the caller requested but doesn't have access to are
silently dropped — we don't leak existence of projects the user
isn't a member of.

NULL end_time handling: a scan that didn't record an end_time
(common for masscan ``--output-format list`` and some bare .gnmap
files) is treated as a single-instant event at ``start_time``.
That's deliberate — pre-treating NULL as open-ended would make every
historical scan with a missing end_time show up in every SOC query
that touched its start_time, drowning real signal.  Rows with a
missing end_time carry ``has_end_time=False`` so the UI can badge
them.

v2 (planned) folds in ``agent_api_calls`` and ``recon_sessions``
via a ``kinds=`` parameter; v1 ships scans-only.  The response shape
already carries a ``kind`` discriminator so v2 doesn't break the
contract.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import DateTime, and_, case, cast, func, or_
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models_agent import ExecutionSession, ReconSession, TestPlan
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectMembership
from app.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# Cap on returned rows.  500 is enough to surface the "which tools
# were running" question for any realistic SOC tolerance window
# (seconds-to-minutes).  An analyst who hits the cap is correlating
# against a window so wide that they want ``/activity/scans-between``
# anyway.
MAX_RESULTS = 500
# Maximum window for /scans-between.  Past 7d the analyst should use
# the per-project scan list, not the cross-project endpoint — bounded
# windows keep the page snappy and prevent runaway queries.
MAX_BETWEEN_WINDOW_DAYS = 7
# Hard cap on tolerance — past 1h, switch to /scans-between.
MAX_TOLERANCE_SECONDS = 3600


class ActivityItem(BaseModel):
    """One activity row whose time window overlapped the query window.

    Unified shape across scan / recon_session / execution_session.  The
    ``kind`` discriminator tells the UI which icon / label set to use.
    """

    kind: str  # "scan" | "recon_session" | "execution_session"
    # Reference id — scan_id for scans, session_id for recon/execution.
    # Kept as a single field so the table can render uniformly; the UI
    # builds the deep-link URL from (kind, id, project_id).
    ref_id: int
    project_id: int
    project_name: str
    # Human-readable primary label.  Scan: tool_name.  Recon: scope
    # name.  Execution: plan title.
    label: str
    # Optional secondary string for tooltip / details pane.  Scan:
    # truncated command_line.  Recon: notes summary.  Execution: mode.
    secondary_label: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    # v2.60.0 — `Scan.created_at` for scans only (sessions don't carry
    # a separate ingestion timestamp; their `started_at` already is the
    # row-creation time).  Always populated for scans; null for recon /
    # execution sessions.  The timeline anchor is still `start_time`;
    # this is metadata that lets the UI show "uploaded at X" alongside
    # the execution timestamp.
    recorded_time: Optional[datetime] = None
    # v2.61.0 — explicit "the value in `start_time` is the upload time,
    # not a scanner-recorded execution time" signal.  True only on the
    # NULL-`start_time` fallback path in `_query_scans`; always False
    # for scans with a real scanner timestamp and for sessions.  The UI
    # uses this to badge the timestamp (table cell + timeline tooltip)
    # so the analyst doesn't read upload time as execution time.
    start_time_is_fallback: bool = False
    has_end_time: bool
    # Host count for scans + recon_sessions; null for execution_sessions
    # (which don't directly map to host count).
    host_count: Optional[int] = None
    # Status string when kind in (recon_session, execution_session);
    # null for scans.
    status: Optional[str] = None


# Backward-compat alias kept for any old caller still using the v1
# response model name.  Same shape; the FastAPI client generator and
# our frontend now consume ActivityItem.
ActivityScanItem = ActivityItem


class ActivityResponse(BaseModel):
    items: List[ActivityItem]
    total: int  # number returned (≤ MAX_RESULTS)
    truncated: bool  # true if cap was hit
    accessible_project_ids: List[int]  # which projects the caller can see
    requested_project_ids: Optional[List[int]] = None
    # Echo the resolved window so the UI can label the result set.
    window_start: datetime
    window_end: datetime


def _accessible_project_ids(db: Session, user: User) -> List[int]:
    """Return the set of project ids the user can see.

    Global admins see every unarchived project; everyone else sees
    the projects they're a member of (any role).
    """
    if user.role == UserRole.ADMIN:
        return [
            pid
            for (pid,) in db.query(Project.id)
            .filter(Project.is_archived.is_(False))
            .all()
        ]
    return [
        pid
        for (pid,) in db.query(ProjectMembership.project_id)
        .join(Project, Project.id == ProjectMembership.project_id)
        .filter(
            ProjectMembership.user_id == user.id,
            Project.is_archived.is_(False),
        )
        .all()
    ]


def _parse_project_ids_csv(raw: Optional[str]) -> Optional[List[int]]:
    if not raw:
        return None
    out: List[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"project_ids must be a comma-separated list of integers; got {chunk!r}",
            )
    return out or None


def _to_utc(value: Optional[datetime], *, allow_none: bool = False) -> Optional[datetime]:
    """Coerce a datetime to UTC-aware.

    Used for two roles:
      * Request inputs (`allow_none=False`): FastAPI parses query
        datetimes as naive when the input lacks a timezone.  Treat
        naive input as UTC — the SOC use case is UTC-aligned and a
        naive timestamp is almost always copy-pasted from a UTC log
        line.  Aware-but-non-UTC values are converted to UTC.
      * ORM-returned values (`allow_none=True`): `Scan.created_at` is
        `DateTime(timezone=True)` but `start_time / end_time` are
        naive `DateTime`.  The fallback path in `_query_scans` mixes
        both into the same response items, and the cross-kind sort
        in `_query_in_window` compares them in Python — which
        TypeErrors on naive↔aware mismatch.  Naive values are tagged
        as UTC (matches what scanners write and what tests assume).
    """
    if value is None:
        if allow_none:
            return None
        raise TypeError("_to_utc received None without allow_none=True")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


KIND_SCAN = "scan"
KIND_RECON = "recon_session"
KIND_EXECUTION = "execution_session"
ALL_KINDS = {KIND_SCAN, KIND_RECON, KIND_EXECUTION}


def _parse_kinds(raw: Optional[str]) -> Set[str]:
    """Parse the optional ``kinds=`` CSV.  Default is all three.

    Unknown kind values raise 400 so a typo doesn't silently return an
    empty result set — the analyst would otherwise stare at a "no
    results" page wondering why their data isn't there.
    """
    if not raw:
        return set(ALL_KINDS)
    parts = {p.strip().lower() for p in raw.split(",") if p.strip()}
    unknown = parts - ALL_KINDS
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown kinds: {sorted(unknown)!r}. "
                f"Allowed: {sorted(ALL_KINDS)!r}."
            ),
        )
    return parts


def _query_scans(
    db: Session,
    project_ids: List[int],
    window_start: datetime,
    window_end: datetime,
) -> List[ActivityItem]:
    # v2.60.0 — fall back to `created_at` ONLY when the scanner didn't
    # write a `start_time` (some `.txt` exports, bare masscan list
    # output, certain `.gnmap` variants).  Without this fallback those
    # scans were silently excluded by the prior `start_time IS NOT
    # NULL` filter.  Scans WITH a real `start_time` are still anchored
    # on tool-execution time — the SOC question is "what was running
    # when?", not "what got uploaded when?".  `start_time_is_fallback`
    # on the response row signals which path produced the timestamp.
    #
    # v2.62.0 — split the WHERE clause by whether the scanner wrote a
    # real `start_time`, so the comparison types are unambiguous on
    # every branch:
    #
    #   * real-start branch: `Scan.start_time` (naive `timestamp`) is
    #     compared against UTC-naive bounds.  Pre-existing semantics
    #     — the app's convention is that `Scan.start_time` is stored
    #     as UTC wall-clock.
    #   * fallback branch: `Scan.created_at` (`timestamptz`) is
    #     compared against UTC-aware bounds.  No cast, no implicit
    #     promotion, no PostgreSQL-session-timezone dependence.  The
    #     prior `cast(created_at, DateTime)` route was only correct
    #     when the DB session ran in UTC.
    window_start_naive = window_start.replace(tzinfo=None)
    window_end_naive = window_end.replace(tzinfo=None)

    # Asymmetric by design: the real-start branch is overlap-based
    # (`[start, COALESCE(end, start)] ∩ window`); the fallback branch
    # is point-in-range, because a NULL-start scan has no end-time
    # candidate and is treated as a single-instant event at
    # `created_at`.  If we later add a parsed-end-time or
    # duration-estimate fallback for NULL-start scans, revisit this.
    #
    # ⚠ Test-coverage gap (Postgres-only).  The split-predicate
    # rewrite below exists to remove a Postgres-session-TZ dependence
    # that the v2.61.0 `cast(created_at, DateTime)` form had.  The
    # backend test suite runs against SQLite, which doesn't
    # distinguish `timestamp` and `timestamptz`, so a regression that
    # reintroduced the cast-based form would pass CI.  A
    # testcontainers harness under non-UTC `SET TIME ZONE` is the
    # right long-term coverage; until then this comment is the
    # canary.  See also `tests/test_activity.py` (v2.62.0 block).
    real_start_filter = and_(
        models.Scan.start_time.isnot(None),
        models.Scan.start_time <= window_end_naive,
        func.coalesce(models.Scan.end_time, models.Scan.start_time)
        >= window_start_naive,
    )
    fallback_filter = and_(
        models.Scan.start_time.is_(None),
        models.Scan.created_at <= window_end,
        models.Scan.created_at >= window_start,
    )

    host_count_sq = (
        db.query(
            models.HostScanHistory.scan_id.label("scan_id"),
            func.count(models.HostScanHistory.host_id).label("host_count"),
        )
        .group_by(models.HostScanHistory.scan_id)
        .subquery()
    )

    # ORDER BY governs only which `MAX_RESULTS + 1` rows survive the
    # cap; the final cross-kind order is produced by the Python-side
    # sort in `_query_in_window`.  Ordering on `Scan.start_time` alone
    # would put every NULL-start row at the tail (NULLS LAST is the
    # Postgres default for DESC), which means a NULL-start row that
    # belongs in the result could be dropped when there are more
    # matches than the cap.  So we still COALESCE — but the cast is
    # explicit (and TZ-imprecise under a non-UTC Postgres session).
    # In the pathological case (>500 matches, mixed NULL-start and
    # real-start, ranking near the cap boundary), a TZ-shifted
    # `created_at` ranking could displace real-start rows from the
    # truncated set — they're still in the window, just dropped from
    # the response.  The truncation flag is the user-facing signal
    # that this happened.
    order_expr = func.coalesce(
        models.Scan.start_time, cast(models.Scan.created_at, DateTime)
    ).desc()

    rows = (
        db.query(
            models.Scan.id,
            models.Scan.project_id,
            Project.name,
            models.Scan.tool_name,
            models.Scan.scan_type,
            models.Scan.start_time,
            models.Scan.end_time,
            models.Scan.created_at,
            models.Scan.command_line,
            func.coalesce(host_count_sq.c.host_count, 0).label("host_count"),
        )
        .join(Project, Project.id == models.Scan.project_id)
        .outerjoin(host_count_sq, host_count_sq.c.scan_id == models.Scan.id)
        .filter(
            models.Scan.project_id.in_(project_ids),
            or_(real_start_filter, fallback_filter),
        )
        .order_by(order_expr)
        .limit(MAX_RESULTS + 1)
        .all()
    )

    return [
        ActivityItem(
            kind=KIND_SCAN,
            ref_id=row[0],
            project_id=row[1],
            project_name=row[2],
            label=row[3] or row[4] or "scan",
            secondary_label=(row[8][:200] + "…")
            if (row[8] and len(row[8]) > 200)
            else row[8],
            # `start_time` is required by the Pydantic contract.  When
            # the scanner didn't write one, substitute `created_at` so
            # the response is well-formed and surface that fact via
            # `start_time_is_fallback`.  `_to_utc(..., allow_none=True)`
            # normalises naive `Scan.start_time` / `Scan.end_time` to
            # UTC-aware so the cross-kind sort in `_query_in_window`
            # doesn't TypeError when comparing scan rows against
            # session rows (which are already aware via
            # `DateTime(timezone=True)`).
            start_time=_to_utc(row[5] or row[7], allow_none=True),
            end_time=_to_utc(row[6], allow_none=True),
            recorded_time=_to_utc(row[7], allow_none=True),
            start_time_is_fallback=row[5] is None,
            has_end_time=row[6] is not None,
            host_count=int(row[9] or 0),
        )
        for row in rows
    ]


def _query_recon_sessions(
    db: Session,
    project_ids: List[int],
    window_start: datetime,
    window_end: datetime,
) -> List[ActivityItem]:
    """ReconSession rows whose [started_at, COALESCE(completed_at, started_at)]
    overlaps the window.  Same single-instant interpretation for NULL
    completed_at as for scans.
    """
    effective_end = func.coalesce(
        ReconSession.completed_at, ReconSession.started_at
    )
    rows = (
        db.query(
            ReconSession.id,
            ReconSession.project_id,
            Project.name,
            models.Scope.name.label("scope_name"),
            ReconSession.notes,
            ReconSession.started_at,
            ReconSession.completed_at,
            ReconSession.status,
            ReconSession.hosts_discovered,
        )
        .join(Project, Project.id == ReconSession.project_id)
        .join(models.Scope, models.Scope.id == ReconSession.scope_id)
        .filter(
            ReconSession.started_at.isnot(None),
            ReconSession.project_id.in_(project_ids),
            ReconSession.started_at <= window_end,
            effective_end >= window_start,
        )
        .order_by(ReconSession.started_at.desc())
        .limit(MAX_RESULTS + 1)
        .all()
    )
    return [
        ActivityItem(
            kind=KIND_RECON,
            ref_id=row[0],
            project_id=row[1],
            project_name=row[2],
            label=f"recon: {row[3]}" if row[3] else "recon",
            secondary_label=(row[4][:200] + "…")
            if (row[4] and len(row[4]) > 200)
            else row[4],
            start_time=row[5],
            end_time=row[6],
            has_end_time=row[6] is not None,
            host_count=int(row[8] or 0),
            status=row[7],
        )
        for row in rows
    ]


def _query_execution_sessions(
    db: Session,
    project_ids: List[int],
    window_start: datetime,
    window_end: datetime,
) -> List[ActivityItem]:
    """ExecutionSession rows.  Project is reached via TestPlan."""
    effective_end = func.coalesce(
        ExecutionSession.completed_at, ExecutionSession.started_at
    )
    rows = (
        db.query(
            ExecutionSession.id,
            TestPlan.project_id,
            Project.name,
            TestPlan.title,
            ExecutionSession.mode,
            ExecutionSession.started_at,
            ExecutionSession.completed_at,
            ExecutionSession.status,
        )
        .join(TestPlan, TestPlan.id == ExecutionSession.test_plan_id)
        .join(Project, Project.id == TestPlan.project_id)
        .filter(
            ExecutionSession.started_at.isnot(None),
            TestPlan.project_id.in_(project_ids),
            ExecutionSession.started_at <= window_end,
            effective_end >= window_start,
        )
        .order_by(ExecutionSession.started_at.desc())
        .limit(MAX_RESULTS + 1)
        .all()
    )
    return [
        ActivityItem(
            kind=KIND_EXECUTION,
            ref_id=row[0],
            project_id=row[1],
            project_name=row[2],
            label=f"execution: {row[3]}" if row[3] else "execution",
            secondary_label=row[4],
            start_time=row[5],
            end_time=row[6],
            has_end_time=row[6] is not None,
            host_count=None,
            status=row[7],
        )
        for row in rows
    ]


def _query_in_window(
    db: Session,
    project_ids: List[int],
    window_start: datetime,
    window_end: datetime,
    kinds: Set[str],
) -> List[ActivityItem]:
    """Run each enabled per-kind query and interleave by start_time
    desc.  Each per-kind query is bounded by MAX_RESULTS + 1; the
    union is then re-sorted and clipped to MAX_RESULTS + 1 so the
    caller-level truncation flag still has correct semantics.
    """
    items: List[ActivityItem] = []
    if KIND_SCAN in kinds:
        items.extend(_query_scans(db, project_ids, window_start, window_end))
    if KIND_RECON in kinds:
        items.extend(
            _query_recon_sessions(db, project_ids, window_start, window_end)
        )
    if KIND_EXECUTION in kinds:
        items.extend(
            _query_execution_sessions(db, project_ids, window_start, window_end)
        )
    items.sort(key=lambda i: i.start_time, reverse=True)
    return items[: MAX_RESULTS + 1]


@router.get("/scans-at", response_model=ActivityResponse)
def scans_at(
    ts: datetime = Query(
        ..., description="ISO-8601 timestamp to correlate against. Naive timestamps assumed UTC."
    ),
    tolerance_seconds: int = Query(
        300,
        ge=0,
        le=MAX_TOLERANCE_SECONDS,
        description=f"± window in seconds; max {MAX_TOLERANCE_SECONDS} (1h). "
        "Use /scans-between for longer windows.",
    ),
    project_ids: Optional[str] = Query(
        None,
        description="Optional CSV of project ids to filter. Omit to query "
        "every project the caller can see. IDs the caller can't see are "
        "silently dropped — no project existence leak.",
    ),
    kinds: Optional[str] = Query(
        None,
        description="Optional CSV of activity kinds to include: "
        "`scan`, `recon_session`, `execution_session`. "
        "Omit for all three.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Point query: list activity whose window covered `ts` (± tolerance)."""
    ts_utc = _to_utc(ts)
    window_start = ts_utc - timedelta(seconds=tolerance_seconds)
    window_end = ts_utc + timedelta(seconds=tolerance_seconds)
    kinds_set = _parse_kinds(kinds)

    accessible = _accessible_project_ids(db, current_user)
    if not accessible:
        return ActivityResponse(
            items=[],
            total=0,
            truncated=False,
            accessible_project_ids=[],
            requested_project_ids=_parse_project_ids_csv(project_ids),
            window_start=window_start,
            window_end=window_end,
        )

    requested = _parse_project_ids_csv(project_ids)
    if requested is not None:
        accessible_set = set(accessible)
        effective = [pid for pid in requested if pid in accessible_set]
    else:
        effective = accessible

    if not effective:
        return ActivityResponse(
            items=[],
            total=0,
            truncated=False,
            accessible_project_ids=accessible,
            requested_project_ids=requested,
            window_start=window_start,
            window_end=window_end,
        )

    items = _query_in_window(db, effective, window_start, window_end, kinds_set)
    truncated = len(items) > MAX_RESULTS
    if truncated:
        items = items[:MAX_RESULTS]

    return ActivityResponse(
        items=items,
        total=len(items),
        truncated=truncated,
        accessible_project_ids=accessible,
        requested_project_ids=requested,
        window_start=window_start,
        window_end=window_end,
    )


@router.get("/scans-between", response_model=ActivityResponse)
def scans_between(
    from_: datetime = Query(
        ...,
        alias="from",
        description="ISO-8601 lower bound (inclusive). Naive timestamps assumed UTC.",
    ),
    to: datetime = Query(
        ...,
        description="ISO-8601 upper bound (inclusive). Naive timestamps assumed UTC.",
    ),
    project_ids: Optional[str] = Query(None),
    kinds: Optional[str] = Query(
        None,
        description="Optional CSV of activity kinds; see /scans-at.",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Window query: list every scan whose window overlaps [from, to].

    The window is capped at MAX_BETWEEN_WINDOW_DAYS days — longer
    correlations should drive off the per-project scan list, not this
    cross-project surface (the result cap would clip most of the data
    anyway).
    """
    from_utc = _to_utc(from_)
    to_utc = _to_utc(to)
    kinds_set = _parse_kinds(kinds)
    if to_utc < from_utc:
        raise HTTPException(
            status_code=400, detail="`to` must be on or after `from`"
        )
    if (to_utc - from_utc).days > MAX_BETWEEN_WINDOW_DAYS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"window must be ≤ {MAX_BETWEEN_WINDOW_DAYS} days; for longer "
                "ranges use the per-project scan list."
            ),
        )

    accessible = _accessible_project_ids(db, current_user)
    if not accessible:
        return ActivityResponse(
            items=[],
            total=0,
            truncated=False,
            accessible_project_ids=[],
            requested_project_ids=_parse_project_ids_csv(project_ids),
            window_start=from_utc,
            window_end=to_utc,
        )

    requested = _parse_project_ids_csv(project_ids)
    if requested is not None:
        accessible_set = set(accessible)
        effective = [pid for pid in requested if pid in accessible_set]
    else:
        effective = accessible

    if not effective:
        return ActivityResponse(
            items=[],
            total=0,
            truncated=False,
            accessible_project_ids=accessible,
            requested_project_ids=requested,
            window_start=from_utc,
            window_end=to_utc,
        )

    items = _query_in_window(db, effective, from_utc, to_utc, kinds_set)
    truncated = len(items) > MAX_RESULTS
    if truncated:
        items = items[:MAX_RESULTS]

    return ActivityResponse(
        items=items,
        total=len(items),
        truncated=truncated,
        accessible_project_ids=accessible,
        requested_project_ids=requested,
        window_start=from_utc,
        window_end=to_utc,
    )
