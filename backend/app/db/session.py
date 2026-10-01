import logging

from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings
from app.core.request_context import instrument_engine

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=settings.SQLALCHEMY_CONNECT_ARGS,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
)
# Per-request statement count and SQL time for the access line and
# ``Server-Timing`` (review 2026-10-01).  A no-op outside a request, so the
# workers that share this engine record nothing.
instrument_engine(engine)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def _limit_statements(db) -> None:
    """Put the API statement timeout on ``db``, a request's session (R23).

    ``SET LOCAL`` at the start of every top-level transaction the session
    opens, so the limit covers the whole request (a handler that commits and
    then reads more included) and ends with each transaction: the pooled
    connection always goes back without it.  That is what keeps it off
    everything that is not a request — workers, startup tasks and scripts open
    ``SessionLocal()`` themselves and never pass through here.  Costs one
    tiny statement per transaction.
    """
    limit = settings.API_STATEMENT_TIMEOUT_MS
    if limit <= 0 or db.get_bind().dialect.name != "postgresql":
        return

    @event.listens_for(db, "after_begin")
    def _set_local(session, transaction, connection):
        if transaction.parent is not None or session.info.get(_NO_TIMEOUT):
            return
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = {int(limit)}")


def get_db():
    db = SessionLocal()
    _limit_statements(db)
    try:
        yield db
    except DBAPIError as exc:
        if not is_statement_timeout(exc):
            raise
        # A statement ran into API_STATEMENT_TIMEOUT_MS.  Say so — the raw
        # exception would be an anonymous 500 — and say it is worth retrying
        # narrower rather than at once.
        logger.warning("statement cancelled after %d ms (API_STATEMENT_TIMEOUT_MS)",
                       settings.API_STATEMENT_TIMEOUT_MS)
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail=(
                "This request needed a database query that ran longer than the server allows "
                "and was stopped. Narrow the filter or try again."
            ),
        ) from exc
    finally:
        db.close()


# SQLSTATE for "canceling statement due to statement timeout" (and a user
# cancel) — what Postgres raises when API_STATEMENT_TIMEOUT_MS is exceeded.
QUERY_CANCELED_SQLSTATE = "57014"
_NO_TIMEOUT = "bluestick_no_statement_timeout"


def is_statement_timeout(exc: BaseException) -> bool:
    """True when ``exc`` is SQLAlchemy's wrapper around a cancelled statement
    (review 2026-10-01 R23)."""
    orig = getattr(exc, "orig", None)
    return getattr(orig, "pgcode", None) == QUERY_CANCELED_SQLSTATE


def disable_statement_timeout(db) -> None:
    """Lift the API statement timeout for the REST of this request's session
    (R23) — for the few request paths that run long statements by design
    (streamed exports).  Does nothing where no limit applies.
    """
    db.info[_NO_TIMEOUT] = True
    if settings.API_STATEMENT_TIMEOUT_MS <= 0:
        return
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(text("SET LOCAL statement_timeout = 0"))
