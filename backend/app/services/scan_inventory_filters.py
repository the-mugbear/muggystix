"""The /scans inventory filters — one implementation for every scan list.

Lived in ``endpoints/scans.py`` as ``_apply_scan_inventory_filters`` until the
2026-10-01 review (B4): the agents' scan list (``agent_assist.py``) imported
the private helper from the router file.  A filter added here reaches the
Scans page, its summary, the batches, the history and the agent read at once
— add a new filter HERE, never per endpoint.
"""
from __future__ import annotations

from sqlalchemy import func

from app.db import models
from app.services.host_query_common import escape_like


def apply_scan_inventory_filters(query, *, search, tool, created_after, uploaded_by=None):
    """Apply the /scans page's search / tool / date-range / uploader filters.

    Shared by the list endpoint and the summary endpoint so the headline
    totals can never drift from the rows the table shows.  Assumes
    ``models.Scan`` is part of the query's FROM clause.
    """
    if search and search.strip():
        # ``%`` and ``_`` in the term are text, not wildcards: file names are
        # full of underscores.
        needle = f"%{escape_like(search.strip())}%"
        query = query.filter(
            (models.Scan.filename.ilike(needle, escape="\\"))
            | (models.Scan.tool_name.ilike(needle, escape="\\"))
            | (models.Scan.scan_type.ilike(needle, escape="\\"))
        )
    if tool and tool.strip():
        tool_lower = tool.strip().lower()
        query = query.filter(
            (func.lower(models.Scan.tool_name) == tool_lower)
            | (func.lower(models.Scan.scan_type) == tool_lower)
        )
    if created_after is not None:
        query = query.filter(models.Scan.created_at >= created_after)
    if uploaded_by is not None:
        query = query.filter(models.Scan.uploaded_by_id == uploaded_by)
    return query


def apply_job_inventory_filters(query, *, search, tool, created_after=None, uploaded_by=None):
    """The same four filters over UPLOADS (``ingestion_jobs``) — the import
    history's rows that have no scan, its status views, and their counts.

    One implementation for every upload list: the import history
    (``GET /scans/history`` with a status), its chips' counts and
    ``GET /ingestion-results`` all call it.  The search reads what an upload
    row shows — its filename and what went wrong (``error_message``,
    ``last_error``); the tool is the upload's own ``tool_name``; the date is
    when it was uploaded; the uploader is who submitted it.  Assumes
    ``models.IngestionJob`` is part of the query's FROM clause.
    """
    Job = models.IngestionJob
    if search and search.strip():
        needle = f"%{escape_like(search.strip())}%"
        query = query.filter(
            Job.original_filename.ilike(needle, escape="\\")
            | Job.error_message.ilike(needle, escape="\\")
            | Job.last_error.ilike(needle, escape="\\")
        )
    if tool and tool.strip():
        query = query.filter(func.lower(Job.tool_name) == tool.strip().lower())
    if created_after is not None:
        query = query.filter(Job.created_at >= created_after)
    if uploaded_by is not None:
        query = query.filter(Job.submitted_by_id == uploaded_by)
    return query
