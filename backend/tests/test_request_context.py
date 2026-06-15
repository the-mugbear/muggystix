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
    res = WebhookDispatcher(db_session).dispatch(
        project_id=test_project.id, event="note_mention", title="t",
    )
    assert isinstance(res, DispatchResult)
    assert res == DispatchResult(queued=0, dropped=0)
