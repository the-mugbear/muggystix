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
import time
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_access_logger = logging.getLogger("app.access")

_REQUEST_ID_HEADER = b"x-request-id"


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
        start = time.perf_counter()
        status_code = 0
        rid_bytes = rid.encode("latin-1", "replace")

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((_REQUEST_ID_HEADER, rid_bytes))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            _access_logger.info(
                "%s %s -> %s %dms req=%s",
                scope.get("method", "?"),
                scope.get("path", "?"),
                status_code or "-",
                duration_ms,
                rid,
            )
            request_id_var.reset(token)
