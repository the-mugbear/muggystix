"""Request-correlation id middleware + webhook dispatch result (audit B5 / A3)."""
from app.services.webhook_dispatcher import WebhookDispatcher, DispatchResult


def test_response_carries_minted_request_id(client):
    r = client.get("/health")
    assert r.status_code in (200, 503)
    assert r.headers.get("x-request-id")  # minted, non-empty


def test_inbound_request_id_is_honored(client):
    r = client.get("/health", headers={"X-Request-ID": "corr-abc-123"})
    assert r.headers.get("x-request-id") == "corr-abc-123"


def test_api_responses_are_no_store(client):
    """Every /api/ response must carry Cache-Control: no-store so a browser/proxy
    can't serve a stale host list (e.g. a 'Not Reviewed' default showing hosts
    that were just reviewed). Even a 404 routes through the middleware, so the
    header presence + scoping is what we pin — no auth/data setup needed."""
    r = client.get("/api/v1/__definitely_not_a_route__")
    assert r.headers.get("cache-control") == "no-store"


def test_non_api_responses_are_not_forced_no_store(client):
    """The header is scoped to the data API; /health (served at root, not under
    /api) is left alone."""
    r = client.get("/health")
    assert r.headers.get("cache-control") != "no-store"


def test_dispatch_returns_split_result_no_targets(db_session, test_project):
    res = WebhookDispatcher(db_session).stage(
        project_id=test_project.id, event="note_mention", title="t",
    )
    assert isinstance(res, DispatchResult)
    assert res == DispatchResult(queued=0, dropped=0)


# ---------------------------------------------------------------------------
# SQL timing per request + the API statement timeout (review 2026-10-01).
# ---------------------------------------------------------------------------
import logging
import re

import pytest
from sqlalchemy import text

from app.core import request_context
from app.core.config import Settings
from app.core.request_context import SqlStats, instrument_engine, sql_stats_var


class _Lines(logging.Handler):
    """``app.access`` does not propagate to the root logger caplog listens on."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def access_lines():
    handler = _Lines()
    log = logging.getLogger("app.access")
    previous = log.level
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    yield handler.lines
    log.removeHandler(handler)
    log.setLevel(previous)


@pytest.fixture
def timed_engine(test_engine):
    """The suite talks to its own engine, not ``app.db.session.engine`` — attach
    the same hooks the app engine carries (idempotent)."""
    instrument_engine(test_engine)
    instrument_engine(test_engine)  # a second call must not double-count
    return test_engine


def test_statements_are_counted_only_inside_a_request(db_session, timed_engine):
    assert sql_stats_var.get() is None
    db_session.execute(text("SELECT 1"))  # outside a request: nothing recorded

    stats = SqlStats()
    token = sql_stats_var.set(stats)
    try:
        db_session.execute(text("SELECT 1"))
        db_session.execute(text("SELECT 2"))
    finally:
        sql_stats_var.reset(token)
    assert stats.count == 2
    assert stats.ms >= 0

    db_session.execute(text("SELECT 3"))
    assert stats.count == 2


def test_a_failed_statement_is_counted_once(db_session, timed_engine):
    """``after_cursor_execute`` never fires for a failed statement; the error
    hook counts it — once, exactly as a successful one would be."""

    def run(sql, fails):
        stats = SqlStats()
        token = sql_stats_var.set(stats)
        try:
            nested = db_session.begin_nested()
            if fails:
                with pytest.raises(Exception):
                    db_session.execute(text(sql))
            else:
                db_session.execute(text(sql))
            nested.rollback()
        finally:
            sql_stats_var.reset(token)
        return stats.count

    assert run("SELECT * FROM __no_such_table__", True) == run("SELECT 1", False)


def test_route_template_names_each_parameter_in_path_order():
    template = request_context._route_template
    assert template({"path": "/x"}) is None  # nothing matched
    assert template({"route": object(), "path": "/health"}) == "/health"
    # Two parameters with the SAME value keep their own names.
    assert template({
        "route": object(),
        "path": "/api/v1/projects/7/hosts/7/notes",
        "path_params": {"project_id": 7, "host_id": 7},
    }) == "/api/v1/projects/{project_id}/hosts/{host_id}/notes"


def test_access_line_and_server_timing_carry_sql_and_the_route_template(
    client, db_session, test_project, timed_engine, access_lines,
):
    url = f"/api/v1/projects/{test_project.id}/hosts/"
    r = client.get(url)
    assert r.status_code == 200, r.text

    timing = r.headers.get("server-timing", "")
    assert re.search(r"app;dur=[\d.]+", timing)
    assert re.search(r"db;dur=[\d.]+", timing)

    line = next(l for l in access_lines if l.startswith(f"GET {url}"))
    m = re.search(r"db_ms=(\d+) db_n=(\d+) route=(\S+)", line)
    assert m, line
    assert int(m.group(2)) >= 1  # the handler queried the database
    # The TEMPLATE, so every project's requests group under one name.
    assert m.group(3) == "/api/v1/projects/{project_id}/hosts/"
    assert str(test_project.id) not in m.group(3)


def test_unmatched_route_logs_a_dash_template(client, access_lines):
    client.get("/api/v1/__definitely_not_a_route__")
    assert any("db_n=0 route=-" in l for l in access_lines), access_lines


def test_app_engine_is_instrumented():
    from sqlalchemy import event
    from app.db.session import engine

    assert event.contains(engine, "before_cursor_execute", request_context._before_cursor_execute)
    assert event.contains(engine, "after_cursor_execute", request_context._after_cursor_execute)


# --- API statement timeout (R23) -------------------------------------------

def _timeout_app():
    """A two-route app on the REAL ``get_db`` (the suite's ``client`` overrides
    it, so the limit is exercised here)."""
    from fastapi import Depends, FastAPI
    from app.db.session import disable_statement_timeout, get_db

    mini = FastAPI()

    @mini.get("/slow")
    def slow(seconds: float = 1.0, commit_first: bool = False, db=Depends(get_db)):
        if commit_first:
            db.execute(text("SELECT 1"))
            db.commit()  # the limit must come back with the next transaction
        db.execute(text("SELECT pg_sleep(:s)"), {"s": seconds})
        return {"ok": True}

    @mini.get("/exempt")
    def exempt(db=Depends(get_db)):
        disable_statement_timeout(db)
        db.execute(text("SELECT pg_sleep(0.3)"))
        db.commit()
        db.execute(text("SELECT pg_sleep(0.3)"))  # still lifted after a commit
        return {"ok": True}

    return mini


@pytest.fixture
def timeout_client(db_session, monkeypatch):
    from fastapi.testclient import TestClient
    from app.db import session as session_module

    if db_session.get_bind().dialect.name != "postgresql":
        pytest.skip("statement_timeout is Postgres-only")
    monkeypatch.setattr(session_module.settings, "API_STATEMENT_TIMEOUT_MS", 100, raising=False)
    with TestClient(_timeout_app()) as mini_client:
        yield mini_client


def test_a_request_statement_over_the_limit_is_a_503_not_a_500(timeout_client):
    r = timeout_client.get("/slow")
    assert r.status_code == 503, r.text
    assert "ran longer than the server allows" in r.json()["detail"]
    # … and the limit still holds in a transaction opened after a commit.
    assert timeout_client.get("/slow", params={"commit_first": True}).status_code == 503
    # A statement inside the limit is untouched.
    assert timeout_client.get("/slow", params={"seconds": 0.01}).status_code == 200


def test_a_request_can_lift_the_limit_for_itself(timeout_client):
    assert timeout_client.get("/exempt").status_code == 200


def test_the_limit_can_be_turned_off(db_session, monkeypatch):
    from fastapi.testclient import TestClient
    from app.db import session as session_module

    if db_session.get_bind().dialect.name != "postgresql":
        pytest.skip("statement_timeout is Postgres-only")
    monkeypatch.setattr(session_module.settings, "API_STATEMENT_TIMEOUT_MS", 0, raising=False)
    with TestClient(_timeout_app()) as mini_client:
        assert mini_client.get("/slow", params={"seconds": 0.3}).status_code == 200


def test_the_limit_never_reaches_a_session_that_is_not_a_request(db_session, monkeypatch):
    """Workers, startup tasks and scripts open ``SessionLocal()`` themselves.
    Their sessions — and the engine's connections — carry no limit, whatever
    the environment says (``env_file: .env`` reaches every service)."""
    from app.db import session as session_module

    monkeypatch.setattr(session_module.settings, "API_STATEMENT_TIMEOUT_MS", 100, raising=False)
    assert "options" not in Settings().SQLALCHEMY_CONNECT_ARGS
    if db_session.get_bind().dialect.name != "postgresql":
        return
    plain = session_module.SessionLocal()   # what a worker does
    try:
        assert plain.execute(text("SHOW statement_timeout")).scalar() in ("0", "0ms")
        plain.execute(text("SELECT pg_sleep(0.3)"))   # well over 100 ms, and it finishes
    finally:
        plain.close()


def test_only_a_cancelled_statement_is_called_a_timeout(db_session):
    from sqlalchemy.exc import DBAPIError
    from app.db.session import is_statement_timeout

    assert not is_statement_timeout(ValueError("x"))
    if db_session.get_bind().dialect.name != "postgresql":
        return
    nested = db_session.begin_nested()
    with pytest.raises(DBAPIError) as other:
        db_session.execute(text("SELECT * FROM __no_such_table__"))
    nested.rollback()
    assert not is_statement_timeout(other.value)
    nested = db_session.begin_nested()
    db_session.execute(text("SET LOCAL statement_timeout = 50"))
    with pytest.raises(DBAPIError) as cancelled:
        db_session.execute(text("SELECT pg_sleep(1)"))
    nested.rollback()
    assert is_statement_timeout(cancelled.value)
