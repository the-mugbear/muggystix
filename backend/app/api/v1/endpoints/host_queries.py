"""Query-UX surface for the Hosts boolean DSL (per-user, per-project).

Three concerns, all hanging off the command bar on the rewritten Hosts
page and intentionally split out of ``hosts.py`` (already over the
file-size policy limit):

* ``GET  /hosts/query/schema``    — field + example catalogue for the
  autocomplete, syntax-help popover, and template gallery.  Sourced from
  the DSL registry so it can never drift from what the parser accepts.
* ``POST /hosts/query/validate``  — parse ``q`` without executing it and,
  when valid, return the live match count.  Always HTTP 200; validity
  lives in the body so the frontend can lint inline without treating a
  bad draft query as an error response.
* ``GET/POST/DELETE /hosts/query/history`` — the recent-queries list.

Mounted at the ``/hosts`` prefix; the host-detail route is typed
``/{host_id:int}`` so these static ``/query/...`` paths don't collide.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import get_current_project
from app.api.v1.endpoints.auth import get_current_user
from app.db import models
from app.db.models import HostQueryHistory
from app.db.models_auth import User
from app.db.models_project import Project
from app.db.session import get_db
from app.services.host_query import build_filtered_host_query
from app.services.host_query_dsl import (
    DSLError,
    count_leaves,
    parse_query,
    schema as dsl_schema,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

# Keep the recency list short and useful rather than an audit trail.
_HISTORY_KEEP = 50


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class QueryFieldSchema(BaseModel):
    name: str
    aliases: List[str]
    value_source: str
    trgm: bool
    enum_values: List[str]
    # Human help text — the single source of truth lives in the DSL registry
    # (host_query_dsl), so the command bar and user-guide reference render
    # these live and never drift.
    description: str = ""
    enum_descriptions: Dict[str, str] = Field(default_factory=dict)


class QueryExampleSchema(BaseModel):
    label: str
    q: str


class QuerySchemaResponse(BaseModel):
    fields: List[QueryFieldSchema]
    examples: List[QueryExampleSchema]


class QueryErrorSchema(BaseModel):
    message: str
    position: Optional[int] = None


class QueryValidateRequest(BaseModel):
    q: str


class QueryValidateResponse(BaseModel):
    valid: bool
    error: Optional[QueryErrorSchema] = None
    leaf_count: Optional[int] = None
    match_count: Optional[int] = None


class QueryHistoryEntry(BaseModel):
    id: int
    q: str
    result_count: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QueryHistoryCreate(BaseModel):
    q: str = Field(..., min_length=1, max_length=2000)
    result_count: Optional[int] = None


# ---------------------------------------------------------------------------
# Schema / validate
# ---------------------------------------------------------------------------

@router.get(
    "/query/schema",
    response_model=QuerySchemaResponse,
    summary="Query DSL field + example catalogue",
)
def get_query_schema():
    """Field names, aliases, value sources, and starter examples for the
    command bar.  Single source of truth = the DSL registry."""
    return dsl_schema()


@router.post(
    "/query/validate",
    response_model=QueryValidateResponse,
    summary="Validate a boolean query and preview its match count",
)
def validate_query(
    body: QueryValidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Parse ``q`` (no execution) and, if valid, count matching hosts.

    Returns 200 whether or not the query parses — ``valid`` + ``error``
    carry the result so the UI can underline the offending position
    without a 4xx round-trip."""
    q = (body.q or "").strip()
    if not q:
        return QueryValidateResponse(valid=True, leaf_count=0, match_count=None)
    try:
        node = parse_query(q)
    except DSLError as exc:
        return QueryValidateResponse(
            valid=False,
            error=QueryErrorSchema(message=exc.message, position=exc.position),
        )
    # The count preview fires on every keystroke (debounced). At scale an
    # expensive DSL query (evidence search, broad bare terms) could pin a
    # worker, and a client abort doesn't reliably cancel SQL already running.
    # Cap it with a per-statement timeout (Postgres); on timeout the syntax
    # result is still valid, we just omit the count rather than block.
    match_count: Optional[int] = None
    try:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(text("SET LOCAL statement_timeout = '3000'"))
        match_count = int(
            build_filtered_host_query(db, current_user, q=q, project_id=project.id)
            .with_entities(func.count(models.Host.id))
            .scalar()
            or 0
        )
    except OperationalError as exc:
        # Only swallow a statement_timeout cancellation (SQLSTATE 57014 —
        # query_canceled); the transaction is aborted, so roll back and leave
        # the count unavailable. Any OTHER operational error (lost connection,
        # real DB defect) must NOT masquerade as a valid zero-match query — log
        # and re-raise it.
        db.rollback()
        pgcode = getattr(getattr(exc, "orig", None), "pgcode", None)
        if pgcode != "57014":
            logger.exception("validate_query count failed (non-timeout)")
            raise
        match_count = None
    return QueryValidateResponse(
        valid=True,
        leaf_count=count_leaves(node),
        match_count=match_count,
    )


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@router.get(
    "/query/history",
    response_model=List[QueryHistoryEntry],
    summary="Recent boolean queries for the current user + project",
)
def list_query_history(
    limit: int = Query(20, ge=1, le=_HISTORY_KEEP),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    return (
        db.query(HostQueryHistory)
        .filter(
            HostQueryHistory.user_id == current_user.id,
            HostQueryHistory.project_id == project.id,
        )
        .order_by(HostQueryHistory.created_at.desc(), HostQueryHistory.id.desc())
        .limit(limit)
        .all()
    )


@router.post(
    "/query/history",
    response_model=QueryHistoryEntry,
    status_code=201,
    summary="Record a committed query (deduped + trimmed)",
)
def record_query_history(
    body: QueryHistoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Append a committed query.  If it matches the most recent entry it
    is bumped to "now" instead of duplicated; the list is then trimmed to
    the newest ``_HISTORY_KEEP``."""
    q = body.q.strip()
    base = db.query(HostQueryHistory).filter(
        HostQueryHistory.user_id == current_user.id,
        HostQueryHistory.project_id == project.id,
    )
    newest = base.order_by(HostQueryHistory.created_at.desc(), HostQueryHistory.id.desc()).first()

    if newest is not None and newest.q == q:
        newest.created_at = func.now()
        newest.result_count = body.result_count
        entry = newest
    else:
        entry = HostQueryHistory(
            user_id=current_user.id,
            project_id=project.id,
            q=q,
            result_count=body.result_count,
        )
        db.add(entry)
    db.flush()

    # Trim anything past the newest _HISTORY_KEEP for this user+project.
    keep_ids = [
        row.id
        for row in base.order_by(
            HostQueryHistory.created_at.desc(), HostQueryHistory.id.desc()
        ).limit(_HISTORY_KEEP).all()
    ]
    if keep_ids:
        base.filter(HostQueryHistory.id.notin_(keep_ids)).delete(synchronize_session=False)

    db.commit()
    db.refresh(entry)
    return entry


@router.delete(
    "/query/history/{entry_id}",
    status_code=204,
    summary="Delete one recent-query entry",
)
def delete_query_history_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    deleted = (
        db.query(HostQueryHistory)
        .filter(
            HostQueryHistory.id == entry_id,
            HostQueryHistory.user_id == current_user.id,
            HostQueryHistory.project_id == project.id,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    # Idempotent: deleting an already-gone (or someone else's) entry is a
    # no-op 204 rather than a 404, matching the "clear my recents" UX.
    _ = deleted
    return None


@router.delete(
    "/query/history",
    status_code=204,
    summary="Clear the current user's recent queries for this project",
)
def clear_query_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    db.query(HostQueryHistory).filter(
        HostQueryHistory.user_id == current_user.id,
        HostQueryHistory.project_id == project.id,
    ).delete(synchronize_session=False)
    db.commit()
    return None
