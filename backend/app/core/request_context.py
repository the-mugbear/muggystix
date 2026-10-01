"""Per-request correlation id + latency (audit B5).

The JWT user API previously emitted no request id and no per-request timing —
only the ``/agent/*`` audit log carried ``duration_ms`` — so a production
"slow / intermittent 500" could only be chased by correlating wall-clock
timestamps across the backend + nginx logs with nothing to join on.

This is a *pure ASGI* middleware (not ``BaseHTTPMiddleware``) on purpose:
BaseHTTPMiddleware buffers the response body, which would break the streaming
CSV inventory export.  Here we only wrap ``send`` to read the status and inject
the ``X-Request-ID`` header — the body bytes stream through untouched.

What it provides:
  * an ``X-Request-ID`` (honored from the inbound header if present, else
    minted) exposed in a contextvar and echoed on every response, so logs,
    nginx, and the client can all be joined on one id;
  * one access-log line per request with ``duration_ms``.

Enriching *every* ``app.*`` log record with the id (a logging filter) is a
deliberate follow-up; the contextvar is in place so it's a small addition.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class SqlStats:
    """Statements run and time spent in the database during one request
    (review 2026-10-01, "SQL timing").

    One mutable object per request, held in a contextvar.  The object — not
    the variable — is what the cursor hooks change: a sync handler runs in a
    worker thread with a COPY of the context, so a value set there would never
    reach the middleware, while a mutation of the shared object does.
    """

    __slots__ = ("count", "ms")

    def __init__(self) -> None:
        self.count = 0
        self.ms = 0.0


# None outside a request: the ingestion and report workers, startup tasks and
# scripts pay one contextvar read per statement and record nothing.
sql_stats_var: ContextVar["SqlStats | None"] = ContextVar("sql_stats", default=None)


def get_sql_stats() -> "SqlStats | None":
    """This request's SQL counters, or ``None`` outside a request."""
    return sql_stats_var.get()


def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if sql_stats_var.get() is None or context is None:
        return
    context._bluestick_sql_t0 = time.perf_counter()


def _record_statement(context) -> None:
    stats = sql_stats_var.get()
    if stats is None or context is None:
        return
    started = getattr(context, "_bluestick_sql_t0", None)
    if started is None:
        return
    # Cleared so a statement that fails is not counted a second time by the
    # error hook after the success hook already ran.
    context._bluestick_sql_t0 = None
    stats.count += 1
    stats.ms += (time.perf_counter() - started) * 1000


def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    _record_statement(context)


def _handle_error(exception_context):
    # A statement that fails — a cancelled one above all — is exactly the one
    # worth seeing in ``db_ms``, and ``after_cursor_execute`` never fires for it.
    _record_statement(exception_context.execution_context)


def instrument_engine(engine) -> None:
    """Attach the SQL counters to ``engine``.  Idempotent."""
    from sqlalchemy import event

    for name, fn in (
        ("before_cursor_execute", _before_cursor_execute),
        ("after_cursor_execute", _after_cursor_execute),
        ("handle_error", _handle_error),
    ):
        if not event.contains(engine, name, fn):
            event.listen(engine, name, fn)


def _route_template(scope) -> str | None:
    """The matched route's path TEMPLATE (``/api/v1/projects/{project_id}/hosts/``),
    so every project's requests to one endpoint group under one name.  ``None``
    when nothing matched (a 404).

    ``scope["route"].path`` cannot be used: FastAPI keeps an included router
    as one node, so it is ROUTER-relative (``/`` for the hosts list — see the
    note in ``api/deps.py``).  The template is rebuilt from the real path by
    putting each matched parameter's name back where its value sits, in the
    order the routers matched them (outer first), which is the order of the
    path.
    """
    if scope.get("route") is None:
        return None
    path = scope.get("path") or ""
    params = scope.get("path_params") or {}
    if not params:
        return path
    segments = path.split("/")
    cursor = 0
    for name, value in params.items():
        text_value = str(value)
        for index in range(cursor, len(segments)):
            if segments[index] == text_value:
                segments[index] = "{" + name + "}"
                cursor = index + 1
                break
        else:
            # A ``{rest:path}`` parameter spans segments: replace it whole.
            joined = "/".join(segments)
            if text_value and text_value in joined:
                segments = joined.replace(text_value, "{" + name + "}", 1).split("/")
    return "/".join(segments)


_access_logger = logging.getLogger("app.access")

_REQUEST_ID_HEADER = b"x-request-id"
_SERVER_TIMING_HEADER = b"server-timing"
_CACHE_CONTROL_HEADER = b"cache-control"
# Authenticated API responses carry per-project, often-mutating data (host
# lists, review state, findings) AND sensitive content. Nothing set a
# Cache-Control header, so a browser/proxy could serve a stale GET /hosts from
# cache — e.g. a project-default "Not Reviewed" filter showing hosts that were
# just reviewed, until a param change busts the cache. `no-store` forces every
# API read to be fresh and keeps sensitive JSON out of the on-disk cache.
_NO_STORE = b"no-store"

# Requests at/above this land on a greppable WARNING ("SLOW request …") so a
# large-dataset instance's hot endpoints surface without trawling every access
# line.  Overridable via env for tuning against a specific deployment.
try:
    _SLOW_MS = int(os.getenv("SLOW_REQUEST_MS", "1000"))
except ValueError:
    _SLOW_MS = 1000


def get_request_id() -> str:
    """Current request's correlation id, or ``"-"`` outside a request."""
    return request_id_var.get()


def _incoming_request_id(scope) -> str | None:
    for name, value in scope.get("headers", ()):
        if name == _REQUEST_ID_HEADER and value:
            # Bound the length so a hostile inbound header can't bloat logs.
            return value.decode("latin-1", "replace")[:64]
    return None


class RequestContextMiddleware:
    """ASGI middleware: set the request-id contextvar, time the request, inject
    the ``X-Request-ID`` response header, and emit one access-log line."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = _incoming_request_id(scope) or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        sql_stats = SqlStats()
        sql_token = sql_stats_var.set(sql_stats)
        start = time.perf_counter()
        status_code = 0
        rid_bytes = rid.encode("latin-1", "replace")
        # Only the data API gets no-store; static assets are served by nginx,
        # not here.
        is_api = scope.get("path", "").startswith("/api/")

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((_REQUEST_ID_HEADER, rid_bytes))
                # Prevent stale/sensitive API responses being served from the
                # browser/proxy cache — unless a handler deliberately set its
                # own Cache-Control (none do today, but don't clobber it).
                if is_api and not any(k == _CACHE_CONTROL_HEADER for k, _ in headers):
                    headers.append((_CACHE_CONTROL_HEADER, _NO_STORE))
                # Server-Timing surfaces backend time in the browser's Network
                # tab, so clicking around a large instance shows which endpoints
                # are slow without reading logs.  Measured to first-byte: for the
                # JSON aggregate endpoints (posture/insights/dashboard/workbench)
                # the handler has fully run by here, so this ≈ total handler time;
                # for streaming responses it's time-to-first-byte (expected).
                elapsed_ms = (time.perf_counter() - start) * 1000
                # ``db`` is the time spent inside database statements up to
                # the same point (review 2026-10-01): app − db is Python.
                headers.append(
                    (
                        _SERVER_TIMING_HEADER,
                        f"app;dur={elapsed_ms:.1f}, db;dur={sql_stats.ms:.1f}".encode("latin-1"),
                    ),
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            method = scope.get("method", "?")
            path = scope.get("path", "?")
            # New fields are APPENDED so anything reading the line by position
            # keeps working.  ``route`` is the template, "-" when none matched.
            route = _route_template(scope) or "-"
            db_ms = int(sql_stats.ms)
            _access_logger.info(
                "%s %s -> %s %dms req=%s db_ms=%d db_n=%d route=%s",
                method, path, status_code or "-", duration_ms, rid,
                db_ms, sql_stats.count, route,
            )
            # Greppable hot-endpoint marker for large-dataset profiling.
            if duration_ms >= _SLOW_MS:
                _access_logger.warning(
                    "SLOW request %s %s %dms -> %s req=%s db_ms=%d db_n=%d route=%s",
                    method, path, duration_ms, status_code or "-", rid,
                    db_ms, sql_stats.count, route,
                )
            sql_stats_var.reset(sql_token)
            request_id_var.reset(token)
