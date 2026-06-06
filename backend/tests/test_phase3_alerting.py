"""Phase 3 (alerting) backend tests: outbound webhooks + scan staleness."""
from datetime import datetime, timedelta, timezone

from app.db import models
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
    assert dispatcher.dispatch(project_id=test_project.id, event="host_assigned", title="t", body="b") == 2
    # note_mention → only "all" (assign-only doesn't subscribe).
    captured.clear()
    assert dispatcher.dispatch(project_id=test_project.id, event="note_mention", title="t") == 1


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
# Scan staleness
# ---------------------------------------------------------------------------

def _scope_with_host(db, project_id, name, cidr, ip, last_seen):
    scope = models.Scope(project_id=project_id, name=name)
    db.add(scope)
    db.flush()
    subnet = models.Subnet(scope_id=scope.id, cidr=cidr)
    db.add(subnet)
    db.flush()
    if ip is not None:
        host = models.Host(project_id=project_id, ip_address=ip, state="up", last_seen=last_seen)
        db.add(host)
        db.flush()
        db.add(models.HostSubnetMapping(host_id=host.id, subnet_id=subnet.id))
        db.flush()
    return scope


def test_staleness_flags_old_and_empty_scopes(client, db_session, test_project):
    now = datetime.now(timezone.utc)
    fresh = _scope_with_host(db_session, test_project.id, "fresh", "10.1.0.0/24", "10.1.0.5", now)
    old = _scope_with_host(db_session, test_project.id, "old", "10.2.0.0/24", "10.2.0.5", now - timedelta(days=40))
    empty = _scope_with_host(db_session, test_project.id, "empty", "10.3.0.0/24", None, None)
    db_session.commit()

    r = client.get(f"/api/v1/projects/{test_project.id}/dashboard/staleness", params={"stale_days": 14})
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {s["scope_id"]: s for s in body["scopes"]}

    assert by_id[fresh.id]["is_stale"] is False
    assert by_id[old.id]["is_stale"] is True
    assert by_id[empty.id]["is_stale"] is True
    assert by_id[empty.id]["last_activity_at"] is None
    # No scans uploaded in this project → project flagged stale.
    assert body["project_is_stale"] is True
    assert body["stale_scope_count"] >= 2
