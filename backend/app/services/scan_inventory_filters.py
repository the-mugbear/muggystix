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


def apply_scan_inventory_filters(query, *, search, tool, created_after, uploaded_by=None):
    """Apply the /scans page's search / tool / date-range / uploader filters.

    Shared by the list endpoint and the summary endpoint so the headline
    totals can never drift from the rows the table shows.  Assumes
    ``models.Scan`` is part of the query's FROM clause.
    """
    if search and search.strip():
        needle = f"%{search.strip()}%"
        query = query.filter(
            (models.Scan.filename.ilike(needle))
            | (models.Scan.tool_name.ilike(needle))
            | (models.Scan.scan_type.ilike(needle))
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
