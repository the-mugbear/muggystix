"""The Query page (v2.509.0): SPL-style queries over hosts, ports and scanner
observations, and the saved / shared ones.

* ``GET /query?q=`` — run one (``query_service.run``); a query that cannot be
  read is a 400 with the character ``position``.
* ``GET /query/schema`` — datasets, columns, commands and caps.
* ``GET / POST /queries/saved``, ``PATCH / DELETE /queries/saved/{id}``.

Mounted under ``/projects/{project_id}``.  Every member reads (the three
datasets are viewer pages) and saves their own; only a query's creator or a
project admin changes or deletes it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_project, get_current_user, require_project_role
from app.api.downloads import attachment
from app.db.models_auth import User
from app.db.models_project import Project, ProjectRole
from app.db.session import get_db
from app.services import query_service
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


@router.get("/query", response_model=QueryAnswer, summary="Run a query (SPL-style)")
def run_query(
    q: str = QUERY_TEXT,
    db: Session = Depends(get_db),
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
):
    return query_service.run(db, project.id, user, q)


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
        headers={**attachment(f"query-{answer['dataset']}.csv"),
                 "X-Total-Count": str(answer["total"]), "X-Row-Count": str(len(answer["rows"]))},
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
