"""
Agent API — scope reads and scanner-output uploads.

An agent reads a scope (its subnets, its in-scope domains, and the hosts the
inventory already holds inside it), runs its own tools, and uploads the output
to its session as results land.  Recon runs were removed in v2.433.0: the key
is project-scoped and every read is keyed on ``scope_id`` directly.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import disable_statement_timeout, get_db
from app.db import models
from app.db.models_agent import Agent
from app.db.models_project import ProjectRole
from app.api.deps import agent_read_floor, check_agent_rate_limit
from app.api.v1.endpoints.agent_schemas import (
    ReconUploadResponse, ReconJobStatus,
)
from app.api.v1.endpoints.agent_common import load_agent_session
from app.services.scope_targets_service import (
    iter_scope_hosts_ndjson as _iter_scope_hosts_ndjson,
    iter_scope_live_hosts as _iter_scope_live_hosts,
    iter_scope_named_targets_ndjson as _iter_scope_named_targets_ndjson,
    iter_scope_web_targets as _iter_scope_web_targets,
)

router = APIRouter()

# A scope's target files are bulk exports of project data: the floor the JWT
# exports have (``export.py`` and ``reports.py`` gate on AUDITOR).
_EXPORT_READ = [Depends(agent_read_floor(ProjectRole.AUDITOR))]


def _seconds_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def _load_scope(db: Session, agent: Agent, scope_id: int) -> models.Scope:
    scope = (
        db.query(models.Scope)
        .filter(models.Scope.id == scope_id, models.Scope.project_id == agent.project_id)
        .first()
    )
    if scope is None:
        raise HTTPException(status_code=404, detail="Scope not found in this project")
    return scope


# ---------------------------------------------------------------------------
# Scope reads
# ---------------------------------------------------------------------------

@router.get(
    "/scopes/{scope_id}/subnets",
    summary="Paginated CIDR list for a scope",
)
def get_scope_subnets(
    scope_id: int = Path(..., gt=0),
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The scope's subnet CIDRs, paginated — walk ``offset`` in ``limit``-sized
    pages until ``subnets`` comes back empty.  Ordered by id so paging is
    deterministic."""
    scope = _load_scope(db, agent, scope_id)
    total = (
        db.query(func.count(models.Subnet.id))
        .filter(models.Subnet.scope_id == scope.id)
        .scalar()
    ) or 0
    rows = (
        db.query(models.Subnet.cidr)
        .filter(models.Subnet.scope_id == scope.id)
        .order_by(models.Subnet.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    cidrs = [r[0] for r in rows]
    return {
        "scope_id": scope.id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": len(cidrs),
        "subnets": cidrs,
        "has_more": offset + len(cidrs) < total,
    }


@router.get(
    "/scopes/{scope_id}/domains",
    summary="Paginated in-scope domain list for a scope",
)
def get_scope_domains(
    scope_id: int = Path(..., gt=0),
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The scope's declared domains, paginated.  Each entry is
    ``{domain, include_subdomains}`` — an exact entry covers only that name,
    an include_subdomains entry covers every descendant.  A name being in
    scope never puts the address it resolves to in subnet scope."""
    scope = _load_scope(db, agent, scope_id)
    total = (
        db.query(func.count(models.ScopeDomain.id))
        .filter(models.ScopeDomain.scope_id == scope.id)
        .scalar()
    ) or 0
    rows = (
        db.query(models.ScopeDomain.domain, models.ScopeDomain.include_subdomains)
        .filter(models.ScopeDomain.scope_id == scope.id)
        .order_by(models.ScopeDomain.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    domains = [{"domain": d, "include_subdomains": bool(sub)} for d, sub in rows]
    return {
        "scope_id": scope.id,
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": len(domains),
        "domains": domains,
        "has_more": offset + len(domains) < total,
        "note": (
            "Name scope is independent of subnet scope: an in-scope name does "
            "not make the address it resolves to in scope."
        ),
    }


# ---------------------------------------------------------------------------
# Bulk host downloads — streamed, meant to be redirected to a file.
# ---------------------------------------------------------------------------

def _stream(db: Session, generator, media_type: str, filename: str) -> StreamingResponse:
    # A streamed target file runs its queries after the response has started
    # and is meant to cover the whole scope: exempt from the API statement
    # timeout (review 2026-10-01 R23), which is for interactive requests.
    disable_statement_timeout(db)
    return StreamingResponse(
        generator,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get(
    "/scopes/{scope_id}/hosts.ndjson",
    dependencies=_EXPORT_READ,
    summary="Stream every in-scope host as newline-delimited JSON",
    response_class=StreamingResponse,
)
def download_scope_hosts_ndjson(
    scope_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Every host in the scope with its open-port detail, one JSON object per
    line — redirect it to a file and process it locally."""
    scope = _load_scope(db, agent, scope_id)
    return _stream(
        db, _iter_scope_hosts_ndjson(db, scope.id),
        "application/x-ndjson",
        f"scope-{scope.id}-hosts.jsonl",
    )


@router.get(
    "/scopes/{scope_id}/live-hosts.txt",
    dependencies=_EXPORT_READ,
    summary="Stream in-scope host IPs as a target file",
    response_class=StreamingResponse,
)
def download_scope_live_hosts(
    scope_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """One IP per line, IP-sorted — an ``-iL`` target file."""
    scope = _load_scope(db, agent, scope_id)
    return _stream(
        db, _iter_scope_live_hosts(db, scope.id),
        "text/plain",
        f"scope-{scope.id}-hosts.txt",
    )


@router.get(
    "/scopes/{scope_id}/web-targets.txt",
    dependencies=_EXPORT_READ,
    summary="Stream derived http/https URLs as a target file",
    response_class=StreamingResponse,
)
def download_scope_web_targets(
    scope_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """One URL per line, derived from in-scope hosts' open web ports."""
    scope = _load_scope(db, agent, scope_id)
    return _stream(
        db, _iter_scope_web_targets(db, scope.id),
        "text/plain",
        f"scope-{scope.id}-web-targets.txt",
    )


@router.get(
    "/scopes/{scope_id}/named-targets.ndjson",
    dependencies=_EXPORT_READ,
    summary="Stream every in-scope NAME with its current addresses and web evidence",
    response_class=StreamingResponse,
)
def download_scope_named_targets(
    scope_id: int = Path(..., gt=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Name scope as a target file, one JSON object per line: every name the
    scope's domain rules cover (``scope_rule``: the matching domain, exact or
    subdomain), the addresses it CURRENTLY resolves to (latest A/AAAA batch)
    each flagged ``in_subnet_scope``, the web interfaces reached as that name,
    and ``unresolved`` + ``reason`` when no address is known.  A declared
    domain with no observed name is listed too.

    Separate from the IP files on purpose: a name in scope authorises that
    name on its address, never the address, and never the other names on it
    — test by name (Host header / SNI) where ``in_subnet_scope`` is false.
    Names only a certificate SAN or a shared address connect to are not
    listed."""
    scope = _load_scope(db, agent, scope_id)
    return _stream(
        db, _iter_scope_named_targets_ndjson(db, scope),
        "application/x-ndjson",
        f"scope-{scope.id}-named-targets.jsonl",
    )


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

@router.post(
    "/uploads",
    response_model=ReconUploadResponse,
    status_code=201,
    summary="Upload scanner output for ingestion",
)
async def upload_scanner_output(
    request: Request,
    file: UploadFile = File(...),
    tool_name: Optional[str] = Form(None),
    command_run: Optional[str] = Form(None),
    batch: Optional[str] = Form(
        None,
        max_length=200,
        description=(
            "Name of the sweep this file is one chunk of (e.g. `nmap-tcp-top1000`). "
            "Every upload with the same label in this agent session joins one "
            "batch, shown on /scans as a single row (v2.335.0)."
        ),
    ),
    skip_informational: Optional[bool] = Form(
        None,
        description=(
            "Nessus only (v2.341.0): drop severity-0 (informational) report items "
            "instead of storing a vulnerability row each; ports are still derived "
            "from them. Omit to follow the project's setting."
        ),
    ),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Multipart upload wrapper around the ingestion pipeline.

    Accepts any scanner output format the ingestion service supports (nmap
    XML, masscan, gnmap, nessus, openvas, eyewitness, nikto, naabu,
    bloodhound, netexec, etc.).  The job belongs to this agent session; no
    run needs to be open (v2.433.0).  Poll ``GET /agent/uploads/{job_id}``
    until the parse completes.

    An identical file already in the project (as a scan, or still parsing) is
    refused with 409 ``duplicate_scan`` naming it — nothing is created. Agents
    cannot force a re-import; that is an operator decision made from the
    Scans page.
    """
    from app.services.ingestion_service import DuplicateUploadError, ingestion_service
    from app.services.scan_batch_service import get_or_create_session_batch

    session = load_agent_session(db, request)

    # Ingestion is a project-level pipeline: parsed hosts land at the project
    # and are correlated to scopes downstream (HostSubnetMapping).
    opts: Dict[str, Any] = {
        "project_id": agent.project_id,
        "agent_session_id": session.id,
        "source": "agent",
    }
    # v2.341.0 — the same precedence rule as the operator upload (form field >
    # project choice > deployment default), from the one helper that owns it.
    from app.db.models_project import Project as _Project, resolve_skip_informational
    opts["skip_informational"] = resolve_skip_informational(
        db.query(_Project).filter(_Project.id == agent.project_id).first(),
        skip_informational,
    )
    if tool_name:
        opts["tool_name_hint"] = tool_name
    if command_run:
        opts["command_run"] = command_run

    # Joins (or starts) the session's batch for this label. The helper COMMITS
    # a new batch before returning (v2.368.0): this handler awaits file I/O
    # next, and an uncommitted unique-index entry held across that await is
    # what froze a worker when a sweep's chunks arrived in parallel.
    from app.services.queue_metrics_service import ensure_room_for_upload
    ensure_room_for_upload(file.size)

    scan_batch = None
    if batch and batch.strip():
        from sqlalchemy.exc import OperationalError
        try:
            scan_batch = get_or_create_session_batch(
                db, project_id=agent.project_id, agent_session_id=session.id, label=batch,
            )
        except OperationalError:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="The upload batch is busy; retry this file in a few seconds.",
                headers={"Retry-After": "5"},
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

    db.commit()
    db.refresh(job)

    # Kick the worker so the job starts as soon as possible on this request's
    # session (parallel chunk uploads hit the two-connections-per-request pool
    # deadlock otherwise).
    ingestion_service.enqueue_job(job.id, db=db)

    return ReconUploadResponse(
        job_id=job.id,
        filename=job.original_filename,
        status=job.status,
        message="Upload queued for parsing",
        batch_id=job.batch_id,
        batch=scan_batch.label if scan_batch is not None else None,
    )


@router.get(
    "/uploads/{job_id}",
    response_model=ReconJobStatus,
    summary="Poll an upload's parse status",
)
def get_upload_job(
    job_id: int,
    request: Request,
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The status of an IngestionJob this agent session uploaded via
    ``POST /agent/uploads``.  404 for a job another session (or a person)
    uploaded — an agent polls only its own work.
    """
    session = load_agent_session(db, request)
    job = (
        db.query(models.IngestionJob)
        .filter(
            models.IngestionJob.id == job_id,
            models.IngestionJob.project_id == agent.project_id,
            models.IngestionJob.agent_session_id == session.id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Upload job not found in this session")

    return ReconJobStatus(
        job_id=job.id,
        status=job.status,
        message=job.message,
        error_message=job.error_message,
        scan_id=job.scan_id,
        tool_name=job.tool_name,
        parse_error_id=job.parse_error_id,
        last_error=job.last_error,
        queue_age_s=_seconds_between(job.created_at, job.started_at),
        parse_s=_seconds_between(job.started_at, job.completed_at),
    )
