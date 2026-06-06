"""Regression tests for v2.90.2 — agent audit middleware skips body
capture for multipart uploads and oversize JSON bodies (code review #1).

Pre-fix: the middleware called ``await request.body()`` unconditionally
on every agent POST/PUT/PATCH/DELETE.  For a multipart recon upload
that buffered the entire payload (up to MAX_FILE_SIZE = 1 GB) in
memory before FastAPI's streaming UploadFile path saw the request,
defeating the chunked-ingestion design and OOM-killing the 2 GB
backend worker at 4 × concurrency.

Post-fix: dispatch inspects the content-type and content-length
headers and skips body capture entirely when:
  * content-type starts with multipart/form-data, or
  * content-length exceeds MAX_BODY_BYTES (the JSON capture cap).

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
async def test_oversize_json_agent_request_skips_body_capture():
    """When content-length exceeds the JSON capture cap, the body
    must NOT be read either — same OOM concern, different content
    type."""
    from app.services.agent_api_log_service import MAX_BODY_BYTES

    logger = AgentApiCallLogger(MagicMock())

    request = MagicMock()
    request.url.path = f"{AGENT_API_PREFIX}/test-plans/1/entries/1/test-results"
    request.method = "POST"
    request.headers = {
        "content-type": "application/json",
        "content-length": str(MAX_BODY_BYTES + 1),
    }
    request.body = AsyncMock(return_value=b"NEVER READ")
    request.state = MagicMock()

    async def _call_next(_req):
        resp = MagicMock()
        resp.status_code = 201
        return resp

    await logger.dispatch(request, _call_next)

    request.body.assert_not_awaited()
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
    request.body = AsyncMock(return_value=captured_body)
    request.state = MagicMock()

    async def _call_next(_req):
        resp = MagicMock()
        resp.status_code = 201
        return resp

    await logger.dispatch(request, _call_next)

    request.body.assert_awaited_once()
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
