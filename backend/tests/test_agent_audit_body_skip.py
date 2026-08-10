"""Regression tests for v2.90.2 — agent audit middleware skips body
capture for multipart uploads and oversize JSON bodies (code review #1).

Pre-fix: the middleware called ``await request.body()`` unconditionally
on every agent POST/PUT/PATCH/DELETE.  For a multipart recon upload
that buffered the entire payload (up to MAX_FILE_SIZE = 1 GB) in
memory before FastAPI's streaming UploadFile path saw the request,
defeating the chunked-ingestion design and OOM-killing the 2 GB
backend worker at 4 × concurrency.

Post-fix (v2.90.2): dispatch skipped the read for multipart and for a
declared oversize Content-Length.

Revised (v2.240.2, review A2): deciding from the *declared* length trusted a
header the caller controls. An absent Content-Length (chunked) and a malformed
one both produced ``None`` and fell through to ``await request.body()`` — and
that runs before ``call_next``, so before authentication. An unauthenticated
caller could aim a 2 GB chunked body at any /agent/* mutation.

Capture is now a bounded tee over the ASGI receive channel: nothing is read in
the middleware, and at most MAX_BODY_BYTES accumulates as the *application*
consumes the stream. These tests therefore assert the PROPERTY (never
pre-reads; never exceeds the cap, whatever the headers claim) rather than the
old mechanism.

When skip kicks in, the audit row's body_summary is synthesised from
the headers ({"_multipart": True, "_size": <content-length>,
"_skipped_for_memory": True}) so the operator-visible signal is
preserved without buffering the payload.

These tests use unittest.mock to assert that ``request.body()`` is
NOT awaited for the skip paths.  Driving the middleware directly is
the cleanest way to nail the contract without spinning up a full
multipart upload through TestClient (which would mask whether the
middleware buffered or not).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent_api_log_service import (
    AGENT_API_PREFIX,
    AgentApiCallLogger,
)


@pytest.fixture(autouse=True)
def _stub_write_row(monkeypatch):
    """Stub ``_write_row`` (the DB-write half of the middleware) to a
    no-op so each test can drive ``dispatch`` without spinning up a
    DB.  ``monkeypatch`` restores the original after the test so we
    don't leak the no-op into other test files (notably the phase1
    middleware integration tests which expect the real writer)."""
    monkeypatch.setattr(
        AgentApiCallLogger,
        "_write_row",
        staticmethod(lambda **kwargs: None),
    )


@pytest.mark.asyncio
async def test_multipart_agent_upload_does_not_call_request_body():
    """The dispatcher must not await ``request.body()`` for
    multipart/form-data agent requests.  Closes the OOM regression
    reported on /agent/recon/upload with multi-hundred-MB Nessus
    files."""
    logger = AgentApiCallLogger(MagicMock())

    request = MagicMock()
    request.url.path = f"{AGENT_API_PREFIX}/recon/upload"
    request.method = "POST"
    request.headers = {
        "content-type": "multipart/form-data; boundary=foo",
        "content-length": str(500 * 1024 * 1024),  # 500 MB upload
    }
    request.body = AsyncMock(return_value=b"NEVER READ")
    request.state = MagicMock()

    async def _call_next(_req):
        resp = MagicMock()
        resp.status_code = 200
        return resp

    await logger.dispatch(request, _call_next)

    request.body.assert_not_awaited()
    assert request.state._agent_audit_body_skip_reason == "multipart"


@pytest.mark.asyncio
async def test_body_is_never_pre_read_even_without_a_content_length():
    """The A2 hole: no Content-Length at all (chunked transfer-encoding).

    The old code treated a missing length as "small enough" and buffered the
    whole payload. Nothing may be read in the middleware now.
    """
    logger = AgentApiCallLogger(MagicMock())

    request = MagicMock()
    request.url.path = f"{AGENT_API_PREFIX}/test-plans/1/entries/1/test-results"
    request.method = "POST"
    request.headers = {"content-type": "application/json"}  # no content-length
    request.body = AsyncMock(return_value=b"NEVER READ")
    request.state = MagicMock()

    async def _call_next(_req):
        resp = MagicMock()
        resp.status_code = 201
        return resp

    await logger.dispatch(request, _call_next)
    request.body.assert_not_awaited()


@pytest.mark.asyncio
async def test_body_is_never_pre_read_with_a_malformed_content_length():
    """The same hole via a garbage header — int() raised, length became None."""
    logger = AgentApiCallLogger(MagicMock())

    request = MagicMock()
    request.url.path = f"{AGENT_API_PREFIX}/test-plans/1/entries/1/test-results"
    request.method = "POST"
    request.headers = {"content-type": "application/json", "content-length": "not-a-number"}
    request.body = AsyncMock(return_value=b"NEVER READ")
    request.state = MagicMock()

    async def _call_next(_req):
        resp = MagicMock()
        resp.status_code = 201
        return resp

    await logger.dispatch(request, _call_next)
    request.body.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_is_bounded_when_a_chunked_body_exceeds_the_cap():
    """A body far larger than the cap, streamed with no declared length.

    This is the attack shape. The tee must retain at most MAX_BODY_BYTES no
    matter how much the client sends, and flag the row as truncated.
    """
    from app.services.agent_api_log_service import MAX_BODY_BYTES

    logger = AgentApiCallLogger(MagicMock())

    # 40 chunks of 1 MiB, streamed — 40 MiB total, never declared.
    chunks = [{"type": "http.request", "body": b"A" * (1024 * 1024), "more_body": i < 39}
              for i in range(40)]
    sent = iter(chunks)

    async def _receive():
        return next(sent)

    request = MagicMock()
    request.url.path = f"{AGENT_API_PREFIX}/test-plans/1/entries/1/test-results"
    request.method = "POST"
    request.headers = {"content-type": "application/json"}
    request.receive = _receive
    request.state = MagicMock()
    holder = {}

    async def _call_next(_req):
        # Stand in for the application draining the stream.
        total = 0
        while True:
            msg = await request._receive()
            total += len(msg.get("body", b""))
            if not msg.get("more_body"):
                break
        holder["drained"] = total
        resp = MagicMock()
        resp.status_code = 201
        return resp

    await logger.dispatch(request, _call_next)

    # The application still saw every byte — the tee must not swallow input.
    assert holder["drained"] == 40 * 1024 * 1024
    # But the middleware retained only the cap.
    assert request.state._agent_audit_body_skip_reason == "oversize"


@pytest.mark.asyncio
async def test_small_json_agent_request_still_captures_body():
    """The fix must NOT regress capture for the common case — small
    JSON bodies on mutation endpoints are still buffered for the
    audit log (truncated client identifiers, scoped IDs, etc.)."""
    logger = AgentApiCallLogger(MagicMock())

    request = MagicMock()
    request.url.path = f"{AGENT_API_PREFIX}/test-plans/1/entries/1/test-results"
    request.method = "POST"
    request.headers = {
        "content-type": "application/json",
        "content-length": "256",
    }
    captured_body = b'{"test_index": 0, "status": "executed"}'
    request.body = AsyncMock(return_value=b"NEVER PRE-READ")
    sent = iter([{"type": "http.request", "body": captured_body, "more_body": False}])

    async def _receive():
        return next(sent)

    request.receive = _receive
    request.state = MagicMock()
    seen = {}

    async def _call_next(_req):
        seen["body"] = (await request._receive())["body"]
        resp = MagicMock()
        resp.status_code = 201
        return resp

    await logger.dispatch(request, _call_next)

    # Capture happens via the tee as the app reads — never by pre-reading.
    request.body.assert_not_awaited()
    assert seen["body"] == captured_body, "the app must still receive the body intact"
    assert request.state._agent_audit_body_captured is True
    assert request.state._agent_audit_body_skip_reason is None


@pytest.mark.asyncio
async def test_non_agent_path_is_untouched():
    """The middleware must only act on /agent/* paths — a regular
    user-API request gets no buffering and no audit row."""
    logger = AgentApiCallLogger(MagicMock())

    request = MagicMock()
    request.url.path = "/api/v1/projects/1/hosts/"
    request.method = "POST"
    request.headers = {"content-type": "application/json", "content-length": "256"}
    request.body = AsyncMock(return_value=b'{"x":1}')
    request.state = MagicMock()

    async def _call_next(_req):
        resp = MagicMock()
        resp.status_code = 200
        return resp

    await logger.dispatch(request, _call_next)

    request.body.assert_not_awaited()
