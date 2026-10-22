"""One person's query history, for the Hosts page and the Query page (v2.512.0).

The owner, 2026-10-17: the Query page keeps its history on the server — the
person's own, their agent's queries labelled, refused queries kept and
labelled so they can be corrected, the newest 200 kept (FIFO).  The Hosts
page's recent queries are the same thing with a cap of 50: ONE table
(``query_history``, told apart by ``kind``) and this one module write both.

* ``record`` is an upsert on (person, project, kind, by_agent, the text):
  running a query again moves it to the top and counts the run — two runs at
  once cannot make two rows (a unique index) — then the oldest past the
  kind's cap are dropped.
* ``run_and_record`` is the Query page's run, on both doors (the page's
  ``GET /query`` and the agents' ``/agent/assist/query``): the answer's size,
  or the refusal (any ``DSLError`` the API answers as a 400: its message and
  position), is recorded.
  A failure to record never fails the query.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import QueryHistory
from app.db.models_auth import User
from app.db.session import is_statement_timeout
from app.services import query_service
from app.services.host_query_dsl import DSLError

logger = logging.getLogger(__name__)

HOSTS = "hosts"
QUERY = "query"
# The newest N kept per person, project and page (FIFO).
CAPS = {HOSTS: 50, QUERY: 200}


def record(
    db: Session, *, user_id: int, project_id: int, kind: str, text: str,
    result_count: Optional[int] = None, error: Optional[str] = None,
    error_position: Optional[int] = None, by_agent: bool = False,
) -> Optional[QueryHistory]:
    """Add a run of ``text`` to the person's history (no commit).  None for
    an empty text."""
    q = text.strip()
    if not q:
        return None
    now = func.clock_timestamp()
    stmt = pg_insert(QueryHistory).values(
        user_id=user_id, project_id=project_id, kind=kind, q=q, by_agent=by_agent,
        result_count=result_count, error=error, error_position=error_position,
        run_count=1, created_at=now, last_run_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[QueryHistory.user_id, QueryHistory.project_id, QueryHistory.kind,
                        QueryHistory.by_agent, func.md5(QueryHistory.q)],
        set_={
            "run_count": QueryHistory.run_count + 1,
            "last_run_at": now,
            "result_count": stmt.excluded.result_count,
            "error": stmt.excluded.error,
            "error_position": stmt.excluded.error_position,
        },
    ).returning(QueryHistory.id)
    entry_id = db.execute(stmt).scalar_one()
    _trim(db, user_id, project_id, kind)
    return db.query(QueryHistory).populate_existing().filter(QueryHistory.id == entry_id).one()


def _mine(db: Session, user_id: int, project_id: int, kind: str):
    return db.query(QueryHistory).filter(
        QueryHistory.user_id == user_id, QueryHistory.project_id == project_id, QueryHistory.kind == kind,
    )


def _newest_first(query):
    return query.order_by(QueryHistory.last_run_at.desc(), QueryHistory.id.desc())


def _trim(db: Session, user_id: int, project_id: int, kind: str) -> None:
    """FIFO: everything past the newest ``CAPS[kind]`` goes."""
    keep = [row.id for row in _newest_first(_mine(db, user_id, project_id, kind)).with_entities(QueryHistory.id)
            .limit(CAPS[kind]).all()]
    if keep:
        _mine(db, user_id, project_id, kind).filter(QueryHistory.id.notin_(keep)).delete(synchronize_session=False)


def entries(
    db: Session, *, user_id: int, project_id: int, kind: str, contains: Optional[str] = None,
    limit: int = 50, offset: int = 0,
) -> Tuple[List[QueryHistory], int]:
    """The person's history, newest first, and how many entries match."""
    query = _mine(db, user_id, project_id, kind)
    if contains and contains.strip():
        pattern = contains.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(QueryHistory.q.ilike(f"%{pattern}%", escape="\\"))
    total = query.count()
    return _newest_first(query).offset(offset).limit(limit).all(), total


def delete(db: Session, *, user_id: int, project_id: int, kind: str, entry_id: int) -> None:
    """Idempotent: another person's or a gone entry is a no-op."""
    _mine(db, user_id, project_id, kind).filter(QueryHistory.id == entry_id).delete(synchronize_session=False)


def clear(db: Session, *, user_id: int, project_id: int, kind: str) -> None:
    _mine(db, user_id, project_id, kind).delete(synchronize_session=False)


def run_and_record(db: Session, project_id: int, user: User, text: str, *, by_agent: bool = False,
                   after: Optional[str] = None) -> Dict[str, Any]:
    """``query_service.run``, recorded in the person's Query history — the
    answer's size, or the refusal (and re-raised).  A timeout or any other
    failure is not a query to correct, so it is not recorded.  A later page
    of a listing (``after``) is the same query read further, not a run: it
    is not recorded."""
    if after is not None:
        return query_service.run(db, project_id, user, text, after=after)
    try:
        answer = query_service.run(db, project_id, user, text)
    except DSLError as exc:
        # Every refusal the API answers as a 400: the grammar's (QueryError)
        # and the Hosts language's raised while its terms are evaluated
        # (`assigned:<nobody>`, `subnet:10.0.5` — review 2026-10-16, N2).
        _record_quietly(db, user, project_id, text, by_agent, error=exc.message, error_position=exc.position)
        raise
    _record_quietly(db, user, project_id, text, by_agent, result_count=answer["total"])
    return answer


def _record_quietly(db: Session, user: User, project_id: int, text: str, by_agent: bool, **outcome) -> None:
    """Record and commit; a failure is logged, never the query's failure."""
    try:
        with db.begin_nested():
            record(db, user_id=user.id, project_id=project_id, kind=QUERY, text=text, by_agent=by_agent, **outcome)
        db.commit()
    except Exception as exc:  # noqa: BLE001 — the history is a convenience; the answer stands
        if is_statement_timeout(exc):
            raise
        logger.warning("Could not record a query in the history", exc_info=True)
        db.rollback()
