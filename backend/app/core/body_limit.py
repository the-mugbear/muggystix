"""A cap on request bodies, applied before the route runs (review 2026-10-07).

FastAPI reads a request's body before it resolves the route's dependencies,
so before authentication.  nginx allows 2 GB on ``/api/`` because a scan
upload needs it; without a second limit an anonymous client could make the
backend hold a 2 GB JSON body in memory on any route, answered with a 401
only after it was read.

Every route gets ``MAX_REQUEST_BODY_BYTES``.  Only the two scan-upload routes
(multipart, spooled to disk and limited by ``MAX_FILE_SIZE`` in the handler)
are exempt.

Pure ASGI, like ``RequestContextMiddleware``: nothing is buffered here.  A
declared ``Content-Length`` over the limit is refused before a byte is read;
a body without one (chunked) is counted as it arrives.
"""
from __future__ import annotations

import json
import re

# Scan uploads: the page's and the agents'.  Everything else that takes a file
# (note attachments 10 MB, template files, a subnet list 2 MB) fits the
# ordinary limit.
_LARGE_BODY_PATHS = re.compile(
    r"^/api/v1/(?:projects/\d+/upload|agent/uploads)/?$"
)
_AGENT_UPLOAD_PATH = re.compile(r"^/api/v1/agent/uploads/?$")
_BODYLESS_METHODS = {"GET", "HEAD", "OPTIONS"}


def allows_large_body(path: str) -> bool:
    return bool(_LARGE_BODY_PATHS.match(path))


def is_agent_upload(path: str) -> bool:
    """The agents' scan upload (the other large-body path is the page's)."""
    return bool(_AGENT_UPLOAD_PATH.match(path))


class RequestBodyLimitMiddleware:
    """``large_body_refusal(path, headers) -> (status, detail) | None`` is
    asked, in a worker thread, before a large-body path's body is read: a
    refusal is answered as it says and the body is never read (review
    2026-10-10, S3 — an anonymous client could make the backend spool 2 GB
    before the route's 401; review 2026-10-23b, PLT-2 — so could a viewer).  The
    application passes ``deps.large_upload_refusal``; without one the paths
    are only exempt from the cap."""

    def __init__(self, app, max_bytes: int, large_body_refusal=None):
        self.app = app
        self.max_bytes = max_bytes
        self.large_body_refusal = large_body_refusal

    async def _answer(self, send, status: int, detail) -> None:
        body = json.dumps({"detail": detail}).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def _refuse(self, send) -> None:
        await self._answer(send, 413, f"Request body is larger than the limit of {self.max_bytes} bytes.")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in _BODYLESS_METHODS:
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if allows_large_body(path):
            if self.large_body_refusal is not None:
                from starlette.concurrency import run_in_threadpool

                headers = {
                    k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in scope.get("headers") or []
                }
                # In a thread: the check reads the caller's session or key and
                # role (a short session of its own, closed before anything is
                # awaited).
                refusal = await run_in_threadpool(self.large_body_refusal, path, headers)
                if refusal is not None:
                    status, detail = refusal
                    await self._answer(send, status, detail)
                    return
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._refuse(send)
                    return
            except ValueError:
                pass  # the server rejects a malformed header itself

        received = 0
        refused = False
        started = False

        async def counted_receive():
            nonlocal received, refused
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > self.max_bytes:
                    refused = True
                    if not started:
                        await self._refuse(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            nonlocal started
            if refused:
                return  # the 413 is the answer; nothing the route says follows it
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except Exception:
            # The route saw a disconnect in the middle of its body and raised;
            # the client already has its 413.
            if not refused:
                raise
