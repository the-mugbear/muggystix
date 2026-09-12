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
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models
from app.db.models_agent import Agent, ReconSession, ReconSessionStatus
from app.api.deps import check_agent_rate_limit
from app.services.agent_session_service import (
    open_recon_phase, resolve_recon_phase,
)

from app.api.v1.endpoints.agent_schemas import (
    ReconContextResponse, ReconUploadResponse,
    ReconJobStatus,
    ReconSummaryResponse, ReconCompleteRequest, ReconStartRequest,
    ReconDownload, ReconDownloads,
    EnvironmentSummary,
)
from app.api.v1.endpoints.agent_common import _scoped_host_ids_subq, load_agent_session
from app.services.agent_prompt_history import PROMPT_VERSION
# v2.27.0 — recon-context helpers extracted to two focused service modules.
# The endpoint handlers in this file call into them via these aliases so
# the route file stays focused on HTTP / auth / response shaping.
from app.services.recon_summary_service import (
    recon_session_host_breakdown as _recon_session_host_breakdown,
    recon_session_host_count as _recon_session_host_count,
    web_targets_from_hosts as _web_targets_from_hosts,
    build_known_hosts_probe as _build_known_hosts_probe,
    session_hosts_file_content as _session_hosts_file_content,
    iter_recon_hosts_ndjson as _iter_recon_hosts_ndjson,
    iter_recon_live_hosts as _iter_recon_live_hosts,
    iter_recon_web_targets as _iter_recon_web_targets,
)
from app.services.recon_planning_service import (
    analyze_scope_size as _analyze_scope_size,
    build_tool_catalog as _build_tool_catalog,
    build_recommended_sequence as _build_recommended_sequence,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Summary payload caps (v2.241.0)
# ---------------------------------------------------------------------------
#
# Measured against one recon session holding 40,000 hosts × 3 open ports,
# the uncapped summary response was 31.4 MB — hosts[] 18.9 MB, web_targets[]
# 12.0 MB, live_hosts_file_content 0.5 MB.  That is roughly 7.8M tokens: it
# does not fit in any context window, so an agent calling /recon/summary on
# a large session lost the run regardless of how careful it was.
#
# AGENTS.md previously handled this by warning the agent not to "read or
# echo it whole" — advice no agent can follow, because receiving a tool
# result is what puts it in context.  The cap has to be server-side.
#
# 50 hosts is a *sample* — enough to sanity-check that ingestion produced
# sensible rows, small enough to never dominate a response.  Everything
# beyond it comes from the streaming download endpoints below.
_SUMMARY_HOST_CAP = 50

# live_hosts_file_content is different in kind: an agent pipes it straight
# into `nmap -iL`.  A shortened list would scan part of the scope while the
# agent reported full coverage, so past this size the field is emptied (not
# trimmed) and the download URL carries the complete set.  An empty target
# file makes the next tool fail loudly, which is the safe direction.
_INLINE_FILE_HOST_CAP = 1000


def _download_manifest(session_id: int) -> ReconDownloads:
    """Bulk-artifact pointers for a recon session.

    The ``curl`` strings are ready to run: they carry the API key header
    and redirect to a file, because the entire point is that these bodies
    land on disk rather than in the agent's context.
    """
    base = "/api/v1/agent/recon"
    auth = '-H "X-API-Key: $BLUESTICK_API_KEY"'
    return ReconDownloads(
        hosts_ndjson=ReconDownload(
            url=f"{base}/hosts.ndjson",
            media_type="application/x-ndjson",
            description=(
                "Every in-scope host this session discovered, one JSON object "
                "per line (host_id, ip_address, hostname, open_port_count, "
                "services, open_ports). Filter locally with jq."
            ),
            curl=f"curl -sS {auth} $BLUESTICK_URL{base}/hosts.ndjson -o session-hosts.jsonl",
        ),
        live_hosts=ReconDownload(
            url=f"{base}/live-hosts.txt",
            media_type="text/plain",
            description=(
                "One IP per line, IP-sorted — the target file for "
                "`nmap -iL` / `masscan -iL`."
            ),
            curl=f"curl -sS {auth} $BLUESTICK_URL{base}/live-hosts.txt -o session-hosts.txt",
        ),
        web_targets=ReconDownload(
            url=f"{base}/web-targets.txt",
            media_type="text/plain",
            description=(
                "One http/https URL per line for every web port discovered — "
                "the target file for `httpx -l` / `eyewitness -f`."
            ),
            curl=f"curl -sS {auth} $BLUESTICK_URL{base}/web-targets.txt -o web-targets.txt",
        ),
    )


def _build_summary_response(
    db: Session,
    session: ReconSession,
    *,
    scans_ingested: int,
    hosts_discovered: int,
    ports_discovered: int,
) -> ReconSummaryResponse:
    """Assemble the capped summary envelope shared by /summary and /complete.

    Both endpoints returned identical bodies built by copy-paste; they now
    share this so a cap fixed in one place can't be missed in the other.
    """
    hosts_total = _recon_session_host_count(db, session.id)
    hosts_sample = _recon_session_host_breakdown(
        db, session.id, limit=_SUMMARY_HOST_CAP,
    )
    hosts_truncated = hosts_total > len(hosts_sample)

    inline_file = (
        _session_hosts_file_content(
            _recon_session_host_breakdown(db, session.id)
        )
        if 0 < hosts_total <= _INLINE_FILE_HOST_CAP
        else ""
    )

    return ReconSummaryResponse(
        recon_session_id=session.id,
        scope_id=session.scope_id,
        status=session.status,
        uploads_submitted=session.uploads_submitted or 0,
        scans_ingested=scans_ingested,
        hosts_discovered=hosts_discovered,
        ports_discovered=ports_discovered,
        started_at=session.started_at,
        completed_at=session.completed_at,
        hosts=hosts_sample,
        hosts_total=hosts_total,
        hosts_truncated=hosts_truncated,
        web_targets=_web_targets_from_hosts(hosts_sample),
        web_targets_truncated=hosts_truncated,
        live_hosts_file_content=inline_file,
        live_hosts_file_truncated=hosts_total > _INLINE_FILE_HOST_CAP,
        downloads=_download_manifest(session.id),
    )


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
    """The reconnaissance run this call is about.

    v2.337.0 — a key is no longer scoped to one recon run; the operator's
    session may have several open.  Resolve from the session, honouring an
    optional ``recon_session_id`` query parameter (which must belong to the
    session).  A session with exactly one active run needs no parameter; none
    open is a 409 that points at ``POST /agent/recon/start``.
    """
    session = load_agent_session(db, request)
    raw = request.query_params.get("recon_session_id")
    recon_id = int(raw) if raw and raw.isdigit() else None
    return resolve_recon_phase(db, session.id, recon_id)


@router.get(
    "/recon/context",
    response_model=ReconContextResponse,
    summary="Recon session context — scope CIDRs + known hosts + tool catalog",
)
def get_recon_context(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
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
    return _recon_context_payload(db, session, scope, agent, subnet_cidrs, include_read_back=False)


def _recon_context_payload(db, session, scope, agent, subnet_cidrs, *, include_read_back):
    """Build the ReconContextResponse shared by /recon/start and /recon/context."""
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

    # v2.328.0 — name scope rides along with the CIDRs, bounded the same
    # way.  The agent needs this to know which names it may resolve/probe
    # without asking (see AGENTS.md "target is in the inventory"); the
    # authoritative full list pages from GET /agent/recon/domains.
    # Count + a LIMITed projection (same shape as GET /agent/recon/domains):
    # the cap must bound the database transfer, not just the serialisation.
    scope_domains_total = (
        db.query(func.count(models.ScopeDomain.id))
        .filter(models.ScopeDomain.scope_id == scope.id)
        .scalar()
        or 0
    )
    domain_rows = (
        db.query(models.ScopeDomain.domain, models.ScopeDomain.include_subdomains)
        .filter(models.ScopeDomain.scope_id == scope.id)
        .order_by(models.ScopeDomain.id)
        .limit(_CONTEXT_CIDR_CAP)
        .all()
    )
    domains_truncated = scope_domains_total > _CONTEXT_CIDR_CAP
    scope_domains_field = [{"domain": d, "include_subdomains": bool(sub)} for d, sub in domain_rows]

    read_back = None
    if include_read_back:
        from app.services.agent_policy import render_phase_read_back
        facts = [
            "the CIDRs you will scan: " + (
                ", ".join(scope_cidrs_field) + (f" (+{scope_cidrs_total - len(scope_cidrs_field)} more)" if subnets_truncated else "")
            ),
        ]
        if scope_domains_field:
            facts.append(
                "in-scope domains (names only, resolving them does not scope their addresses): "
                + ", ".join(
                    (f"*.{d['domain']}" if d["include_subdomains"] else d["domain"])
                    for d in scope_domains_field
                )
            )
        facts.append("the working directory you will run every tool from and write output into")
        read_back = render_phase_read_back("recon", facts=facts)

    return ReconContextResponse(
        recon_session_id=session.id,
        scope_id=scope.id,
        scope_name=scope.name,
        prompt_version=PROMPT_VERSION,
        read_back=read_back,
        scope_cidrs=scope_cidrs_field,
        scope_cidrs_total=scope_cidrs_total,
        subnets_truncated=subnets_truncated,
        scope_domains=scope_domains_field,
        scope_domains_total=scope_domains_total,
        domains_truncated=domains_truncated,
        known_host_summary=known_host_summary,
        tool_catalog=_build_tool_catalog(subnet_cidrs, scope_size),
        session_status=session.status,
        started_at=session.started_at,
        scope_size=scope_size,
        recommended_sequence=recommended_sequence,
        known_hosts_probe=known_hosts_probe,
        # Echo the session's environment probe (snapshotted onto the run).
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
    agent: Agent = Depends(check_agent_rate_limit),
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


@router.get(
    "/recon/domains",
    summary="Paginated authoritative in-scope domain list for the recon scope",
)
def get_recon_domains(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Return the recon scope's declared domains, paginated (v2.328.0).

    Mirrors ``/recon/subnets``: ``/recon/context`` caps ``scope_domains``
    at 100, so walk ``offset`` here until ``domains`` comes back empty.
    Each entry is ``{domain, include_subdomains}`` — an exact entry covers
    only that name, an include_subdomains entry covers every descendant.
    These are the names the agent may resolve or probe without asking; a
    name being in scope never puts the address it resolves to in subnet
    scope.  Ordered by row id so paging is deterministic.
    """
    session = _load_recon_session(db, request)
    total = (
        db.query(func.count(models.ScopeDomain.id))
        .filter(models.ScopeDomain.scope_id == session.scope_id)
        .scalar()
    ) or 0
    rows = (
        db.query(models.ScopeDomain.domain, models.ScopeDomain.include_subdomains)
        .filter(models.ScopeDomain.scope_id == session.scope_id)
        .order_by(models.ScopeDomain.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    domains = [{"domain": d, "include_subdomains": bool(sub)} for d, sub in rows]
    return {
        "recon_session_id": session.id,
        "scope_id": session.scope_id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": len(domains),
        # Empty `domains` signals the caller to stop paging.
        "domains": domains,
        "has_more": offset + len(domains) < total,
        "note": (
            "Name scope is independent of subnet scope: an in-scope name does not "
            "make the address it resolves to in scope."
        ),
    }


# ---------------------------------------------------------------------------
# Bulk downloads (v2.241.0)
# ---------------------------------------------------------------------------
#
# Streamed, uncapped, and meant to be redirected to a file.  These are the
# answer to "the complete host list does not fit in a context window": the
# agent writes them to disk and greps/jqs them, so coverage stays complete
# without the payload ever being read into the model.
#
# Bounded server memory too — iter_recon_session_hosts pages the rows, so a
# 40k-host session streams in chunks instead of materialising 19 MB of ORM
# objects the way the old inline `hosts[]` did.

def _stream(generator, media_type: str, filename: str) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get(
    "/recon/hosts.ndjson",
    summary="Stream every discovered host as newline-delimited JSON",
    response_class=StreamingResponse,
)
def download_recon_hosts_ndjson(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Complete per-host breakdown, one JSON object per line.

    Use this instead of reading ``hosts[]`` from ``/recon/summary`` when
    ``hosts_truncated`` is true.  Redirect it to a file:

        curl -sS -H "X-API-Key: $KEY" .../recon/hosts.ndjson -o hosts.jsonl
        jq -c 'select(.open_ports[]?.port == 445)' hosts.jsonl

    Same fields, same IP ordering, and same scope bounding as the summary
    breakdown — this is the identical dataset, delivered so it can be
    processed without being read whole.
    """
    session = _load_recon_session(db, request)
    return _stream(
        _iter_recon_hosts_ndjson(db, session.id),
        "application/x-ndjson",
        f"recon-session-{session.id}-hosts.jsonl",
    )


@router.get(
    "/recon/live-hosts.txt",
    summary="Stream discovered host IPs as an -iL target file",
    response_class=StreamingResponse,
)
def download_recon_live_hosts(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """One IP per line, IP-sorted — feed straight to ``nmap -iL``.

    This is the authoritative form of ``live_hosts_file_content``, which
    the summary leaves EMPTY past _INLINE_FILE_HOST_CAP hosts rather than
    shortening it (a trimmed target file would silently under-scan).
    """
    session = _load_recon_session(db, request)
    return _stream(
        _iter_recon_live_hosts(db, session.id),
        "text/plain",
        f"recon-session-{session.id}-hosts.txt",
    )


@router.get(
    "/recon/web-targets.txt",
    summary="Stream derived http/https URLs as an httpx/eyewitness target file",
    response_class=StreamingResponse,
)
def download_recon_web_targets(
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """One URL per line for every discovered web port — ``httpx -l`` input."""
    session = _load_recon_session(db, request)
    return _stream(
        _iter_recon_web_targets(db, session.id),
        "text/plain",
        f"recon-session-{session.id}-web-targets.txt",
    )


# --- Environment probe (v2.23.0) ---
#
# MUST be the agent's first call after the user clicks Start Recon.
# Per-recon-session, per-user; echoed back by /recon/context so the
# agent's scan-flavour choices reflect this operator host (e.g. don't
# propose `masscan` if it's not on PATH; don't propose
# `Get-NetTCPConnection` from a Kali Linux box).

@router.post(
    "/recon/start",
    response_model=ReconContextResponse,
    status_code=201,
    summary="Open a reconnaissance run against a scope in this session",
)
def start_recon_phase(
    body: ReconStartRequest,
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Open a recon run on ``scope_id`` and return its context.

    v2.337.0 — replaces the operator-side "Start Agentic Recon" mint.  The
    session already holds the operator's authority and environment probe; this
    picks the scope to work.  A run already open on the scope is reused.  The
    response is the same ``/recon/context`` shape plus the phase read-back the
    agent must state before scanning — the CIDRs and in-scope domains are the
    facts it restates, which is the moment they exist and can be wrong.
    """
    session = load_agent_session(db, request)
    scope = (
        db.query(models.Scope)
        .filter(models.Scope.id == body.scope_id, models.Scope.project_id == agent.project_id)
        .first()
    )
    if not scope:
        raise HTTPException(status_code=404, detail="Scope not found in this project")
    subnet_cidrs = [
        row[0] for row in db.query(models.Subnet.cidr).filter(models.Subnet.scope_id == scope.id).all()
    ]
    if not subnet_cidrs:
        raise HTTPException(
            status_code=400,
            detail="Scope has no subnets registered — upload a subnet file first.",
        )
    run = open_recon_phase(db, session=session, scope=scope, notes=body.notes)
    db.commit()
    db.refresh(run)
    # Reuse the context builder so /start and /context never drift.
    return _recon_context_payload(db, run, scope, agent, subnet_cidrs, include_read_back=True)


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
    batch: Optional[str] = Form(
        None,
        max_length=200,
        description=(
            "Name of the sweep this file is one chunk of (e.g. `nmap-tcp-top1000`). "
            "Every upload with the same label in this recon session joins one "
            "batch, shown on /scans as a single row (v2.335.0)."
        ),
    ),
    agent: Agent = Depends(check_agent_rate_limit),
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

    An identical file already in the project (as a scan, or still parsing) is
    refused with 409 ``duplicate_scan`` naming it — nothing is created and no
    session counter moves. Agents cannot force a re-import; that is an
    operator decision made from the Scans page.
    """
    from app.services.ingestion_service import DuplicateUploadError, ingestion_service
    from app.services.scan_batch_service import get_or_create_session_batch

    session = _load_recon_session(db, request)

    # TERMINAL-STATE GUARD (v2.317.0): a finalized recon session must not keep
    # ingesting.  record_environment and /recon/complete both 409 on a
    # non-active session — "finalized sessions are part of the audit trail and
    # stay immutable" — but upload skipped the check, so a completed/abandoned
    # session (whose summary and handoff note the operator already read) would
    # silently accept more scanner output and mutate its rollup.  Uploads add
    # host/port data, so the immutability argument applies more strongly here,
    # not less.
    if session.status != ReconSessionStatus.ACTIVE.value:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Recon session #{session.id} is in terminal state "
                f"'{session.status}'; it cannot ingest more scanner output. "
                "Its counts and handoff note are final — start a fresh recon "
                "session from the Scopes UI for another pass."
            ),
        )

    # SCOPE BOUNDARY (review A-6): a recon key is scope-bound for AUTH and
    # ATTRIBUTION, not for CONTAINMENT.  Ingestion is a project-level pipeline:
    # the parsed hosts land at `project_id` and are correlated to scopes
    # downstream (HostSubnetMapping), so output containing IPs OUTSIDE the
    # bound scope's CIDRs is written to the project, not rejected.  This is
    # deliberate — a sweep legitimately discovers adjacent hosts the operator
    # wants — so do NOT mistake the scope binding for a write-containment
    # boundary.  The `recon_session_id` below is what ties the upload back to
    # this session for audit/roll-up.
    opts: Dict[str, Any] = {
        "project_id": agent.project_id,
        "recon_session_id": session.id,
        "source": "agent-recon",
    }
    if tool_name:
        opts["tool_name_hint"] = tool_name
    if command_run:
        opts["command_run"] = command_run

    # Joins (or starts) the session's batch for this label in the same
    # transaction as the job; a rejected upload rolls it back with the job.
    scan_batch = None
    if batch and batch.strip():
        scan_batch = get_or_create_session_batch(
            db, project_id=agent.project_id, recon_session_id=session.id, label=batch,
        )

    try:
        job = await ingestion_service.create_job(
            db=db,
            upload=file,
            submitted_by_id=None,  # agent-submitted; no JWT user
            options=opts,
            batch_id=scan_batch.id if scan_batch is not None else None,
        )
    except DuplicateUploadError as exc:
        raise HTTPException(status_code=409, detail=exc.detail())
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
        batch_id=job.batch_id,
        batch=scan_batch.label if scan_batch is not None else None,
    )


@router.get(
    "/recon/jobs/{job_id}",
    response_model=ReconJobStatus,
    summary="Poll an upload job's parse status",
)
def get_recon_job(
    job_id: int,
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
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
    agent: Agent = Depends(check_agent_rate_limit),
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

    return _build_summary_response(
        db, session,
        scans_ingested=scans_count,
        hosts_discovered=hosts_count,
        ports_discovered=ports_count,
    )


@router.post(
    "/recon/complete",
    response_model=ReconSummaryResponse,
    summary="Mark the recon session complete",
)
def complete_recon_session(
    body: ReconCompleteRequest,
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
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

    return _build_summary_response(
        db, session,
        scans_ingested=session.scans_ingested,
        hosts_discovered=session.hosts_discovered,
        ports_discovered=session.ports_discovered,
    )
