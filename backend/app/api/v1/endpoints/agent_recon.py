"""
Agent API — scope reads and scanner-output uploads.

An agent reads the project's scope (its subnets, its in-scope domains, and the
hosts the inventory already holds inside it), runs its own tools, and uploads
the output to its session as results land.  The key is project-scoped and a
project has exactly one scope, so no read names one (``/agent/scope/…``).
"""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.session import disable_statement_timeout, get_db
from app.db import models
from app.db.models_agent import Agent
from app.db.models_project import ProjectRole
from app.api.downloads import attachment
from app.api.deps import agent_read_floor, check_agent_rate_limit, get_client_info
from app.api.v1.endpoints.agent_schemas import (
    AgentRowsPage, KeepTruncatedUploadRequest, ReconUploadResponse, ReconJobStatus, agent_page,
)
from app.api.v1.endpoints.agent_common import load_agent_session
from app.services.agent_policy import TRUNCATED_UPLOAD_ASK_FIRST
from app.services.default_scope import project_scope
from app.services.import_attention_service import failure_kind
from app.services.ingestion_service import (
    TRUNCATED_FILE_ERROR, DuplicateUploadError, ingestion_service,
)
from app.services.job_transitions import JobNotTransitionable
from app.services.staged_import_service import file_retained, keep_truncated_for_agent
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


def _scope_id(db: Session, agent: Agent) -> int:
    """The id of the project's ONE scope, for the builders that are keyed by
    it — or 0, which no row has, when the project has declared nothing yet:
    every read below then answers empty, never 404 (there is no scope to name
    wrongly any more).  A read never creates the scope."""
    scope = project_scope(db, agent.project_id)
    return scope.id if scope is not None else 0


# ---------------------------------------------------------------------------
# Scope reads — the project has exactly one scope (2.488.0), so no route names
# one: `/agent/scope/…`.  They were `/agent/scopes/{scope_id}/…`.
# ---------------------------------------------------------------------------

class ScopeDomainsPage(AgentRowsPage):
    note: str = ""


@router.get(
    "/scope/subnets",
    response_model=AgentRowsPage,
    summary="The project's in-scope subnets and addresses, paginated",
)
def get_scope_subnets(
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The project's subnet CIDRs (``items``, each a CIDR string), as a page.
    Ordered by id so paging is deterministic."""
    scope_id = _scope_id(db, agent)
    total = (
        db.query(func.count(models.Subnet.id))
        .filter(models.Subnet.scope_id == scope_id)
        .scalar()
    ) or 0
    rows = (
        db.query(models.Subnet.cidr)
        .filter(models.Subnet.scope_id == scope_id)
        .order_by(models.Subnet.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return agent_page([r[0] for r in rows], total=total, limit=limit, offset=offset)


@router.get(
    "/scope/domains",
    response_model=ScopeDomainsPage,
    summary="The project's in-scope domains, paginated",
)
def get_scope_domains(
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """The project's declared domains (``items``), as a page.  Each entry is
    ``{domain, include_subdomains}`` — an exact entry covers only that name,
    an include_subdomains entry covers every descendant.  A name being in
    scope never puts the address it resolves to in subnet scope."""
    scope_id = _scope_id(db, agent)
    total = (
        db.query(func.count(models.ScopeDomain.id))
        .filter(models.ScopeDomain.scope_id == scope_id)
        .scalar()
    ) or 0
    rows = (
        db.query(models.ScopeDomain.domain, models.ScopeDomain.include_subdomains)
        .filter(models.ScopeDomain.scope_id == scope_id)
        .order_by(models.ScopeDomain.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    domains = [{"domain": d, "include_subdomains": bool(sub)} for d, sub in rows]
    return {
        **agent_page(domains, total=total, limit=limit, offset=offset),
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
        headers=attachment(filename),
    )


@router.get(
    "/scope/hosts.ndjson",
    dependencies=_EXPORT_READ,
    summary="Stream every in-scope host as newline-delimited JSON",
    response_class=StreamingResponse,
)
def download_scope_hosts_ndjson(
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Every host in the project's scope with its open-port detail, one JSON
    object per line — redirect it to a file and process it locally."""
    return _stream(
        db, _iter_scope_hosts_ndjson(db, _scope_id(db, agent)),
        "application/x-ndjson",
        "scope-hosts.jsonl",
    )


@router.get(
    "/scope/live-hosts.txt",
    dependencies=_EXPORT_READ,
    summary="Stream in-scope host IPs as a target file",
    response_class=StreamingResponse,
)
def download_scope_live_hosts(
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """One IP per line, IP-sorted — a plain-text target list."""
    return _stream(
        db, _iter_scope_live_hosts(db, _scope_id(db, agent)),
        "text/plain",
        "scope-hosts.txt",
    )


@router.get(
    "/scope/web-targets.txt",
    dependencies=_EXPORT_READ,
    summary="Stream derived http/https URLs as a target file",
    response_class=StreamingResponse,
)
def download_scope_web_targets(
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """One URL per line, derived from in-scope hosts' open web ports."""
    return _stream(
        db, _iter_scope_web_targets(db, _scope_id(db, agent)),
        "text/plain",
        "scope-web-targets.txt",
    )


@router.get(
    "/scope/named-targets.ndjson",
    dependencies=_EXPORT_READ,
    summary="Stream every in-scope NAME with its current addresses and web evidence",
    response_class=StreamingResponse,
)
def download_scope_named_targets(
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
    scope = project_scope(db, agent.project_id)
    return _stream(
        db, _iter_scope_named_targets_ndjson(db, scope) if scope is not None else iter(()),
        "application/x-ndjson",
        "scope-named-targets.jsonl",
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
            "batch, shown on /scans as a single row."
        ),
    ),
    skip_informational: Optional[bool] = Form(
        None,
        description=(
            "Nessus only: drop severity-0 (informational) report items "
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
    bloodhound, netexec, etc.).  The job belongs to this agent session.  Poll
    ``GET /agent/uploads/{job_id}`` until the parse completes.

    An identical file already in the project (as a scan, or still parsing) is
    refused with 409 ``duplicate_scan`` naming it — nothing is created. Agents
    cannot force a re-import; that is an operator decision made from the
    Scans page.
    """
    from app.services.ingestion_service import DuplicateUploadError, ingestion_service
    from app.services.scan_batch_service import get_or_create_session_batch

    # Database work runs in the thread pool, never on the event loop
    # (external review 2026-10-16, A2): a slow query or lock wait here stalled
    # every other request on this process.
    session = await run_in_threadpool(load_agent_session, db, request)

    # Ingestion is a project-level pipeline: parsed hosts land at the project
    # and are correlated to the scope's subnets downstream (HostSubnetMapping).
    opts: Dict[str, Any] = {
        "project_id": agent.project_id,
        "agent_session_id": session.id,
        "source": "agent",
    }
    # v2.341.0 — the same precedence rule as the operator upload (form field >
    # project choice > deployment default), from the one helper that owns it.
    from app.db.models_project import Project as _Project, resolve_skip_informational
    project_row = await run_in_threadpool(lambda: db.query(_Project).filter(_Project.id == agent.project_id).first())
    opts["skip_informational"] = resolve_skip_informational(project_row, skip_informational)
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
            scan_batch = await run_in_threadpool(
                get_or_create_session_batch,
                db, project_id=agent.project_id, agent_session_id=session.id, label=batch,
            )
        except OperationalError:
            await run_in_threadpool(db.rollback)
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

    def _finish():
        db.commit()
        db.refresh(job)
        # Kick the worker so the job starts as soon as possible on this
        # request's session (parallel chunk uploads hit the
        # two-connections-per-request pool deadlock otherwise).
        ingestion_service.enqueue_job(job.id, db=db)

    await run_in_threadpool(_finish)

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
    return _job_status(db, _session_upload(db, request, agent, job_id))


def _session_upload(db: Session, request: Request, agent: Agent, job_id: int) -> models.IngestionJob:
    """The upload job ``job_id`` of THIS agent session, or 404."""
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
        # Deliberately this session's jobs only.  A resumed or later session of
        # the same operator asked for its earlier upload and got a bare 404
        # (native MCP run 2026-10-10): say where that job's outcome is read.
        raise HTTPException(status_code=404, detail=(
            "Upload job not found in this session — this reads only jobs THIS session "
            "uploaded. For an upload by an earlier session or a person, read the scan: "
            "assist_list_scans gives each scan's ingestion_job_id, and "
            "assist_list_ingestion_issues lists imports that failed or finished partial."
        ))
    return job


def _parse_error_of(db: Session, job: models.IngestionJob) -> Optional[models.ParseError]:
    return (
        db.get(models.ParseError, job.parse_error_id)
        if job.status == "failed" and job.parse_error_id is not None else None
    )


def _job_status(db: Session, job: models.IngestionJob) -> ReconJobStatus:
    # The kind of failure, read as the pages read it (one function).
    parse_error = _parse_error_of(db, job)
    return ReconJobStatus(
        job_id=job.id,
        status=job.status,
        message=job.message,
        error_message=job.error_message,
        scan_id=job.scan_id,
        tool_name=job.tool_name,
        parse_error_id=job.parse_error_id,
        last_error=job.last_error,
        failure_kind=failure_kind(job, parse_error),
        queue_age_s=_seconds_between(job.created_at, job.started_at),
        parse_s=_seconds_between(job.started_at, job.completed_at),
    )


@router.post(
    "/uploads/{job_id}/keep-truncated",
    response_model=ReconJobStatus,
    summary="Import what a cut-short upload holds (after asking the operator; recorded)",
)
def keep_truncated_upload(
    body: KeepTruncatedUploadRequest,
    request: Request,
    job_id: int = Path(..., ge=1),
    agent: Agent = Depends(check_agent_rate_limit),
    db: Session = Depends(get_db),
):
    """Re-queue this session's upload that failed as cut short
    (``failure_kind: truncated_file``) so that it imports the complete records
    the file holds and finishes PARTIAL.  Answers the job's new state; poll
    ``GET /agent/uploads/{job_id}`` as after an upload.

    A POST that declares nothing, so the gate's default holds: the key's
    operator must be able to WRITE to the project (analyst and above; an
    auditor's or a viewer's agent is refused 403).

    ``operator_agreed`` is the agent's statement that it asked.  The server
    cannot check a conversation it never sees, so nothing here is an approval:
    what it does is refuse a request that does not make the statement, and
    write an audit row (``truncated_upload_kept``: the operator, the project,
    the session, the job, the format and the count of complete records) in
    the transaction that re-queues the job.  The re-queue is the people's
    (``staged_import_service.queue_job``): one attempt's instruction.
    """
    if body.operator_agreed is not True:
        raise HTTPException(status_code=422, detail=TRUNCATED_UPLOAD_ASK_FIRST)
    job = _session_upload(db, request, agent, job_id)
    parse_error = _parse_error_of(db, job)
    if job.status != "failed":
        raise HTTPException(status_code=409, detail=(
            f"This upload is {job.status}, not failed: only an upload that failed as cut short "
            "(failure_kind: truncated_file) can be imported this way."
        ))
    kind = failure_kind(job, parse_error)
    if kind != TRUNCATED_FILE_ERROR:
        raise HTTPException(status_code=409, detail=(
            "This upload did not fail as cut short (failure_kind: "
            f"{kind or 'none'}): there is nothing here to keep. Its error_message says why it failed."
        ))
    gone = HTTPException(status_code=409, detail=(
        "The uploaded file is no longer kept on the server, so it cannot be read again: "
        "upload the file again."
    ))
    if not file_retained(job):
        raise gone
    try:
        queued = keep_truncated_for_agent(
            db, job, parse_error,
            operator_id=request.state.key_operator_id,
            agent_session_id=job.agent_session_id,
            **get_client_info(request),
        )
    except DuplicateUploadError as exc:
        raise HTTPException(status_code=409, detail=exc.detail())
    except JobNotTransitionable as exc:
        if exc.status == "file_missing":
            raise gone
        raise HTTPException(status_code=409, detail=(
            f"This upload is {exc.status} now, not failed: it was started again meanwhile."
        ))
    except LookupError:
        raise HTTPException(status_code=404, detail="Upload job not found in this session")
    ingestion_service.enqueue_job(queued.id, db=db)
    return _job_status(db, queued)
