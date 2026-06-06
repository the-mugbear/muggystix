"""
Recon-session detail (v3 alpha.6) — JWT-authenticated, human-facing.

The agent surface (``/agent/recon/*``) is API-key authenticated and
designed for one specific recon session at a time.  The v3 UI's
Recon Run Detail page needs a different shape: arbitrary session by
ID, project-scoped, with the same per-host breakdown that the agent
sees plus the new "plans generated from this recon" link (alpha.3
introduced ``TestPlan.source_recon_session_id``).

Endpoints:

* ``GET /projects/{id}/recon-sessions/`` — list every recon session
  in the project, newest-started first.  Optional ``status`` filter.
* ``GET /projects/{id}/recon-sessions/{session_id}`` — full bundle
  for one session: metadata + counts + per-upload ingestion stats +
  hosts discovered (reuses the agent-facing breakdown helper) +
  plans whose ``source_recon_session_id`` matches this run.

Why not extend the existing ``/scopes/{id}`` surface?  Recon sessions
are per-scope but multiple sessions can exist under one scope; the
v3 nav reaches them as first-class items ("/recon/runs/:id") not as
sub-resources.  A dedicated read surface matches that mental model
and keeps the agent vs human auth chains physically separate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.schemas.pagination import Paginated
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, require_project_role, is_project_admin
from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models_agent import (
    AgentApiCall,
    ReconSession,
    ReconSessionStatus,
    TestPlan,
)
from app.db.models_auth import User, UserRole
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.services.recon_summary_service import (
    recon_session_diff_ips,
    recon_session_host_breakdown,
    recon_session_host_stats,
)


router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ReconSessionRow(BaseModel):
    """Summary row for the list endpoint."""
    id: int
    project_id: int
    scope_id: int
    scope_name: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    started_by_username: Optional[str] = None
    agent_name: Optional[str] = None
    # v2.30.0 attribution columns.
    generated_by_model: Optional[str] = None
    generated_by_tool: Optional[str] = None
    prompt_version: Optional[str] = None
    # The session's own cached counters (refreshed by the agent
    # /recon/summary call; may be slightly stale until the next
    # summary call).
    uploads_submitted: int = 0
    scans_ingested: int = 0
    hosts_discovered: int = 0
    ports_discovered: int = 0
    # Timestamp of the most recent agent API call against this recon
    # session (from the agent_api_calls audit log), or None if the
    # agent never called in.  Mirrors the execution-session field of
    # the same name.
    last_activity_at: Optional[datetime] = None
    # Server-side "looks interrupted" judgment — true when the session
    # is ``active`` AND has been silent for ``_STALE_THRESHOLD_SECONDS``.
    # Now uses ``max(last_activity_at, started_at)`` as the reference,
    # mirroring execution: the previous shipped version compared
    # against ``started_at`` alone, which fired a false positive on
    # any long-running session even while the agent was actively
    # calling in.  Computed server-side so the UI's "Possibly
    # interrupted" badge is immune to operator/server clock skew.
    is_stale: bool = False


class ReconUploadRow(BaseModel):
    """One IngestionJob submitted under this recon session.

    Surfaces enough for the UI to render a per-upload status table:
    filename, status, skipped count + warnings (v2.22.0 ingestion
    quality columns), and which Scan row it produced.
    """
    job_id: int
    filename: str
    status: str
    scan_id: Optional[int] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    skipped_count: int = 0
    parser_warnings: Optional[str] = None
    last_error: Optional[str] = None


class ReconHostRow(BaseModel):
    """Per-host breakdown row.

    Mirrors the agent-facing ``ReconHostBrief`` shape but stripped to
    what the UI's table renders so the human and agent surfaces can
    evolve independently if one needs richer detail later.
    """
    host_id: int
    ip_address: str
    hostname: Optional[str] = None
    open_port_count: int = 0
    open_ports: List[int] = Field(default_factory=list)
    services: List[str] = Field(default_factory=list)


class ReconPlanLink(BaseModel):
    """A test plan whose ``source_recon_session_id`` matches this run.

    Closes the provenance loop: from a recon run, see which plans
    were drafted from its output.
    """
    plan_id: int
    title: str
    status: str
    version: int
    entry_count: int = 0
    created_at: datetime
    generated_by_model: Optional[str] = None


class ReconEnvironmentSnapshot(BaseModel):
    """Operator-environment snapshot captured at the start of the
    recon session (and re-posted with `tools_status` after preflight).

    Surfaces what the agent saw so the operator can audit it from the
    UI without raw-SQL — answers questions like "which tools were
    missing on this run", "did the agent know about my Kali shell",
    "did preflight flag httpx".  Sourced from
    `recon_sessions.environment` (JSONB) plus the dedicated audit
    columns (`environment_probed_at` etc.) so callers don't have to
    parse the JSON to know whether a probe ever arrived.
    """
    probed_at: Optional[datetime] = None
    probed_from_ip: Optional[str] = None
    # Hoisted out of the JSON for stable typing; the raw body lives in
    # `raw` so callers can show details that aren't part of the
    # canonical EnvironmentSummary contract.
    os_family: Optional[str] = None
    os_release: Optional[str] = None
    shell: Optional[str] = None
    arch: Optional[str] = None
    python: Optional[str] = None
    notes: Optional[str] = None
    tools_status: List[Dict[str, Any]] = Field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None


class ReconToolBreakdown(BaseModel):
    """One row of the per-tool aggregate produced by a recon session.

    ``host_count`` is distinct hosts the tool touched in this session
    (so a host scanned by both nmap and httpx counts once under each).
    """
    tool_name: str
    scan_count: int
    host_count: int
    port_count: int


class ReconServiceBreakdown(BaseModel):
    """Top-N service rollup (one row per service_name)."""
    service_name: str
    host_count: int


class ReconPortBreakdown(BaseModel):
    """Top-N open-port rollup (one row per (port_number, protocol))."""
    port_number: int
    protocol: str
    host_count: int


class ReconHostStats(BaseModel):
    """Aggregate stats for a recon session.

    Replaces the per-host list on the Recon Run Detail page (v2.52.0).
    Cheap to compute regardless of host count — all five fields are
    GROUP BY queries that return at most ~10 rows, so the run page
    renders in <100ms even for sessions with tens of thousands of
    hosts.
    """
    host_count: int = 0
    host_count_with_open_ports: int = 0
    by_tool: List[ReconToolBreakdown] = Field(default_factory=list)
    top_services: List[ReconServiceBreakdown] = Field(default_factory=list)
    top_open_ports: List[ReconPortBreakdown] = Field(default_factory=list)


class ReconSessionDetail(BaseModel):
    """Full detail bundle for one recon session.

    v2.52.0 — ``hosts`` is opt-in via ``?include_hosts=true``.  The
    Recon Run Detail page renders ``host_stats`` (cheap rollup) and
    links to /inventory for the full host list; the only remaining
    consumer that needs the host array is the per-host comparison
    fallback in the diff endpoint, which loads it on demand.  Older
    callers that hit the endpoint without the query param get the
    empty list — the contract change is breaking-but-bounded:
    operators on the two affected pages will see the stats payload
    immediately after deploy; agents don't read this surface.

    v2.87.0 — ``uploads`` + ``plans_generated`` paginated.  Pre-fix
    both lists were ``.all()`` so a recon run that uploaded hundreds
    of files (live tcpdump captures, batched httpx output) or that
    drafted dozens of plans against the same session shipped every
    child row on every detail load.  Now caller-controlled via
    ``uploads_skip`` / ``uploads_limit`` / ``plans_skip`` /
    ``plans_limit``; the ``*_total`` fields let the page render
    "showing N of T".  ``all_scan_ids`` rides separately so the
    Inventory deep-link can target every scan from this run
    regardless of which uploads page is currently visible.
    """
    summary: ReconSessionRow
    uploads: List[ReconUploadRow] = Field(default_factory=list)
    uploads_total: int = 0
    uploads_skip: int = 0
    uploads_limit: int = 0
    host_stats: ReconHostStats = Field(default_factory=ReconHostStats)
    hosts: List[ReconHostRow] = Field(default_factory=list)
    plans_generated: List[ReconPlanLink] = Field(default_factory=list)
    plans_total: int = 0
    plans_skip: int = 0
    plans_limit: int = 0
    # v2.87.0 — full set of scan IDs this session produced.  Always
    # populated (cheap query); decouples the Inventory CTA from the
    # paginated ``uploads`` slice.
    all_scan_ids: List[int] = Field(default_factory=list)
    # v2.40.2 — operator environment snapshot.  Null when no probe
    # was ever posted; populated with the merged Step 0 + Step 1
    # body once the agent has run preflight and re-posted.
    environment: Optional[ReconEnvironmentSnapshot] = None


class ReconDiffHostRow(BaseModel):
    """Minimal host identifier for the per-session-diff sample list."""
    host_id: int
    ip_address: str
    hostname: Optional[str] = None


class ReconSessionDiff(BaseModel):
    """Pairwise diff of two recon sessions in the same project.

    Carries the per-side stats so the comparison view can render a
    delta panel without a second round-trip, plus capped samples of
    the IP set difference.  The full uncapped counts (``*_count``)
    are always present so the UI can say "showing 50 of 312 new
    hosts — view all in Inventory".
    """
    session_a_id: int
    session_b_id: int
    stats_a: ReconHostStats
    stats_b: ReconHostStats
    in_a_not_b_count: int
    in_b_not_a_count: int
    shared_count: int
    in_a_not_b_sample: List[ReconDiffHostRow] = Field(default_factory=list)
    in_b_not_a_sample: List[ReconDiffHostRow] = Field(default_factory=list)
    limit: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Inactivity window after which an ``active`` recon session is treated
# as "looks interrupted".  Mirrors the execution-session staleness in
# test_plans.py — same rationale (computed server-side so the threshold
# crossing is immune to client/server clock skew) and same threshold so
# both workflows behave consistently.
_STALE_THRESHOLD_SECONDS = 15 * 60


def _recon_is_stale(
    session: ReconSession, last_activity_at: Optional[datetime] = None
) -> bool:
    """``active`` + silent for longer than the threshold = looks
    interrupted.  References the most recent agent API call when one
    exists (the right signal for "the agent has gone quiet"), falling
    back to ``started_at`` for sessions that have never been touched.
    """
    if session.status != ReconSessionStatus.ACTIVE.value:
        return False
    ref = last_activity_at or session.started_at
    if ref is None:
        return False
    # Defend against legacy tz-naive rows by treating them as UTC.
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ref).total_seconds() > _STALE_THRESHOLD_SECONDS


def _row_from_session(
    session: ReconSession,
    scope_name: Optional[str] = None,
    last_activity_at: Optional[datetime] = None,
) -> ReconSessionRow:
    """Project a ReconSession ORM row into the API response shape.

    ``scope_name`` is resolved by the caller (the model doesn't expose
    a ``scope`` relationship; doing so here would force a per-row
    lookup that defeats the list endpoint's batching).
    """
    return ReconSessionRow(
        id=session.id,
        project_id=session.project_id,
        scope_id=session.scope_id,
        scope_name=scope_name,
        status=session.status,
        started_at=session.started_at,
        completed_at=session.completed_at,
        started_by_username=(
            session.started_by.username if session.started_by else None
        ),
        agent_name=session.agent.name if session.agent else None,
        generated_by_model=session.generated_by_model,
        generated_by_tool=session.generated_by_tool,
        prompt_version=session.prompt_version,
        uploads_submitted=session.uploads_submitted or 0,
        scans_ingested=session.scans_ingested or 0,
        hosts_discovered=session.hosts_discovered or 0,
        ports_discovered=session.ports_discovered or 0,
        last_activity_at=last_activity_at,
        is_stale=_recon_is_stale(session, last_activity_at),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/",
    response_model=Paginated[ReconSessionRow],
    summary="List recon sessions for this project (v3 alpha.6)",
)
def list_recon_sessions(
    project_id: int = Path(..., gt=0),
    status: Optional[str] = Query(
        None,
        description=(
            "Filter by ReconSessionStatus — 'active' / 'completed' / "
            "'failed' / 'abandoned'.  Omit to get all."
        ),
    ),
    scope_id: Optional[int] = Query(
        None, gt=0, description="Filter to one scope's recon runs."
    ),
    search: Optional[str] = Query(
        None,
        max_length=200,
        description=(
            "Case-insensitive substring match on generated_by_model + "
            "generated_by_tool + prompt_version (v2.86.10)."
        ),
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return the project's recon runs, newest-started first.

    Lighter shape than the detail endpoint; used by Operations'
    "Active runs" link-out and any future "all recon runs for this
    project" list view.

    v2.86.10 — paginated.  ``skip`` and ``limit`` advance the page.

    v2.86.13 — response shape standardised on ``Paginated[T]``
    (``{items, total, skip, limit, has_more}``).  The previous
    ``X-Total-Count`` header has been retired here; the same total
    figure is in ``response.total`` and the convenient ``has_more``
    flag is computed server-side.
    """
    q = (
        db.query(ReconSession)
        .filter(ReconSession.project_id == project.id)
    )
    if status:
        q = q.filter(ReconSession.status == status)
    if scope_id is not None:
        q = q.filter(ReconSession.scope_id == scope_id)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        q = q.filter(
            or_(
                ReconSession.generated_by_model.ilike(like),
                ReconSession.generated_by_tool.ilike(like),
                ReconSession.prompt_version.ilike(like),
            )
        )

    total = q.with_entities(func.count(ReconSession.id)).scalar() or 0
    rows = (
        q.order_by(ReconSession.started_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    # Batch-resolve scope names so we don't N+1 across the list.
    scope_ids = {r.scope_id for r in rows if r.scope_id is not None}
    name_by_id: dict = {}
    if scope_ids:
        name_by_id = dict(
            db.query(models.Scope.id, models.Scope.name)
            .filter(models.Scope.id.in_(scope_ids))
            .all()
        )
    # Batch-resolve last agent-API-call timestamp per session — one
    # GROUP BY against the (recon_session_id, created_at) index.  Feeds
    # both the displayed "last activity" and the server-side staleness
    # judgment so the "Possibly interrupted" badge fires on the right
    # signal (silence) instead of session age alone.
    session_ids = [r.id for r in rows]
    activity_by_id: Dict[int, datetime] = {}
    if session_ids:
        activity_by_id = dict(
            db.query(
                AgentApiCall.recon_session_id,
                func.max(AgentApiCall.created_at),
            )
            .filter(AgentApiCall.recon_session_id.in_(session_ids))
            .group_by(AgentApiCall.recon_session_id)
            .all()
        )
    items = [
        _row_from_session(
            r,
            scope_name=name_by_id.get(r.scope_id),
            last_activity_at=activity_by_id.get(r.id),
        )
        for r in rows
    ]
    return Paginated[ReconSessionRow].build(
        items=items, total=total, skip=skip, limit=limit,
    )


@router.get(
    "/{session_id}",
    response_model=ReconSessionDetail,
    summary="Full detail for one recon session (v3 alpha.6)",
)
def get_recon_session(
    project_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    include_hosts: bool = Query(
        False,
        description=(
            "Include the per-host array (~10-30 MB on large sessions). "
            "The Recon Run Detail page reads ``host_stats`` instead — "
            "this flag is for the comparison/diff view's fallback path "
            "and one-off integrations that need the raw list."
        ),
    ),
    # v2.87.0 — child-list pagination.  Default page size is generous
    # enough that most sessions fit on page 1; the cap protects against
    # a runaway request from accidentally streaming thousands of rows.
    uploads_skip: int = Query(0, ge=0),
    uploads_limit: int = Query(50, ge=1, le=500),
    plans_skip: int = Query(0, ge=0),
    plans_limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return the full detail bundle for one recon session.

    Distinguishes "doesn't exist" from "exists in another project"
    so a cross-project link doesn't dead-end silently, matching the
    pattern used by ``get_test_plan``.

    v2.52.0 — ``hosts`` array is now opt-in via ``include_hosts``.
    The default (omit the array, always include ``host_stats``)
    keeps the Recon Run Detail page responsive on 40k+ host sessions.
    """
    session = (
        db.query(ReconSession)
        .filter(
            ReconSession.id == session_id,
            ReconSession.project_id == project.id,
        )
        .first()
    )
    if not session:
        other = (
            db.query(ReconSession.project_id)
            .filter(ReconSession.id == session_id)
            .first()
        )
        if other:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Recon session #{session_id} belongs to a different "
                    f"project (project #{other[0]}). Switch to that "
                    f"project to view it."
                ),
            )
        raise HTTPException(status_code=404, detail="Recon session not found")

    scope_name_row = (
        db.query(models.Scope.name)
        .filter(models.Scope.id == session.scope_id)
        .first()
    )
    last_activity = (
        db.query(func.max(AgentApiCall.created_at))
        .filter(AgentApiCall.recon_session_id == session.id)
        .scalar()
    )
    summary = _row_from_session(
        session,
        scope_name=scope_name_row[0] if scope_name_row else None,
        last_activity_at=last_activity,
    )

    # Uploads + per-upload ingest stats.  ``recon_session_id`` was
    # added to ``IngestionJob`` in v2.11.0 specifically so the recon
    # workflow could enumerate the jobs it produced.
    # v2.87.0 — paginated.  ``uploads_total`` ships separately so the
    # page can render "showing N of T" even when ``uploads`` is a
    # capped slice; ``all_scan_ids`` (computed below) keeps the
    # Inventory CTA deep-linking to every scan from this run.
    uploads_total = (
        db.query(func.count(models.IngestionJob.id))
        .filter(models.IngestionJob.recon_session_id == session.id)
        .scalar()
        or 0
    )
    upload_rows = (
        db.query(models.IngestionJob)
        .filter(models.IngestionJob.recon_session_id == session.id)
        .order_by(models.IngestionJob.created_at.desc())
        .offset(uploads_skip)
        .limit(uploads_limit)
        .all()
    )
    uploads = [
        ReconUploadRow(
            job_id=j.id,
            filename=j.filename,
            status=j.status,
            scan_id=j.scan_id,
            created_at=j.created_at,
            completed_at=j.completed_at,
            skipped_count=j.skipped_count or 0,
            parser_warnings=j.parser_warnings,
            last_error=j.last_error,
        )
        for j in upload_rows
    ]

    # Decoupled from the paginated uploads slice so the Inventory CTA
    # (``/hosts?scan_ids=...``) targets every scan this run produced,
    # not just the page currently rendered.  Cheap: scan_id is
    # indexed on ingestion_jobs and the result set is small (~1 per
    # parsed file).
    all_scan_ids = [
        row[0]
        for row in (
            db.query(models.IngestionJob.scan_id)
            .filter(
                models.IngestionJob.recon_session_id == session.id,
                models.IngestionJob.scan_id.isnot(None),
            )
            .all()
        )
    ]

    # Cheap aggregate that always rides on the response — replaces the
    # per-host list as the run-detail page's "what did this produce"
    # surface.  Six GROUP BY queries; doesn't scale with host count.
    host_stats = ReconHostStats(**recon_session_host_stats(db, session.id))

    # Per-host breakdown — only built when the caller opts in.  Skipping
    # this on the default path is what makes a 40k-host session render
    # in <100ms instead of pinning the worker on Pydantic serialisation
    # of tens of thousands of rows.
    hosts: List[ReconHostRow] = []
    if include_hosts:
        agent_breakdown = recon_session_host_breakdown(db, session.id)
        hosts = [
            ReconHostRow(
                host_id=h.host_id,
                ip_address=h.ip_address,
                hostname=h.hostname,
                open_port_count=h.open_port_count or 0,
                open_ports=[p.port for p in (h.open_ports or [])],
                services=[
                    p.service for p in (h.open_ports or [])
                    if p.service
                ],
            )
            for h in agent_breakdown
        ]

    # Plans drafted from this recon, via the alpha.3
    # ``source_recon_session_id`` FK.  Older plans (pre-alpha.3) carry
    # ``source_kind='unspecified'`` and won't appear here — that's the
    # honest answer, not a regression.
    # v2.87.0 — paginated.  ``plans_total`` ships separately so the
    # page can render "showing N of T" even on the capped slice.
    plans_total = (
        db.query(func.count(TestPlan.id))
        .filter(TestPlan.source_recon_session_id == session.id)
        .scalar()
        or 0
    )
    plan_rows = (
        db.query(TestPlan)
        .filter(TestPlan.source_recon_session_id == session.id)
        .order_by(TestPlan.created_at.desc())
        .offset(plans_skip)
        .limit(plans_limit)
        .all()
    )
    plan_links: List[ReconPlanLink] = []
    if plan_rows:
        plan_ids = [p.id for p in plan_rows]
        # Entry counts in one batch query so a plan with no entries
        # still renders with entry_count=0.
        from app.db.models_agent import TestPlanEntry
        entry_counts = dict(
            db.query(
                TestPlanEntry.test_plan_id,
                func.count(TestPlanEntry.id),
            )
            .filter(TestPlanEntry.test_plan_id.in_(plan_ids))
            .group_by(TestPlanEntry.test_plan_id)
            .all()
        )
        plan_links = [
            ReconPlanLink(
                plan_id=p.id,
                title=p.title,
                status=p.status,
                version=p.version,
                entry_count=entry_counts.get(p.id, 0),
                created_at=p.created_at,
                generated_by_model=p.generated_by_model,
            )
            for p in plan_rows
        ]

    # v2.40.2 — operator-environment snapshot.  Only surface when a
    # probe has actually arrived; otherwise return null so the UI
    # renders an "agent did not post a probe" affordance instead of
    # showing empty fields that look like real data.
    env_snapshot: Optional[ReconEnvironmentSnapshot] = None
    raw_env = session.environment if isinstance(session.environment, dict) else None
    if raw_env or session.environment_probed_at:
        # v2.44.4 — defensive tools_status coercion.  The canonical
        # shape (per recon_planning_service docstring) is a list of
        # {name, status, issue} dicts mirroring preflight.sh's
        # .tools[] output.  But agents sometimes reshape into a dict
        # keyed by name ({"nmap": {...}, ...}) because that's easier
        # to consume client-side, and the env-probe write path
        # accepted whichever shape arrived (no coercion before v2.44.4).
        # Reading raw==dict with the bare list(...) call returned the
        # dict's keys as strings — Pydantic rejected str where Dict
        # was required and the whole endpoint 500'd.  Normalize here
        # so legacy rows with either shape render cleanly; ignore
        # anything we can't coerce rather than crashing the page.
        raw_status = (raw_env or {}).get("tools_status")
        if isinstance(raw_status, list):
            tools_status_norm = [e for e in raw_status if isinstance(e, dict)]
        elif isinstance(raw_status, dict):
            tools_status_norm = []
            for name, payload in raw_status.items():
                if isinstance(payload, dict):
                    tools_status_norm.append({"name": name, **payload})
                else:
                    tools_status_norm.append({"name": name, "status": str(payload)})
        else:
            tools_status_norm = []

        env_snapshot = ReconEnvironmentSnapshot(
            probed_at=session.environment_probed_at,
            probed_from_ip=session.environment_probed_from_ip,
            os_family=(raw_env or {}).get("os_family"),
            os_release=(raw_env or {}).get("os_release"),
            shell=(raw_env or {}).get("shell"),
            arch=(raw_env or {}).get("arch"),
            python=(raw_env or {}).get("python"),
            notes=(raw_env or {}).get("notes"),
            tools_status=tools_status_norm,
            raw=raw_env,
        )

    return ReconSessionDetail(
        summary=summary,
        uploads=uploads,
        uploads_total=uploads_total,
        uploads_skip=uploads_skip,
        uploads_limit=uploads_limit,
        host_stats=host_stats,
        hosts=hosts,
        plans_generated=plan_links,
        plans_total=plans_total,
        plans_skip=plans_skip,
        plans_limit=plans_limit,
        all_scan_ids=all_scan_ids,
        environment=env_snapshot,
    )


# ---------------------------------------------------------------------------
# Pairwise diff — what changed between two recon sessions (v2.52.0)
# ---------------------------------------------------------------------------

@router.get(
    "/{session_id}/diff/{other_session_id}",
    response_model=ReconSessionDiff,
    summary="Diff two recon sessions in the same project (v2.52.0)",
)
def diff_recon_sessions(
    project_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    other_session_id: int = Path(..., gt=0),
    limit: int = Query(
        50, ge=1, le=500,
        description=(
            "Cap on the per-side sample of differing hosts.  The full "
            "counts (uncapped) are always returned alongside so the UI "
            "can render 'showing N of M — view all in Inventory'."
        ),
    ),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    _user: User = Depends(get_current_user),
):
    """Return the per-side stats + capped IP set difference between
    two recon sessions in the same project.

    Replaces the alpha.6 ReconCompare view's habit of fetching both
    sessions' full host arrays and diffing them client-side.  At 40k
    × 40k hosts that path served ~60 MB of JSON only to render a
    diff no human could parse anyway; this endpoint computes the
    difference in PostgreSQL (cheap), returns capped samples, and
    leaves "browse the full list" to the existing /hosts page.
    """
    if session_id == other_session_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot diff a recon session against itself.",
        )
    # Both sessions must belong to the current project — cross-project
    # diff is meaningless and would expose host counts across boundaries.
    rows = (
        db.query(ReconSession.id, ReconSession.project_id)
        .filter(ReconSession.id.in_([session_id, other_session_id]))
        .all()
    )
    by_id = {r.id: r.project_id for r in rows}
    missing = [sid for sid in (session_id, other_session_id) if sid not in by_id]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Recon session(s) not found: {', '.join(str(m) for m in missing)}",
        )
    foreign = [sid for sid, pid in by_id.items() if pid != project.id]
    if foreign:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Recon session(s) {', '.join(str(s) for s in foreign)} "
                f"belong to a different project."
            ),
        )

    stats_a = ReconHostStats(**recon_session_host_stats(db, session_id))
    stats_b = ReconHostStats(**recon_session_host_stats(db, other_session_id))
    diff = recon_session_diff_ips(db, session_id, other_session_id, limit=limit)

    return ReconSessionDiff(
        session_a_id=session_id,
        session_b_id=other_session_id,
        stats_a=stats_a,
        stats_b=stats_b,
        in_a_not_b_count=diff["in_a_not_b_count"],
        in_b_not_a_count=diff["in_b_not_a_count"],
        shared_count=diff["shared_count"],
        in_a_not_b_sample=[ReconDiffHostRow(**r) for r in diff["in_a_not_b_sample"]],
        in_b_not_a_sample=[ReconDiffHostRow(**r) for r in diff["in_b_not_a_sample"]],
        limit=diff["limit"],
    )


# ---------------------------------------------------------------------------
# Abandon — operator-driven stuck-session cleanup (v2.36.0)
# ---------------------------------------------------------------------------

class ReconAbandonRequest(BaseModel):
    """Optional reason the operator typed in the confirmation dialog.

    Capped server-side so a malicious actor can't blow up the notes
    column.  Empty reason is fine — the audit line still records who
    abandoned and when.
    """
    reason: Optional[str] = Field(
        default=None,
        max_length=512,
        description="Short free-form explanation (e.g. 'agent process died').",
    )


@router.post(
    "/{session_id}/abandon",
    response_model=ReconSessionRow,
    summary="Mark a stuck recon session as abandoned (v2.36.0)",
    dependencies=[Depends(require_project_role(ProjectRole.ANALYST))],
)
def abandon_recon_session(
    body: ReconAbandonRequest,
    project_id: int = Path(..., gt=0),
    session_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """Operator-driven completion path for recon sessions that the
    terminal-side agent never called ``/agent/recon/complete`` on.

    Why this exists: ``/agent/recon/complete`` is the only thing that
    transitions a session out of ``'active'`` in the agent flow.  If
    the agent process dies / is killed / forgets, the session sits at
    ``'active'`` forever, the AgentActivityRail keeps surfacing it as
    a live run, and the operator has no recourse short of manual SQL.

    This endpoint is the recourse.  Requires the **analyst** role on
    the project (operators can clean up their own runs; viewers
    cannot).  Only valid for sessions in ``'active'`` state — calling
    on a session already in a terminal state (completed / failed /
    abandoned) returns 409 so accidental double-abandons don't
    silently rewrite metadata.

    The abandon reason (when supplied) is appended to ``session.notes``
    with a header line identifying the user and timestamp, so the
    audit trail lives with the row.  Same notes field the agent's
    ``/complete`` call uses, so consumers see a uniform shape.
    """
    session = (
        db.query(ReconSession)
        .filter(
            ReconSession.id == session_id,
            ReconSession.project_id == project.id,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Recon session not found")

    # v2.45.9 — ownership gate.  The analyst project-role (above) lets
    # a user clean up THEIR OWN stuck runs, but a fellow analyst must
    # not be able to abandon someone else's live session.  Abandoning
    # another operator's session requires project-admin — that's the
    # departed-user-cleanup recovery path.  A session whose owner was
    # deleted (started_by_id NULL) is treated as orphaned: any analyst
    # may reclaim it.
    if (
        session.started_by_id is not None
        and session.started_by_id != user.id
        and not is_project_admin(db, project.id, user)
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "This recon session belongs to another operator. Only its "
                "owner or a project admin can abandon it."
            ),
        )

    if session.status != ReconSessionStatus.ACTIVE.value:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Recon session #{session_id} is already in terminal state "
                f"'{session.status}'; only active sessions can be abandoned."
            ),
        )

    now = datetime.now(timezone.utc)
    header = (
        f"[Abandoned by {user.username} on {now.isoformat()}]"
    )
    audit_line = header if not body.reason else f"{header}: {body.reason.strip()}"
    session.notes = (
        f"{session.notes}\n\n{audit_line}" if session.notes else audit_line
    )[:8192]
    session.status = ReconSessionStatus.ABANDONED.value
    session.completed_at = now
    db.commit()
    db.refresh(session)

    scope_name_row = (
        db.query(models.Scope.name)
        .filter(models.Scope.id == session.scope_id)
        .first()
    )
    return _row_from_session(
        session,
        scope_name=scope_name_row[0] if scope_name_row else None,
    )
