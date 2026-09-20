"""Phase 3 (alerting) backend tests: outbound webhooks."""
from app.db.models_project import WebhookConfig
from app.services import webhook_dispatcher as wd


# ---------------------------------------------------------------------------
# Webhook URL validation + dispatch routing (no real HTTP)
# ---------------------------------------------------------------------------

def test_is_valid_webhook_url():
    assert wd.is_valid_webhook_url("https://hooks.slack.com/services/abc")
    assert wd.is_valid_webhook_url("http://10.0.0.5:8080/hook")  # internal allowed (trusted admin)
    assert not wd.is_valid_webhook_url("ftp://example.com/x")
    assert not wd.is_valid_webhook_url("not-a-url")
    assert not wd.is_valid_webhook_url("")


def test_dispatch_routes_only_to_subscribed(db_session, test_project, monkeypatch):
    # v2.91.2 — the dispatcher now uses a bounded queue.Queue instead
    # of ThreadPoolExecutor.submit().  Capture put_nowait calls
    # (which is what dispatch() now invokes) so we can assert
    # routing without actually firing HTTP.
    captured = []
    monkeypatch.setattr(wd._QUEUE, "put_nowait", lambda item: captured.append(item))

    db_session.add_all([
        WebhookConfig(project_id=test_project.id, name="all", url="https://x/1", events=[], is_active=True),
        WebhookConfig(project_id=test_project.id, name="assign-only", url="https://x/2", events=["host_assigned"], is_active=True),
        WebhookConfig(project_id=test_project.id, name="disabled", url="https://x/3", events=[], is_active=False),
    ])
    db_session.commit()

    dispatcher = wd.WebhookDispatcher(db_session)

    # host_assigned → "all" (empty=all) + "assign-only"; "disabled" excluded.
    assert dispatcher.stage(project_id=test_project.id, event="host_assigned", title="t", body="b").queued == 2
    # note_mention → only "all" (assign-only doesn't subscribe).
    captured.clear()
    assert dispatcher.stage(project_id=test_project.id, event="note_mention", title="t").queued == 1


def test_build_payload_is_slack_compatible():
    p = wd.build_payload("host_assigned", "Title", "Body", 5, {"host_id": 9})
    assert p["text"].startswith("*Title*")  # Slack incoming-webhook reads `text`
    assert p["event"] == "host_assigned"
    assert p["project_id"] == 5
    assert p["context"] == {"host_id": 9}


# ---------------------------------------------------------------------------
# Webhook CRUD (admin client)
# ---------------------------------------------------------------------------

def test_webhook_crud_and_validation(client, test_project):
    base = f"/api/v1/projects/{test_project.id}/webhooks"

    r = client.post(base, json={
        "name": "slack", "url": "https://hooks.slack.com/services/x",
        "secret": "s3cr3t", "events": ["host_assigned"],
    })
    assert r.status_code == 201, r.text
    body = r.json()
    wid = body["id"]
    assert body["has_secret"] is True
    assert "secret" not in body  # secret never returned

    assert any(w["id"] == wid for w in client.get(base).json())

    # unknown event → 422
    assert client.post(base, json={"name": "x", "url": "https://x/y", "events": ["nope"]}).status_code == 422
    # non-http scheme → 422
    assert client.post(base, json={"name": "x", "url": "ftp://x/y"}).status_code == 422

    # clear secret + disable
    rp = client.patch(f"{base}/{wid}", json={"secret": "", "is_active": False})
    assert rp.status_code == 200
    assert rp.json()["has_secret"] is False and rp.json()["is_active"] is False

    assert client.delete(f"{base}/{wid}").status_code == 204
    assert all(w["id"] != wid for w in client.get(base).json())


def test_webhook_event_types(client, test_project):
    r = client.get(f"/api/v1/projects/{test_project.id}/webhooks/event-types")
    assert r.status_code == 200
    keys = {e["key"] for e in r.json()}
    assert "host_assigned" in keys


# ---------------------------------------------------------------------------
# Scan staleness — REMOVED (v2.374.2)
# ---------------------------------------------------------------------------

def test_the_staleness_endpoint_is_gone(client, test_project):
    """`GET /dashboard/staleness` ("what needs re-scanning?") fed the Operations
    "Scan freshness" card.  A project is a snapshot of one assessment window,
    so the age of an observation inside it is not a defect to chase; both were
    removed.  Pinned so the endpoint is not reintroduced by habit."""
    r = client.get(f"/api/v1/projects/{test_project.id}/dashboard/staleness")
    assert r.status_code == 404
