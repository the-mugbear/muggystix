"""Regression tests for v2.91.2 — bounded webhook queue + drop
notifications (code review NEW D, Option A).

Pre-fix the dispatcher used an unbounded ThreadPoolExecutor queue; a
slow webhook during a notification burst could pile up arbitrarily
many pending tasks (each carrying a JSON payload + URL + secret),
inflating backend RAM and delivering stale notifications.

Post-fix:
  * Queue is capped at _QUEUE_MAX (256).
  * Submissions past the cap drop the delivery and create a
    user-visible Notification addressed to the webhook's creator.
  * Drop notifications are coalesced per-(config_id, 5-minute
    window) so a sustained outage doesn't flood the bell.

These tests fill the queue artificially (worker loop monkey-patched
so nothing drains it), call dispatch(), and assert that the drop
notification surfaces with the right shape.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models_project import Notification, WebhookConfig
from app.services import webhook_dispatcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_drop_tracker():
    """Wipe the per-process drop tracker between tests so coalescing
    state from a prior test doesn't leak into the next."""
    with webhook_dispatcher._DROP_TRACKER_LOCK:
        webhook_dispatcher._DROP_TRACKER.clear()
    yield
    with webhook_dispatcher._DROP_TRACKER_LOCK:
        webhook_dispatcher._DROP_TRACKER.clear()


@pytest.fixture(autouse=True)
def _drain_queue():
    """Empty the bounded queue before each test.  Production code
    starts daemon workers that drain it asynchronously, but tests
    don't await them — and a half-full queue across tests would
    produce flaky drop counts."""
    while not webhook_dispatcher._QUEUE.empty():
        try:
            webhook_dispatcher._QUEUE.get_nowait()
            webhook_dispatcher._QUEUE.task_done()
        except Exception:
            break
    yield
    while not webhook_dispatcher._QUEUE.empty():
        try:
            webhook_dispatcher._QUEUE.get_nowait()
            webhook_dispatcher._QUEUE.task_done()
        except Exception:
            break


@pytest.fixture
def webhook_creator(db_session, test_project):
    """A User row to own the webhook config — drop notifications target
    this user via WebhookConfig.created_by_id."""
    from app.db.models_auth import User, UserRole
    # Test fixtures hardcode test_user.id=1; offset to avoid collision.
    u = User(
        id=7777,
        username="webhook-owner",
        email="webhook-owner@example.com",
        full_name="Webhook Owner",
        hashed_password="$2b$12$abcdefghijklmnopqrstuv",
        role=UserRole.MEMBER,
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def webhook_cfg(db_session, test_project, webhook_creator):
    cfg = WebhookConfig(
        project_id=test_project.id,
        name="overloaded-receiver",
        url="https://example.invalid/hook",
        events=[],  # empty = all events
        is_active=True,
        created_by_id=webhook_creator.id,
    )
    db_session.add(cfg)
    db_session.commit()
    return cfg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_drop_records_notification_for_creator(db_session, webhook_cfg, webhook_creator):
    """A single drop creates one Notification addressed to the webhook
    creator with the expected shape."""
    webhook_dispatcher._record_dropped_delivery(
        webhook_cfg, "note_mention", "Alice mentioned Bob",
    )
    notifs = (
        db_session.query(Notification)
        .filter(Notification.user_id == webhook_creator.id)
        .all()
    )
    assert len(notifs) == 1
    n = notifs[0]
    assert n.type == "webhook_dropped"
    assert "Webhook delivery dropped" in n.title
    assert "1 event" in n.body
    assert "note_mention" in n.body
    assert "Alice mentioned Bob" in n.body
    assert "not a backend error" in n.body
    assert n.source_type == "webhook"
    assert n.source_id == webhook_cfg.id


def test_drops_within_window_coalesce_to_one_notification(
    db_session, webhook_cfg, webhook_creator,
):
    """Within the 5-minute coalesce window, only the first drop fires a
    notification; subsequent drops increment an internal counter that
    surfaces on the NEXT notification (after the window closes)."""
    for i in range(5):
        webhook_dispatcher._record_dropped_delivery(
            webhook_cfg, "note_mention", f"event-{i}",
        )
    notifs = (
        db_session.query(Notification)
        .filter(Notification.user_id == webhook_creator.id)
        .all()
    )
    # First call fired one notification with count=1; the next 4 were
    # suppressed by coalescing.  The internal counter holds 4 pending.
    assert len(notifs) == 1
    assert "1 event" in notifs[0].body
    with webhook_dispatcher._DROP_TRACKER_LOCK:
        entry = webhook_dispatcher._DROP_TRACKER[webhook_cfg.id]
        assert entry["drops"] == 4  # accumulating for the next window


def test_drop_without_creator_logs_only(db_session, test_project):
    """A webhook config whose creator was deleted (created_by_id=None)
    gets no notification — we don't know who to ping.  The drop is
    still logged."""
    cfg = WebhookConfig(
        project_id=test_project.id,
        name="orphan-webhook",
        url="https://example.invalid/hook",
        events=[],
        is_active=True,
        created_by_id=None,  # orphan
    )
    db_session.add(cfg)
    db_session.commit()
    webhook_dispatcher._record_dropped_delivery(
        cfg, "note_mention", "test",
    )
    count = db_session.query(Notification).count()
    # The test_user fixture may have other Notification rows; assert
    # none target the orphan or carry source_id=cfg.id.
    related = (
        db_session.query(Notification)
        .filter(Notification.source_id == cfg.id, Notification.type == "webhook_dropped")
        .all()
    )
    assert related == []


def test_dispatch_drops_when_queue_full(db_session, webhook_cfg, monkeypatch):
    """End-to-end: when ``_QUEUE.put_nowait`` raises queue.Full, the
    dispatch loop routes through ``_record_dropped_delivery``.  We
    mock put_nowait rather than racing against the real daemon
    workers (which would drain whatever we pre-loaded, leaving the
    queue with capacity at the moment dispatch tries to put)."""
    import queue as _queue
    from app.services.webhook_dispatcher import WebhookDispatcher

    def always_full(_item):
        raise _queue.Full

    monkeypatch.setattr(webhook_dispatcher._QUEUE, "put_nowait", always_full)

    svc = WebhookDispatcher(db_session)
    result = svc.dispatch(
        project_id=webhook_cfg.project_id,
        event="note_mention",
        title="overflow",
        body="queue is wedged",
    )
    # The one target was dropped (queue full): it counts as dropped, NOT queued.
    # (Previously dispatch returned len(targets), reporting the drop as a send.)
    assert result.queued == 0
    assert result.dropped == 1
    notifs = (
        db_session.query(Notification)
        .filter(Notification.source_id == webhook_cfg.id)
        .all()
    )
    assert len(notifs) == 1
    assert notifs[0].type == "webhook_dropped"
    assert "queue was full" in notifs[0].body
