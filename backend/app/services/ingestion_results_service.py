"""Uploads and how they went — the ONE implementation (review 2026-10-10, R1b).

The Scans page's import history (``GET /scans/history``: its upload rows,
status filters and their counts — 2.523.0, owner decision 2026-10-22, G15;
until then the page's separate Ingestion results view), its rows by id
(``GET /ingestion-results?ids=``) and the agents' read
(``GET /agent/assist/ingestion-issues``, MCP ``assist_list_ingestion_issues``)
all call this module.  The agents' read used to be a second implementation:
it loaded every failed job with ``.all()`` and told an expired or discarded
staged upload from a failed import by testing the message text in Python,
and drifted from the page three times.

What lives here:

* the page's VIEWS as SQL conditions (``view_condition``): ``failed`` (an
  import that went wrong — not a staged upload that expired or was
  discarded), ``expired``, ``discarded``, ``failed_dismissed``,
  ``needs_attention`` (``blocked_import_condition``), ``superseded``;
* ``job_counts`` — every count the page's summary states about jobs, and the
  two the agents' read adds (``partial``, ``degraded``), in one statement;
* ``list_results`` / ``summary`` — the page's list and summary;
* ``list_issues`` — the agents' "what is not all here" list: the jobs whose
  file is not (all) in the project and the unresolved parse errors no failed
  job carries, ordered and paged in SQL.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, case, exists, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, aliased

from app.db import models
from app.schemas.ingestion_results import IngestionResultError, IngestionResultItem, IngestionResultStats
from app.services.format_registry import format_label
from app.services.import_attention_service import annotate_jobs, superseded_import_condition
from app.services.operations_read_service import blocked_import_condition
from app.services.scan_inventory_filters import apply_job_inventory_filters
from app.services.staged_import_service import (
    DISCARDED_MESSAGE,
    EXPIRED_MESSAGE_PREFIX,
    file_retained,
    retained_until,
)

Job = models.IngestionJob


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def expired_job_condition():
    """A staged upload nobody started within the window: its job is written
    ``failed`` (``staged_import_service.expire_staged_jobs``), but nothing
    failed — the Scans page says "expired before review", and so does this
    list (UX review 2026-09-24; it counted them under Failed)."""
    return and_(Job.status == "failed", Job.error_message.like(f"{EXPIRED_MESSAGE_PREFIX}%"))


def discarded_job_condition():
    """A staged upload the operator discarded — also written ``failed``."""
    return and_(Job.status == "failed", Job.error_message == DISCARDED_MESSAGE)


def failed_import_condition():
    """An import that went wrong — the ``failed`` view's OWN condition: not a
    staged upload that expired or was discarded (those have their own views).
    ``error_message`` may be NULL, which ``NOT LIKE`` alone would drop."""
    return and_(
        Job.status == "failed",
        ~and_(Job.error_message.isnot(None), or_(expired_job_condition(), discarded_job_condition())),
    )


def dismissed_failed_import_condition():
    """The part of the ``failed`` view someone has dismissed (2.489.0): the
    view's own condition narrowed by ``dismissed_at`` — what the Failed chip
    states beside its number, and what ``status=failed_dismissed`` lists."""
    return and_(failed_import_condition(), Job.dismissed_at.isnot(None))


def partial_import_condition():
    """Finished, but parsing stopped early: what came after is NOT in the project."""
    return and_(Job.status == "completed", Job.partial.is_(True))


def degraded_import_condition():
    """Finished to the end, but the parser dropped rows (``skipped_count``)."""
    return and_(Job.status == "completed", Job.partial.isnot(True), Job.skipped_count > 0)


def issue_job_condition():
    """A job whose file is not (all) in the project — the agents' issue list:
    failed (an expired staged upload included; a discarded one is not an
    issue — nobody wanted it), partial or degraded."""
    return or_(
        and_(Job.status == "failed", ~and_(Job.error_message.isnot(None), discarded_job_condition())),
        partial_import_condition(),
        degraded_import_condition(),
    )


#: Every ``status=`` value a list of uploads understands: the views below and
#: the stored job statuses.  The import history refuses anything else (422).
VIEWS = (
    "needs_attention", "superseded", "expired", "discarded", "failed", "failed_dismissed",
    "staged", "queued", "processing", "completed",
)


def unsuccessful_upload_condition():
    """An upload the grouped import history lists as a row of its own
    (owner decision 2026-10-22, G15): it was not imported — failed, expired or
    discarded (all written ``failed``) — so it has no scan row, and it was not
    part of a batch (a batch row lists its own files).  A completed import is
    its scan row; an upload still in flight or awaiting review is the
    ingestion queue's, and its status view's."""
    return and_(Job.status == "failed", Job.scan_id.is_(None), Job.batch_id.is_(None))


def view_condition(status: str):
    """The page's ``status=`` value as a condition: a view name, or a plain
    job status."""
    if status == "needs_attention":
        # v2.363.0 — not a job status: failed OR finished partial, and not
        # dismissed.  The same condition the Operations blockers count, so the
        # number on "Inspect import errors" and this list cannot disagree.
        return blocked_import_condition()
    if status == "superseded":
        # v2.403.0 — failed or partial, not dismissed, and the same file
        # imported cleanly by a later job — what "Dismiss N superseded" clears.
        return and_(Job.dismissed_at.is_(None), superseded_import_condition())
    if status == "expired":
        return expired_job_condition()
    if status == "discarded":
        return discarded_job_condition()
    if status == "failed":
        return failed_import_condition()
    if status == "failed_dismissed":
        return dismissed_failed_import_condition()
    return Job.status == status


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

def job_counts(db: Session, project_id: int, filters: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """Every count about the project's jobs, in one statement, each with its
    view's own condition: ``failed`` / ``expired`` / ``discarded`` /
    ``failed_dismissed`` / ``needs_attention`` / ``superseded`` (what the page's
    chips list), ``partial`` / ``degraded`` (the agents' issue kinds) and one
    per stored status (``status:<value>``).

    ``filters`` (``search`` / ``tool`` / ``created_after`` / ``uploaded_by``)
    narrows every count with the import history's own upload filters, so a
    chip counts exactly the list it opens under the same filters; without
    them ``needs_attention`` is the project's ``blocked_import_condition``
    count — the Operations figure."""
    def n(cond):
        return func.count(case((cond, Job.id)))

    query = (
        db.query(
            n(failed_import_condition()),
            n(expired_job_condition()),
            n(discarded_job_condition()),
            n(dismissed_failed_import_condition()),
            n(blocked_import_condition()),
            n(and_(Job.dismissed_at.is_(None), superseded_import_condition())),
            n(partial_import_condition()),
            n(degraded_import_condition()),
            n(Job.status == "staged"),
            n(Job.status == "completed"),
            n(Job.status == "queued"),
            n(Job.status == "processing"),
        )
        .filter(Job.project_id == project_id)
    )
    if filters:
        query = apply_job_inventory_filters(query, **filters)
    row = query.one()
    keys =("failed", "expired", "discarded", "failed_dismissed", "needs_attention", "superseded",
            "partial", "degraded", "staged", "completed", "queued", "processing")
    return {k: int(v or 0) for k, v in zip(keys, row)}


def summary(db: Session, project_id: int) -> Dict[str, Any]:
    """The Ingestion Results page's summary."""
    counts = job_counts(db, project_id)
    summary_host = (
        db.query(
            func.count(models.HostScanHistory.id).label("total_hosts"),
            func.count(case((models.HostScanHistory.state_at_scan == "up", 1))).label("total_hosts_up"),
        )
        .join(Job, Job.scan_id == models.HostScanHistory.scan_id)
        .filter(Job.project_id == project_id, Job.status == "completed")
        .first()
    )
    summary_port = (
        db.query(
            func.count(models.PortScanHistory.id).label("total_ports"),
            func.count(case((models.PortScanHistory.state_at_scan == "open", 1))).label("total_open_ports"),
        )
        .join(Job, Job.scan_id == models.PortScanHistory.scan_id)
        .filter(Job.project_id == project_id, Job.status == "completed")
        .first()
    )
    return {
        "total_needs_attention": counts["needs_attention"],
        "total_superseded": counts["superseded"],
        "total_staged": counts["staged"],
        "total_completed": counts["completed"],
        # Failed imports only; the expired and discarded staged uploads that
        # share the job status are counted apart.
        "total_failed": counts["failed"],
        # How many of `total_failed` someone has dismissed — what
        # `status=failed_dismissed` lists.
        "total_failed_dismissed": counts["failed_dismissed"],
        "total_expired": counts["expired"],
        "total_discarded": counts["discarded"],
        "total_queued": counts["queued"],
        "total_processing": counts["processing"],
        "total_hosts": summary_host.total_hosts if summary_host else 0,
        "total_hosts_up": summary_host.total_hosts_up if summary_host else 0,
        "total_ports": summary_port.total_ports if summary_port else 0,
        "total_open_ports": summary_port.total_open_ports if summary_port else 0,
    }


# ---------------------------------------------------------------------------
# The page's list
# ---------------------------------------------------------------------------

SORT_COLUMNS = {
    "created_at": Job.created_at,
    "original_filename": Job.original_filename,
    "status": Job.status,
    "tool_name": Job.tool_name,
    "file_size": Job.file_size,
}


def _stats_by_scan(db: Session, scan_ids: List[int]) -> Dict[int, IngestionResultStats]:
    if not scan_ids:
        return {}
    host_lookup = {
        row.scan_id: row for row in db.query(
            models.HostScanHistory.scan_id,
            func.count(models.HostScanHistory.id).label("hosts_parsed"),
            func.count(case((models.HostScanHistory.state_at_scan == "up", 1))).label("hosts_up"),
        )
        .filter(models.HostScanHistory.scan_id.in_(scan_ids))
        .group_by(models.HostScanHistory.scan_id)
    }
    port_lookup = {
        row.scan_id: row for row in db.query(
            models.PortScanHistory.scan_id,
            func.count(models.PortScanHistory.id).label("ports_found"),
            func.count(case((models.PortScanHistory.state_at_scan == "open", 1))).label("open_ports"),
        )
        .filter(models.PortScanHistory.scan_id.in_(scan_ids))
        .group_by(models.PortScanHistory.scan_id)
    }
    service_lookup = {
        row.scan_id: row.services_detected for row in db.query(
            models.PortScanHistory.scan_id,
            func.count(func.distinct(models.Port.service_name)).label("services_detected"),
        )
        .join(models.Port, models.PortScanHistory.port_id == models.Port.id)
        .filter(
            models.PortScanHistory.scan_id.in_(scan_ids),
            models.Port.service_name.isnot(None),
            models.Port.service_name != "",
        )
        .group_by(models.PortScanHistory.scan_id)
    }
    out: Dict[int, IngestionResultStats] = {}
    for sid in scan_ids:
        h = host_lookup.get(sid)
        p = port_lookup.get(sid)
        out[sid] = IngestionResultStats(
            hosts_parsed=h.hosts_parsed if h else 0,
            hosts_up=h.hosts_up if h else 0,
            ports_found=p.ports_found if p else 0,
            open_ports=p.open_ports if p else 0,
            services_detected=service_lookup.get(sid, 0),
        )
    return out


def result_items(db: Session, jobs: List[models.IngestionJob]) -> List[IngestionResultItem]:
    """The page's rows for ``jobs`` (annotated here), in their order."""
    jobs = annotate_jobs(db, jobs)
    scan_ids = [j.scan_id for j in jobs if j.scan_id is not None]
    stats_by_scan = _stats_by_scan(db, scan_ids)

    error_ids = [j.parse_error_id for j in jobs if j.parse_error_id is not None]
    error_lookup: Dict[int, models.ParseError] = {}
    if error_ids:
        error_lookup = {
            e.id: e for e in db.query(models.ParseError).filter(models.ParseError.id.in_(error_ids))
        }

    scan_type_lookup: Dict[int, Optional[str]] = {}
    scan_tool_lookup: Dict[int, Optional[str]] = {}
    if scan_ids:
        for r in db.query(models.Scan.id, models.Scan.scan_type, models.Scan.tool_name).filter(
            models.Scan.id.in_(scan_ids)
        ):
            scan_type_lookup[r.id] = r.scan_type
            scan_tool_lookup[r.id] = r.tool_name

    items: List[IngestionResultItem] = []
    for job in jobs:
        duration = None
        if job.started_at and job.completed_at:
            duration = (job.completed_at - job.started_at).total_seconds()
        tool = job.tool_name
        scan_type = None
        if job.scan_id:
            scan_type = scan_type_lookup.get(job.scan_id)
            if not tool:
                tool = scan_tool_lookup.get(job.scan_id)
        item = IngestionResultItem(
            id=job.id,
            parse_error_id=job.parse_error_id,
            original_filename=job.original_filename,
            status=job.status,
            file_size=job.file_size,
            tool_name=tool,
            scan_type=scan_type,
            scan_id=job.scan_id,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_seconds=duration,
            progress=job.progress,
            detected_file_type=job.detected_file_type,
            detected_format_label=format_label(job.detected_file_type),
            format_override=job.format_override,
            format_override_label=format_label(job.format_override),
            final_file_type=job.final_file_type,
            final_format_label=format_label(job.final_file_type),
            source_tool=job.source_tool,
            file_retained=file_retained(job),
            retained_until=retained_until(job),
            partial=bool(job.partial),
            skipped_count=int(job.skipped_count or 0),
            parser_warnings=job.parser_warnings,
            dismissed_at=job.dismissed_at,
            superseded_by_job_id=job.superseded_by_job_id,
            failure_reason=job.failure_reason,
            failure_kind=job.failure_kind,
            uninterpreted_total=int((job.uninterpreted_lines or {}).get("total") or 0),
            uninterpreted_distinct=int((job.uninterpreted_lines or {}).get("distinct") or 0),
        )
        if job.scan_id and job.scan_id in stats_by_scan:
            item.stats = stats_by_scan[job.scan_id]
        if job.parse_error_id and job.parse_error_id in error_lookup:
            pe = error_lookup[job.parse_error_id]
            item.error = IngestionResultError(
                error_type=pe.error_type, error_message=pe.error_message, user_message=pe.user_message,
            )
        elif job.status == "failed" and job.error_message:
            item.error = IngestionResultError(
                error_type="processing_error", error_message=job.error_message, user_message=job.error_message,
            )
        items.append(item)
    return items


def list_results(
    db: Session,
    project_id: int,
    *,
    status: Optional[str] = None,
    tool: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    skip: int = 0,
    limit: int = 50,
    ids: Optional[List[int]] = None,
) -> Tuple[List[IngestionResultItem], int]:
    """The page's list: ``(rows, total)``; ``total`` uses the same filters.

    ``ids`` names the rows (the import history hydrates its upload rows by id,
    as it does its scans and batches)."""
    base = db.query(Job).filter(Job.project_id == project_id)
    if ids is not None:
        base = base.filter(Job.id.in_(ids))
    if status:
        base = base.filter(view_condition(status))
    base = apply_job_inventory_filters(base, search=search, tool=tool)
    total =base.with_entities(func.count(Job.id)).scalar() or 0
    col = SORT_COLUMNS[sort_by]
    order = col.asc() if sort_order == "asc" else col.desc()
    # The id breaks ties: sorted by a column many rows share (status, tool),
    # an order with no tiebreaker is a different order on every page read.
    jobs = base.order_by(order, Job.id.desc()).offset(skip).limit(limit).all()
    return result_items(db, jobs), total


# ---------------------------------------------------------------------------
# The agents' issue list
# ---------------------------------------------------------------------------

def _orphan_parse_error_condition(project_id: int):
    """An unresolved parse error no FAILED job of the project carries — a
    failed job already reports its own (listing both would show one broken
    upload twice)."""
    Claimer = aliased(models.IngestionJob)
    return and_(
        models.ParseError.project_id == project_id,
        models.ParseError.status == "unresolved",
        ~exists().where(
            Claimer.project_id == project_id,
            Claimer.status == "failed",
            Claimer.parse_error_id == models.ParseError.id,
        ),
    )


def unresolved_parse_error_counts(db: Session, project_id: int) -> Tuple[int, int]:
    """``(every unresolved parse error, those no failed job carries)``."""
    PE = models.ParseError
    total, orphans = (
        db.query(
            func.count(PE.id),
            func.count(case((_orphan_parse_error_condition(project_id), PE.id))),
        )
        .filter(PE.project_id == project_id, PE.status == "unresolved")
        .one()
    )
    return int(total or 0), int(orphans or 0)


def list_issues(
    db: Session, project_id: int, *, limit: int, offset: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """``(page, total)`` of what is not (all) in the project, newest first
    (undated last; on equal times a job before a parse error, then the higher
    id).  Each entry is ``{"kind", "job"}`` or ``{"kind", "parse_error"}``;
    a job carries ``needs_attention`` and the page's ``failure_kind``."""
    kind = case(
        (expired_job_condition(), literal("expired")),
        (Job.status == "failed", literal("failed")),
        (Job.partial.is_(True), literal("partial")),
        else_=literal("degraded"),
    )
    jobs = select(
        literal(0).label("src"), Job.id.label("id"), Job.created_at.label("created_at"), kind.label("kind"),
    ).where(Job.project_id == project_id, issue_job_condition())
    errors = select(
        literal(1).label("src"), models.ParseError.id.label("id"),
        models.ParseError.created_at.label("created_at"), literal("parse_error").label("kind"),
    ).where(_orphan_parse_error_condition(project_id))
    rows = union_all(jobs, errors).subquery()
    total = db.execute(select(func.count()).select_from(rows)).scalar() or 0
    page = db.execute(
        select(rows.c.src, rows.c.id, rows.c.kind)
        .order_by(rows.c.created_at.desc().nullslast(), rows.c.src, rows.c.id.desc())
        .offset(offset).limit(limit)
    ).all()

    job_ids = [r.id for r in page if r.src == 0]
    error_ids = [r.id for r in page if r.src == 1]
    job_rows: Dict[int, models.IngestionJob] = {}
    waiting: set = set()
    if job_ids:
        job_rows = {j.id: j for j in annotate_jobs(db, db.query(Job).filter(Job.id.in_(job_ids)).all())}
        waiting = {
            jid for (jid,) in db.query(Job.id).filter(Job.id.in_(job_ids), blocked_import_condition())
        }
    error_rows: Dict[int, models.ParseError] = {}
    if error_ids:
        error_rows = {e.id: e for e in db.query(models.ParseError).filter(models.ParseError.id.in_(error_ids))}
    # The user-facing message of the parse error a failed job carries.
    carried = {j.parse_error_id for j in job_rows.values() if j.parse_error_id is not None and j.status == "failed"}
    messages: Dict[int, Optional[str]] = {}
    if carried:
        messages = dict(
            db.query(models.ParseError.id, models.ParseError.user_message)
            .filter(models.ParseError.id.in_(carried))
            .all()
        )

    out: List[Dict[str, Any]] = []
    for r in page:
        if r.src == 0:
            job = job_rows[r.id]
            out.append({
                "kind": r.kind,
                "job": job,
                "needs_attention": job.id in waiting,
                "parse_error_message": messages.get(job.parse_error_id) if job.status == "failed" else None,
            })
        else:
            out.append({"kind": r.kind, "parse_error": error_rows[r.id]})
    return out, int(total)
