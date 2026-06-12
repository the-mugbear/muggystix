"""Response-size cap on outbound HTTP (audit A4).

safe_request is the shared SSRF-safe egress helper used by LLM providers,
scanner integrations, and webhooks. It streams the body and aborts past a byte
cap so a hostile/broken endpoint can't balloon backend RAM via .json()/.text.
We swap safe_http_client for a MockTransport client to drive the cap directly.
"""
import httpx
import pytest

from app.services import url_validator
from app.services.url_validator import safe_request, ResponseTooLarge


def _mock_factory(handler):
    def factory(**kwargs):  # mirrors safe_http_client(allow_private=…, timeout=…)
        return httpx.Client(transport=httpx.MockTransport(handler))
    return factory


def test_safe_request_returns_body_under_cap(monkeypatch):
    monkeypatch.setattr(
        url_validator, "safe_http_client",
        _mock_factory(lambda req: httpx.Response(200, json={"ok": True})),
    )
    r = safe_request("GET", "https://example.test/x", max_bytes=1024)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_safe_request_raises_when_body_exceeds_cap(monkeypatch):
    monkeypatch.setattr(
        url_validator, "safe_http_client",
        _mock_factory(lambda req: httpx.Response(200, content=b"A" * 5000)),
    )
    with pytest.raises(ResponseTooLarge):
        safe_request("GET", "https://example.test/x", max_bytes=1024)


def test_safe_request_exactly_at_cap_is_allowed(monkeypatch):
    monkeypatch.setattr(
        url_validator, "safe_http_client",
        _mock_factory(lambda req: httpx.Response(200, content=b"A" * 1024)),
    )
    r = safe_request("GET", "https://example.test/x", max_bytes=1024)
    assert r.content == b"A" * 1024
