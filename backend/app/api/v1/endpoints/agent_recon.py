"""
Agent API — agentic reconnaissance ingest workflow (v2.11.0+).

These endpoints accept a scope-bound API key (api_keys.scope_id set)
and gate on ``require_recon_scope``.  The agent's workflow is
unrelated to test plans: discover hosts, upload raw scanner output,
poll for parse completion, iterate, complete.

Test-plan-scoped keys are rejected here, and recon keys are rejected
on plan endpoints, so the two workflows are cleanly isolated at the
auth layer.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.db.models_agent import Agent, ReconSession, ReconSessionStatus
from app.api.deps import require_recon_scope

from app.api.v1.endpoints.agent_schemas import (
    ReconContextResponse, ReconUploadResponse,
    ReconJobStatus,
    ReconSummaryResponse, ReconCompleteRequest,
    EnvironmentProbeRequest, EnvironmentProbeResponse, EnvironmentSummary,
)
from app.api.v1.endpoints.agent_common import _scoped_host_ids_subq
from app.services.agent_prompt_history import PROMPT_VERSION
# v2.27.0 — recon-context helpers extracted to two focused service modules.
# The endpoint handlers in this file call into them via these aliases so
# the route file stays focused on HTTP / auth / response shaping.
from app.services.agent_environment_probe_service import apply_environment_probe
from app.services.recon_summary_service import (
    recon_session_host_breakdown as _recon_session_host_breakdown,
    web_targets_from_hosts as _web_targets_from_hosts,
    build_known_hosts_probe as _build_known_hosts_probe,
    session_hosts_file_content as _session_hosts_file_content,
)
from app.services.recon_planning_service import (
    analyze_scope_size as _analyze_scope_size,
    build_tool_catalog as _build_tool_catalog,
    build_recommended_sequence as _build_recommended_sequence,
)

router = APIRouter()


def _seconds_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    """Elapsed seconds between two job timestamps, rounded to 2 dp.

    Returns None unless both ends are present, so an in-flight job reports
    None rather than a misleading partial delta — and we never subtract a
    tz-aware timestamp from a naive one (the PG vs SQLite backends differ)."""
    if start is None or end is None:
        return None
    try:
        return round((end - start).total_seconds(), 2)
    except TypeError:
        # Mixed tz-aware / naive — don't guess, just omit the timing.
        return None


def _load_recon_session(db: Session, request: Request) -> ReconSession:
    """Resolve the ReconSession for the caller's recon-scoped key.

    v2.45.0 — keys minted from /scopes/{id}/recon/start now bind to a
    specific ReconSession via ``api_keys.recon_session_id``.  When that
    binding is present, we use it directly — multiple concurrent
    recons on the same scope (intentional: cross-model agent coverage,
    multi-user workflows) each resolve to their own session.

    Pre-v2.45.0 keys only had ``scope_id``; for those we fall back to
    the legacy "newest active session under the scope" heuristic.  This
    heuristic was the root cause of the concurrent-recon collision
    bug — Agent A and Agent B both holding scope-only keys would have
    their /recon/upload calls silently routed to whichever session
    started later.

    404 if there's no usable session — the bound session was deleted,
    or for legacy keys no active session exists for the scope.
    """
    scope_id = getattr(request.state, "scoped_scope_id", None)
    if scope_id is None:
        # Should be unreachable — require_recon_scope already gates on this.
        raise HTTPException(status_code=403, detail="Recon scope not bound")

    # Preferred path (v2.45.0+ keys): the key knows its session.
    bound_session_id = getattr(request.state, "scoped_recon_session_id", None)
    session = None
    if bound_session_id is not None:
        session = (
            db.query(ReconSession)
            .filter(ReconSession.id == bound_session_id)
            .first()
        )
        # Defence-in-depth: the key's scope_id must still match the
        # session's scope_id.  Catches a hypothetical session→scope
        # FK swap or a manually-edited api_keys row.
        if session is not None and session.scope_id != scope_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "API key's bound recon session belongs to a "
                    "different scope — refusing to serve."
                ),
            )

    # Legacy fallback (pre-v2.45.0 keys with NULL recon_session_id):
    # newest active session on the scope.  See the docstring for why
    # this heuristic is the bug we're moving away from.
    if session is None:
        session = (
            db.query(ReconSession)
            .filter(
                ReconSession.scope_id == scope_id,
                ReconSession.status == ReconSessionStatus.ACTIVE.value,
            )
            .order_by(ReconSession.started_at.desc())
            .first()
        )
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No active reconnaissance session for this scope. "
                "Ask the user to click 'Start Agentic Recon' on the "
                "Scopes page again."
            ),
        )
    return session


@router.get(
    "/recon/context",
    response_model=ReconContextResponse,
    summary="Recon session context — scope CIDRs + known hosts + tool catalog",
)
def get_recon_context(
    request: Request,
    agent: Agent = Depends(require_recon_scope),
    db: Session = Depends(get_db),
):
    """Return the recon session's scope, already-known hosts, and a
    starting tool catalog parameterized to the scope's CIDRs.

    Agents call this once at the start of a session to get oriented.
    The catalog is a starting point, not a constraint — agents are
    free to run other tools as long as they stay within the scope's
    CIDR list and follow the approval protocol.
    """
    session = _load_recon_session(db, request)
    scope = db.query(models.Scope).filter(models.Scope.id == session.scope_id).first()
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found (deleted?)")

    subnet_cidrs = [
        row[0] for row in db.query(models.Subnet.cidr).filter(models.Subnet.scope_id == scope.id).all()
    ]

    # Summarise already-known hosts inside *this scope* (not the whole
    # project) so the agent can decide whether it's re-running against
    # a populated scope or starting fresh.  v2.11.1 — previously this
    # returned project-wide counts, which misled the agent whenever
    # the project had hosts from other scopes' recon runs.
    #
    # Scope membership is determined by HostSubnetMapping, which is
    # populated by subnet_correlation during ingest.  A host is
    # "in scope" iff it maps to any subnet under this scope_id.
    # v2.68.0 — call the shared helper rather than inline the query.
    # The previous inline form used `.subquery()` which raises
    # `SAWarning: Coercing Subquery into a select() for use in IN()`
    # on every call; the helper returns a `Select` directly.
    scoped_host_ids_subq = _scoped_host_ids_subq(db, scope.id)
    total_known = (
        db.query(func.count(models.Host.id))
        .filter(
            models.Host.project_id == agent.project_id,
            models.Host.id.in_(scoped_host_ids_subq),
        )
        .scalar()
    ) or 0
    hosts_with_ports = (
        db.query(func.count(func.distinct(models.Port.host_id)))
        .join(models.Host, models.Host.id == models.Port.host_id)
        .filter(
            models.Host.project_id == agent.project_id,
            models.Host.id.in_(scoped_host_ids_subq),
            models.Port.state == "open",
        )
        .scalar()
    ) or 0

    known_host_summary = {
        "total_known_hosts": total_known,
        "hosts_with_open_ports": hosts_with_ports,
        "note": (
            "These counts are scoped to this recon session's scope "
            "(via host→subnet correlation from prior ingests).  "
            "total_known_hosts may be 0 on a fresh scope even if the "
            "project has hosts from other scopes' recon runs."
        ),
    }

    scope_size = _analyze_scope_size(subnet_cidrs)
    # v2.39.0 — pass the operator's environment probe (when available)
    # into sequence building so steps whose default tool is broken on
    # this host (httpx Python-CLI collision, masscan no raw-socket
    # privilege) get swapped for working alternatives.  Closes recon
    # feedback #2.  Falls through silently when no probe has been
    # posted yet — the pre-v2.39 static sequence shape is preserved.
    recommended_sequence = _build_recommended_sequence(
        subnet_cidrs, scope_size, hosts_with_ports,
        environment=session.environment,
    )
    known_hosts_probe = _build_known_hosts_probe(db, agent.project_id, scope.id)

    # v2.45.4 — bound the scope_cidrs in the response.  The internal
    # subnet_cidrs list stays full (scope-size math + tool-catalog
    # command parameterization need every CIDR), but a scope with
    # thousands of subnets must not dump them all into every
    # /recon/context response.  Cap the response field; the agent
    # pages the authoritative full list from GET /agent/recon/subnets.
    _CONTEXT_CIDR_CAP = 100
    scope_cidrs_total = len(subnet_cidrs)
    subnets_truncated = scope_cidrs_total > _CONTEXT_CIDR_CAP
    scope_cidrs_field = (
        subnet_cidrs[:_CONTEXT_CIDR_CAP] if subnets_truncated else subnet_cidrs
    )

    return ReconContextResponse(
        recon_session_id=session.id,
        scope_id=scope.id,
        scope_name=scope.name,
        prompt_version=PROMPT_VERSION,
        scope_cidrs=scope_cidrs_field,
        scope_cidrs_total=scope_cidrs_total,
        subnets_truncated=subnets_truncated,
        known_host_summary=known_host_summary,
        tool_catalog=_build_tool_catalog(subnet_cidrs, scope_size),
        session_status=session.status,
        started_at=session.started_at,
        scope_size=scope_size,
        recommended_sequence=recommended_sequence,
        known_hosts_probe=known_hosts_probe,
        # v2.23.0 — echo the recon environment probe.  None until the
        # agent posts /recon/sessions/{id}/environment.
        environment=(
            EnvironmentSummary(**session.environment)
            if session.environment else None
        ),
    )


@router.get(
    "/recon/subnets",
    summary="Paginated authoritative subnet list for the recon scope",
)
def get_recon_subnets(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    agent: Agent = Depends(require_recon_scope),
    db: Session = Depends(get_db),
):
    """Return the recon scope's subnet CIDRs, paginated (v2.45.4).

    The recon prompt inlines at most ~25 CIDRs and ``/recon/context``
    caps its ``scope_cidrs`` at 100 — a scope with thousands of
    subnets would otherwise overflow the agent's context window.
    This endpoint is the authoritative full-list source: walk
    ``offset`` in ``limit``-sized pages until the ``subnets`` array
    comes back empty.

    Ordered by subnet id (stable insertion order) so paging is
    deterministic across calls.
    """
    session = _load_recon_session(db, request)
    total = (
        db.query(func.count(models.Subnet.id))
        .filter(models.Subnet.scope_id == session.scope_id)
        .scalar()
    ) or 0
    rows = (
        db.query(models.Subnet.cidr)
        .filter(models.Subnet.scope_id == session.scope_id)
        .order_by(models.Subnet.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    cidrs = [r[0] for r in rows]
    return {
        "recon_session_id": session.id,
        "scope_id": session.scope_id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": len(cidrs),
        # Empty `subnets` signals the caller to stop paging.
        "subnets": cidrs,
        "has_more": offset + len(cidrs) < total,
    }


# --- Environment probe (v2.23.0) ---
#
# MUST be the agent's first call after the user clicks Start Recon.
# Per-recon-session, per-user; echoed back by /recon/context so the
# agent's scan-flavour choices reflect this operator host (e.g. don't
# propose `masscan` if it's not on PATH; don't propose
# `Get-NetTCPConnection` from a Kali Linux box).

@router.post(
    "/recon/sessions/{session_id}/environment",
    response_model=EnvironmentProbeResponse,
    summary="Record this recon session's operator environment",
)
def record_recon_environment(
    body: EnvironmentProbeRequest,
    request: Request,
    session_id: int = Path(..., gt=0),
    agent: Agent = Depends(require_recon_scope),
    db: Session = Depends(get_db),
):
    """Record the agent's environment probe for a recon session.

    Re-POSTing replaces the previous probe — useful for long-running
    sessions where the operator installs additional tools mid-run.
    """
    # Resolve through the shared helper so the v2.45.0 per-session key
    # binding is honoured.  A scope-only check (session.scope_id ==
    # scoped_scope_id) is NOT enough: two concurrent recons share a
    # scope, so a key bound to session X could otherwise overwrite a
    # different active session Y's environment probe (and corrupt Y's
    # audit attribution).  _load_recon_session enforces the binding;
    # we then assert the path matches what the key resolved to.
    session = _load_recon_session(db, request)
    if session.id != session_id:
        raise HTTPException(
            status_code=403,
            detail="Not your recon session — the API key is bound to a different session.",
        )

    # v2.43.3 (AUD-C1 + AUD-O3): write path moved to the shared
    # `apply_environment_probe` service.  It enforces the audit
    # invariant — historical sessions stay immutable, so probes are
    # rejected with 409 once `session.status` is no longer in the
    # allowed set.  Mirrors the execution-side guard.
    # Recon sessions only have ACTIVE → COMPLETED/FAILED/ABANDONED
    # transitions.  PAUSED is an execution-session concept (see
    # ExecutionSessionStatus) — referencing ReconSessionStatus.PAUSED
    # here AttributeError'd every env probe before this fix (v2.44.3),
    # which the agent saw as a generic 500 because the unhandled
    # exception handler v2.44.2 hadn't shipped yet at the time the
    # bug landed in v2.43.3 (the extraction of apply_environment_probe).
    apply_environment_probe(
        session=session,
        body=body,
        request=request,
        agent=agent,
        active_statuses={ReconSessionStatus.ACTIVE.value},
        session_kind="recon",
    )
    db.commit()
    db.refresh(session)

    return EnvironmentProbeResponse(
        session_id=session.id,
        session_type="recon",
        probed_at=session.environment_probed_at,
        probed_by_user_id=session.environment_probed_by_user_id,
        probed_from_ip=session.environment_probed_from_ip,
        environment=EnvironmentSummary(**session.environment),
    )


@router.post(
    "/recon/upload",
    response_model=ReconUploadResponse,
    status_code=201,
    summary="Upload scanner output for ingestion into this recon session",
)
async def upload_recon_output(
    request: Request,
    file: UploadFile = File(...),
    tool_name: Optional[str] = Form(None),
    command_run: Optional[str] = Form(None),
    agent: Agent = Depends(require_recon_scope),
    db: Session = Depends(get_db),
):
    """Multipart upload wrapper around the existing ingestion pipeline.

    Accepts any scanner output format the ingestion service already
    supports (nmap XML, masscan XML/JSON/txt, gnmap, nessus, openvas,
    eyewitness JSON/CSV, nikto, naabu, bloodhound, etc.).  Creates an
    IngestionJob tagged with the recon session's id so
    ``/agent/recon/summary`` can roll up counts per session.

    Returns the queued job; the agent then polls
    ``GET /agent/recon/jobs/{job_id}`` until the parse completes.
    """
    from app.services.ingestion_service import ingestion_service

    session = _load_recon_session(db, request)

    opts: Dict[str, Any] = {
        "project_id": agent.project_id,
        "recon_session_id": session.id,
        "source": "agent-recon",
    }
    if tool_name:
        opts["tool_name_hint"] = tool_name
    if command_run:
        opts["command_run"] = command_run

    try:
        job = await ingestion_service.create_job(
            db=db,
            upload=file,
            submitted_by_id=None,  # agent-submitted; no JWT user
            options=opts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # recon_session_id is stamped inside create_job's transaction (passed
    # via opts), so the job is attributed to this session the instant it
    # becomes claimable — no worker-race window.  Here we only bump the
    # session's own upload counter.
    session.uploads_submitted = (session.uploads_submitted or 0) + 1
    db.commit()
    db.refresh(job)

    # Kick the worker so the job starts as soon as possible.
    ingestion_service.enqueue_job(job.id)

    return ReconUploadResponse(
        job_id=job.id,
        filename=job.original_filename,
        status=job.status,
        message="Upload queued for parsing",
        recon_session_id=session.id,
    )


@router.get(
    "/recon/jobs/{job_id}",
    response_model=ReconJobStatus,
    summary="Poll an upload job's parse status",
)
def get_recon_job(
    job_id: int,
    request: Request,
    agent: Agent = Depends(require_recon_scope),
    db: Session = Depends(get_db),
):
    """Return the status of an IngestionJob the agent previously
    uploaded via ``POST /recon/upload``.

    Enforces session scope: jobs belonging to a different recon
    session (or to a human upload) are not visible — the agent can
    only poll its own in-flight work.  404 if the job doesn't exist
    or belongs to someone else.
    """
    session = _load_recon_session(db, request)
    job = (
        db.query(models.IngestionJob)
        .filter(
            models.IngestionJob.id == job_id,
            models.IngestionJob.recon_session_id == session.id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Recon job not found")

    # Echo definitive timings so the agent can tell a slow queue from a slow
    # parse.  Computed only from completed transitions (no "now" delta) to
    # avoid mixing tz-aware/naive timestamps across the PG/SQLite backends.
    queue_age_s = _seconds_between(job.created_at, job.started_at)
    parse_s = _seconds_between(job.started_at, job.completed_at)

    return ReconJobStatus(
        job_id=job.id,
        status=job.status,
        message=job.message,
        error_message=job.error_message,
        scan_id=job.scan_id,
        tool_name=job.tool_name,
        parse_error_id=job.parse_error_id,
        recon_session_id=job.recon_session_id,
        last_error=job.last_error,
        queue_age_s=queue_age_s,
        parse_s=parse_s,
    )


@router.get(
    "/recon/summary",
    response_model=ReconSummaryResponse,
    summary="Rolling summary of what this recon session has discovered",
)
def get_recon_summary(
    request: Request,
    agent: Agent = Depends(require_recon_scope),
    db: Session = Depends(get_db),
):
    """Return the live counts for this recon session.

    ``scans_ingested`` and ``hosts_discovered`` are computed from the
    scan_history tables joined against IngestionJob rows tagged with
    this session — authoritative rather than cached.  The ReconSession
    row's own counters are refreshed as a side effect so later reads
    (e.g. admin UI) see consistent numbers.
    """
    session = _load_recon_session(db, request)
    # Scope-filter subquery, matching the breakdown and /agent/hosts.
    # A parser can write a row whose resolved IP is outside the scope
    # (observed live during session #6 testing: httpx -tls-probe's
    # SAN-expansion produced records for 127.0.0.1/localhost/pi.hole).
    # Without this filter, the aggregate counters over-report by the
    # count of such rows — inconsistent with the per-host breakdown
    # which IS scope-filtered.
    scope_host_subq = _scoped_host_ids_subq(db, session.scope_id)

    scans_count = (
        db.query(func.count(func.distinct(models.IngestionJob.scan_id)))
        .filter(
            models.IngestionJob.recon_session_id == session.id,
            models.IngestionJob.scan_id.isnot(None),
        )
        .scalar()
    ) or 0

    # Hosts: distinct Host IDs that appear in scans belonging to this
    # session via host_scan_history, bounded by scope membership.
    hosts_count = (
        db.query(func.count(func.distinct(models.HostScanHistory.host_id)))
        .join(
            models.IngestionJob,
            models.IngestionJob.scan_id == models.HostScanHistory.scan_id,
        )
        .filter(
            models.IngestionJob.recon_session_id == session.id,
            models.HostScanHistory.host_id.in_(scope_host_subq),
        )
        .scalar()
    ) or 0

    ports_count = (
        # distinct(Port.id): a host with N HostScanHistory rows in this
        # session would otherwise fan out and count every port N times.
        db.query(func.count(func.distinct(models.Port.id)))
        .join(models.Host, models.Host.id == models.Port.host_id)
        .join(
            models.HostScanHistory,
            models.HostScanHistory.host_id == models.Host.id,
        )
        .join(
            models.IngestionJob,
            models.IngestionJob.scan_id == models.HostScanHistory.scan_id,
        )
        .filter(
            models.IngestionJob.recon_session_id == session.id,
            models.Port.state == "open",
            models.Host.id.in_(scope_host_subq),
        )
        .scalar()
    ) or 0

    # Refresh the session's own counters so subsequent reads match.
    session.scans_ingested = scans_count
    session.hosts_discovered = hosts_count
    session.ports_discovered = ports_count
    db.commit()

    hosts_breakdown = _recon_session_host_breakdown(db, session.id)
    return ReconSummaryResponse(
        recon_session_id=session.id,
        scope_id=session.scope_id,
        status=session.status,
        uploads_submitted=session.uploads_submitted or 0,
        scans_ingested=scans_count,
        hosts_discovered=hosts_count,
        ports_discovered=ports_count,
        started_at=session.started_at,
        completed_at=session.completed_at,
        hosts=hosts_breakdown,
        web_targets=_web_targets_from_hosts(hosts_breakdown),
        live_hosts_file_content=_session_hosts_file_content(hosts_breakdown),
    )


@router.post(
    "/recon/complete",
    response_model=ReconSummaryResponse,
    summary="Mark the recon session complete",
)
def complete_recon_session(
    body: ReconCompleteRequest,
    request: Request,
    agent: Agent = Depends(require_recon_scope),
    db: Session = Depends(get_db),
):
    """Transition the recon session from active to completed.

    The session's final counters are frozen at the values returned
    by a fresh summary computation.  The API key remains valid until
    its TTL expires, but subsequent calls to ``/recon/upload`` or
    ``/recon/context`` will 404 because ``_load_recon_session`` only
    matches active sessions.  The user needs to start a fresh recon
    session from the Scopes UI if they want to do another pass.
    """
    session = _load_recon_session(db, request)

    # Reject a second /recon/complete on an already-terminal session.  For a
    # session-bound key _load_recon_session resolves by recon_session_id
    # without a status filter, so without this guard a double-complete would
    # silently overwrite completed_at and re-freeze the counters.  Mirrors
    # the execution-side guard in complete_execution_session.
    terminal_states = {
        ReconSessionStatus.COMPLETED.value,
        ReconSessionStatus.FAILED.value,
        ReconSessionStatus.ABANDONED.value,
    }
    if session.status in terminal_states:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Recon session #{session.id} is already in terminal state "
                f"'{session.status}'; cannot complete twice. Start a fresh "
                f"recon session from the Scopes UI for another pass."
            ),
        )

    if body.notes:
        combined = (session.notes + "\n\n" + body.notes) if session.notes else body.notes
        session.notes = combined[:8192]  # cap — notes is free-form

    # Freeze final counters via a live recomputation, scope-filtered to
    # match the summary endpoint (v2.13.1).
    scope_host_subq = _scoped_host_ids_subq(db, session.scope_id)
    session.scans_ingested = (
        db.query(func.count(func.distinct(models.IngestionJob.scan_id)))
        .filter(
            models.IngestionJob.recon_session_id == session.id,
            models.IngestionJob.scan_id.isnot(None),
        )
        .scalar()
    ) or 0
    session.hosts_discovered = (
        db.query(func.count(func.distinct(models.HostScanHistory.host_id)))
        .join(
            models.IngestionJob,
            models.IngestionJob.scan_id == models.HostScanHistory.scan_id,
        )
        .filter(
            models.IngestionJob.recon_session_id == session.id,
            models.HostScanHistory.host_id.in_(scope_host_subq),
        )
        .scalar()
    ) or 0
    session.ports_discovered = (
        # distinct(Port.id): see get_recon_summary — the HostScanHistory
        # join fans out ports by the number of session scans per host.
        db.query(func.count(func.distinct(models.Port.id)))
        .join(models.Host, models.Host.id == models.Port.host_id)
        .join(
            models.HostScanHistory,
            models.HostScanHistory.host_id == models.Host.id,
        )
        .join(
            models.IngestionJob,
            models.IngestionJob.scan_id == models.HostScanHistory.scan_id,
        )
        .filter(
            models.IngestionJob.recon_session_id == session.id,
            models.Port.state == "open",
            models.Host.id.in_(scope_host_subq),
        )
        .scalar()
    ) or 0

    session.status = ReconSessionStatus.COMPLETED.value
    session.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)

    hosts_breakdown = _recon_session_host_breakdown(db, session.id)
    return ReconSummaryResponse(
        recon_session_id=session.id,
        scope_id=session.scope_id,
        status=session.status,
        uploads_submitted=session.uploads_submitted or 0,
        scans_ingested=session.scans_ingested,
        hosts_discovered=session.hosts_discovered,
        ports_discovered=session.ports_discovered,
        started_at=session.started_at,
        completed_at=session.completed_at,
        hosts=hosts_breakdown,
        web_targets=_web_targets_from_hosts(hosts_breakdown),
        live_hosts_file_content=_session_hosts_file_content(hosts_breakdown),
    )
