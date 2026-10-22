"""The Query page (v2.509.0): pipe queries over hosts, ports and scanner
observations, and the saved / shared ones.

* ``GET /query?q=`` — run one (``query_service.run``); a query that cannot be
  read is a 400 with the character ``position``.  Every run, refused or not,
  goes into the caller's history (``query_history_service``, v2.512.0).
* ``GET /query/history``, ``DELETE /query/history[/{id}]`` — that history.
* ``GET /query/values`` — a text column's values, for the editor.
* ``GET /query/parse`` — a query as the builder shows it.
* ``GET /query/schema`` — datasets, columns, commands and caps.
* ``GET / POST /queries/saved``, ``PATCH / DELETE /queries/saved/{id}``.

Mounted under ``/projects/{project_id}``.  Every member reads (the three
datasets are viewer pages) and saves their own; only a query's creator or a
project admin changes or deletes it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, get_current_user, require_project_role
from app.api.downloads import attachment
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db, is_statement_timeout
from app.services import query_history_service, query_service
from app.services import saved_query_service as saved

router = APIRouter(dependencies=[Depends(get_current_user)])

QUERY_TEXT = Query(..., min_length=1, max_length=4000, description="The query: a dataset, then | commands.")


class QueryColumnOut(BaseModel):
    name: str
    type: str


class QueryAnswer(BaseModel):
    query: str
    dataset: str
    kind: str = Field(..., description="rows (a listing) or stats (stats / top)")
    columns: List[QueryColumnOut]
    rows: List[List[Any]]
    total: int = Field(..., description="Rows that matched (a listing) or groups (stats / top) — the whole answer")
    limit: int = Field(..., description="The most this answer shows")
    capped: bool = Field(..., description="head asked for more than the cap")
    drilldowns: Optional[List[Optional[str]]] = Field(
        None, description="stats / top: for each row, the query listing exactly that group's rows",
    )
    links: Optional[List[Optional[str]]] = Field(
        None, description="A listing: for each row, its page (the host, the finding, the endpoint)",
    )
    by: Optional[List[str]] = Field(None, description="stats / top: the group columns (the answer's first columns)")
    truncated_values: List[List[Any]] = Field(
        default_factory=list,
        description="[row, column] of each values() list cut at the cap (20): the cell holds the first 20",
    )
    hint: Optional[str] = Field(
        None,
        description="An empty answer whose `where` compares text with `=`: that `=` is exact and "
                    "case-sensitive, and how many rows would match ignoring case",
    )
    start: int = Field(0, description="A listing: where these rows begin in the whole answer (0 = its first row)")
    next_after: Optional[str] = Field(
        None, description="A listing with more rows past these (and no `head`): pass it as `after` for the next page",
    )
    hosts_q: Optional[str] = Field(
        None,
        description="A `hosts` listing with no `where`: the Hosts-page query listing exactly these hosts "
                    "(\"\" = every host); null for any other answer",
    )


# One declaration for both doors (the page's and the agents').
QUERY_AFTER = Query(
    None, min_length=1, max_length=2000,
    description="A listing's next page: the `next_after` of the page before. The cap is the page's size; "
                "`total` stays the whole answer and `start` says where the page begins.",
)


@router.get("/query", response_model=QueryAnswer, summary="Run a query (a pipe query language)")
def run_query(
    q: str = QUERY_TEXT,
    after: Optional[str] = QUERY_AFTER,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """Run it, and record it in the caller's history (a refused query too).
    A later page (``after``) is not recorded again."""
    return query_history_service.run_and_record(db, project.id, user, q, after=after)


class BuilderCondition(BaseModel):
    column: str
    op: str
    value: str


class BuilderMeasure(BaseModel):
    fn: str = Field(..., description="count, dc, min, max, avg, sum, values — or top")
    column: str = ""
    # The answer's name for it when the query names it (`as x`).
    as_: Optional[str] = Field(None, alias="as")

    model_config = {"populate_by_name": True}


class BuilderShape(BaseModel):
    dataset: str
    terms: str
    conditions: List[BuilderCondition]
    by: List[str]
    measure: BuilderMeasure
    most_first: bool
    head: int = Field(..., description="head N (0: none); for top, its limit")


class QueryParseOut(BaseModel):
    shape: Optional[BuilderShape] = Field(None, description="What the builder shows; null when it cannot show all of it")
    reason: Optional[str] = None


@router.get("/query/parse", response_model=QueryParseOut, response_model_by_alias=True,
            summary="A query as the page's builder shows it (no database read)")
def parse_query_for_builder(q: str = QUERY_TEXT):
    """The builder opens on the editor's query (review Q9): the grammar is the
    server's, so the page asks here rather than parsing the text itself.  A
    query that does not read is a 400 with its ``position``, as on Run; one
    the builder cannot show whole says why (``reason``).  A typing aid — no
    agent route."""
    return query_service.builder_shape(q)


class QueryHistoryEntryOut(BaseModel):
    id: int
    q: str
    by_agent: bool = Field(..., description="Run by the person's agent (assist_query), not on the page")
    result_count: Optional[int] = Field(None, description="The answer's total at the last run; null when refused")
    error: Optional[str] = Field(None, description="Why the last run was refused — correct it and run again")
    error_position: Optional[int] = None
    run_count: int
    first_run_at: Optional[datetime] = None
    last_run_at: datetime

    @classmethod
    def of(cls, row) -> "QueryHistoryEntryOut":
        return cls(id=row.id, q=row.q, by_agent=row.by_agent, result_count=row.result_count, error=row.error,
                   error_position=row.error_position, run_count=row.run_count, first_run_at=row.created_at,
                   last_run_at=row.last_run_at)


class QueryHistoryPage(BaseModel):
    items: List[QueryHistoryEntryOut]
    total: int = Field(..., description="Entries matching `contains` (at most 200 are kept)")


HISTORY_LIMIT = Query(50, ge=1, le=query_history_service.CAPS[query_history_service.QUERY])
HISTORY_CONTAINS = Query(None, max_length=200, description="Only entries whose text contains this (any case)")


def history_page(db: Session, project_id: int, user: User, contains: Optional[str], limit: int, offset: int):
    """The caller's Query history — the page's read and the agents' (one call)."""
    rows, total = query_history_service.entries(
        db, user_id=user.id, project_id=project_id, kind=query_history_service.QUERY,
        contains=contains, limit=limit, offset=offset,
    )
    return [QueryHistoryEntryOut.of(r) for r in rows], total


@router.get("/query/history", response_model=QueryHistoryPage, summary="My queries, newest first")
def list_query_history(
    contains: Optional[str] = HISTORY_CONTAINS,
    limit: int = HISTORY_LIMIT,
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """The caller's own (their agent's labelled), refused ones included, the
    newest 200 kept."""
    items, total = history_page(db, project.id, user, contains, limit, offset)
    return QueryHistoryPage(items=items, total=total)


@router.delete("/query/history/{entry_id}", status_code=204, summary="Forget one of my queries")
def delete_query_history_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    query_history_service.delete(db, user_id=user.id, project_id=project.id,
                                 kind=query_history_service.QUERY, entry_id=entry_id)
    db.commit()
    return Response(status_code=204)


@router.delete("/query/history", status_code=204, summary="Forget all my queries in this project")
def clear_query_history(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    query_history_service.clear(db, user_id=user.id, project_id=project.id, kind=query_history_service.QUERY)
    db.commit()
    return Response(status_code=204)


class ColumnValuesOut(BaseModel):
    supported: bool = Field(..., description="False for a column that is not text: nothing to offer")
    values: List[Dict[str, Any]] = Field(default_factory=list, description="{value, count}, most rows first")
    timed_out: bool = False


@router.get("/query/values", response_model=ColumnValuesOut, summary="A text column's values, for the editor")
def query_column_values(
    dataset: str = Query(..., min_length=1, max_length=40),
    column: str = Query(..., min_length=1, max_length=60),
    contains: str = Query("", max_length=200),
    limit: int = Query(12, ge=1, le=query_service.VALUES_SUGGEST_CAP),
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """What the editor offers after `where col = "`: the project's values of
    that column containing what was typed.  Asked while typing, so it has a
    short limit of its own: on a timeout the list is empty and says why (the
    Hosts page's value suggestions do the same)."""
    try:
        db.execute(text("SET LOCAL statement_timeout = '2000'"))
        values = query_service.column_values(db, project.id, dataset, column, contains, limit)
    except OperationalError as exc:
        db.rollback()
        if not is_statement_timeout(exc):
            raise
        return ColumnValuesOut(supported=True, timed_out=True)
    return ColumnValuesOut(supported=values is not None, values=values or [])


@router.get(
    "/query/export.csv",
    summary="A query's answer as CSV (auditor and above, as every export)",
    dependencies=[Depends(require_project_role(ProjectRole.AUDITOR))],
    response_class=PlainTextResponse,
)
def export_query_csv(
    q: str = QUERY_TEXT,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    """The page's answer, with a listing cap of ``EXPORT_ROW_CAP`` rows (groups
    stay at the page's cap).  The file's first line is the column names."""
    answer = query_service.run(db, project.id, user, q, row_cap=query_service.EXPORT_ROW_CAP)
    return PlainTextResponse(
        query_service.to_csv(answer), media_type="text/csv; charset=utf-8",
        # What the file holds against the whole answer (the page says "the
        # first N of M" — external review 2026-10-16, U1).
        headers={**attachment(f"query-{answer['dataset']}.csv"),
                 "X-Total-Count": str(answer["total"]), "X-Row-Count": str(len(answer["rows"])),
                 "X-Answer-Kind": answer["kind"],
                 "X-Cap": str(query_service.GROUP_CAP if answer["kind"] == "stats" else query_service.EXPORT_ROW_CAP)},
    )


@router.get("/query/schema", summary="What a query can name: datasets, columns, commands, caps")
def query_schema() -> Dict[str, Any]:
    return query_service.schema()


class SavedQueryOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    query: str
    shared: bool
    created_by: Optional[str] = None
    mine: bool
    can_modify: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SavedQueryIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    query: str = Field(..., min_length=1, max_length=4000)
    description: Optional[str] = Field(None, max_length=2000)
    shared: bool = False


class SavedQueryPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    query: Optional[str] = Field(None, min_length=1, max_length=4000)
    description: Optional[str] = Field(None, max_length=2000)
    shared: Optional[bool] = None


@router.get("/queries/saved", response_model=List[SavedQueryOut], summary="My saved queries and the team's shared ones")
def list_saved_queries(
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    return saved.list_visible(db, project.id, user)


@router.post("/queries/saved", response_model=SavedQueryOut, status_code=201, summary="Save a query")
def create_saved_query(
    body: SavedQueryIn,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    return saved.create(db, project.id, user, name=body.name, query=body.query,
                        description=body.description, shared=body.shared)


@router.patch("/queries/saved/{query_id}", response_model=SavedQueryOut, summary="Rename, change or share a saved query")
def update_saved_query(
    query_id: int,
    body: SavedQueryPatch,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    return saved.update(db, project.id, user, query_id, body.model_dump(exclude_unset=True))


@router.delete("/queries/saved/{query_id}", status_code=204, summary="Delete a saved query")
def delete_saved_query(
    query_id: int,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    saved.delete(db, project.id, user, query_id)
    return Response(status_code=204)
