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
* ``GET  /hosts/query/suggest``   — one field's values containing what was
  typed, project-wide, for the value autocomplete (v2.405.0).
* ``GET/POST/DELETE /hosts/query/history`` — the recent-queries list.

Mounted at the ``/hosts`` prefix; the host-detail route is typed
``/{host_id:int}`` so these static ``/query/...`` paths don't collide.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import get_current_project
from app.api.deps import get_current_user
from app.db import models
from app.db.models_auth import User
from app.db.models_project import Project
from app.db.session import get_db
from app.services.host_query import build_filtered_host_query
from app.services.host_query_dsl import (
    DSLError,
    count_leaves,
    parse_query,
    provenance_examples,
    schema as dsl_schema,
)
from app.services import query_history_service
from app.services.host_query_suggest import MAX_SUGGESTIONS, suggest as suggest_values

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


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


class QuerySuggestion(BaseModel):
    value: str
    # What the operator knows the value by, when that isn't the value itself
    # (a scan's filename, an ASN's name, a user's full name, a has: meaning).
    label: Optional[str] = None
    # Hosts carrying the value; None where a count would mean nothing.
    count: Optional[int] = None


class QuerySuggestResponse(BaseModel):
    field: str
    # False for an unknown field or one with nothing to enumerate (note text,
    # time windows) — the command bar then shows its own hints.
    supported: bool
    values: List[QuerySuggestion]
    # True when the lookup hit the statement timeout: the list is empty
    # because it was too expensive, not because nothing matches.
    timed_out: bool = False


class QueryHistoryEntry(BaseModel):
    id: int
    q: str
    result_count: Optional[int] = None
    # When it was last run (v2.512.0; it was `created_at`, which a repeat
    # also moved — the same moment under a wrong name).
    last_run_at: datetime

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
def get_query_schema(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Field names, aliases, value sources, and starter examples for the
    command bar.  Single source of truth = the DSL registry; the provenance
    examples are built from this project's RDAP data (v2.423.0)."""
    out = dsl_schema()
    return {**out, "examples": [*out["examples"], *provenance_examples(db, project.id)]}


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
    except DSLError as exc:
        # A value the builder rejects (`scan:nm`, `service:ssh@`, `port:abc`)
        # parses fine and only fails here. It used to escape as a 400, which
        # the command bar reads as "validation unavailable" — hiding exactly
        # the messages written to help (v2.405.1).
        return QueryValidateResponse(
            valid=False,
            error=QueryErrorSchema(message=exc.message, position=exc.position),
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


@router.get(
    "/query/suggest",
    response_model=QuerySuggestResponse,
    summary="Values of one query field that contain the typed text",
)
def suggest_query_values(
    field: str = Query(..., min_length=1, max_length=40),
    prefix: str = Query("", max_length=200),
    limit: int = Query(20, ge=1, le=MAX_SUGGESTIONS),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    """Backs the command bar's value autocomplete: the page's facet lists are
    capped and cascaded by the active filters, so a rare port or a CVE could
    not be completed from them. Fires while typing (debounced), so it is
    capped like the validate count: on a statement timeout the list is empty
    and ``timed_out`` says why."""
    try:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(text("SET LOCAL statement_timeout = '2000'"))
        return suggest_values(
            db, project_id=project.id, current_user=current_user,
            field=field, prefix=prefix, limit=limit,
        )
    except OperationalError as exc:
        db.rollback()
        pgcode = getattr(getattr(exc, "orig", None), "pgcode", None)
        if pgcode != "57014":
            logger.exception("suggest_query_values failed (non-timeout)")
            raise
        return QuerySuggestResponse(field=field, supported=True, values=[], timed_out=True)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@router.get(
    "/query/history",
    response_model=List[QueryHistoryEntry],
    summary="Recent boolean queries for the current user + project",
)
def list_query_history(
    limit: int = Query(20, ge=1, le=query_history_service.CAPS[query_history_service.HOSTS]),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
):
    items, _ = query_history_service.entries(
        db, user_id=current_user.id, project_id=project.id, kind=query_history_service.HOSTS, limit=limit,
    )
    return items


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
    """Record a committed query (``query_history_service``, the Query page's
    too): a query already in the list moves to the top, and the list keeps
    the newest 50."""
    if not body.q.strip():
        raise HTTPException(status_code=422, detail="An empty query is not recorded")
    entry = query_history_service.record(
        db, user_id=current_user.id, project_id=project.id, kind=query_history_service.HOSTS,
        text=body.q, result_count=body.result_count,
    )
    db.commit()
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
    # Idempotent: deleting an already-gone (or someone else's) entry is a
    # no-op 204 rather than a 404, matching the "clear my recents" UX.
    query_history_service.delete(
        db, user_id=current_user.id, project_id=project.id, kind=query_history_service.HOSTS, entry_id=entry_id,
    )
    db.commit()
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
    query_history_service.clear(db, user_id=current_user.id, project_id=project.id, kind=query_history_service.HOSTS)
    db.commit()
    return None
