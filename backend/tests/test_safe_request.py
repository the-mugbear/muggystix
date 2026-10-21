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


def test_every_outbound_call_has_a_wall_clock_budget():
    """Review 2026-10-21, A1: ``timeout`` bounds each phase, not the request —
    a server that trickles bytes inside every read timeout held an LLM draft
    or a connection test (API request threads) for as long as it liked; only
    the webhook sweep passed ``total_timeout``.  Every ``safe_request`` call in
    the application now names one, and a new call without one fails here."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    missing = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "url_validator.py":
            continue  # the helper itself
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "safe_request":
                if not any(k.arg == "total_timeout" for k in node.keywords):
                    missing.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not missing, f"safe_request without total_timeout: {missing}"


def test_a_trickling_provider_is_cut_off_at_the_callers_budget(monkeypatch):
    """At the caller, not only in the helper: a chat completion whose server
    keeps sending inside each read timeout ends at the adapter's budget with
    the one "did not answer" text."""
    import time
    from types import SimpleNamespace

    from app.services import llm_provider_service as svc
    from app.services import url_validator

    def trickle(*_args, **_kwargs):
        time.sleep(2)  # longer than the budget below; each "read" on time
        raise AssertionError("the budget should have ended the call first")

    monkeypatch.setattr(url_validator, "_safe_request", trickle)
    adapter = svc._ADAPTERS["ollama"]
    monkeypatch.setitem(svc._ADAPTERS, "ollama", svc.LLMAdapter(
        build_request=lambda **_k: ("http://127.0.0.1:9/api/chat", {}, {}),
        parse_response=adapter.parse_response, timeout_seconds=0.1, allow_private=True,
    ))
    provider = SimpleNamespace(id=1, provider_type="ollama", api_key_encrypted=None, base_url="http://127.0.0.1:9",
                               model_id="m", is_active=True)
    started = time.monotonic()
    with pytest.raises(RuntimeError) as exc:
        svc.chat_completion(provider, system=None, messages=[{"role": "user", "content": "x"}])
    assert time.monotonic() - started < 1.5
    assert str(exc.value) == svc.PROVIDER_UNREACHABLE
