"""
Webhook delivery survives receiver failure and process restart (v2.233.0).

Delivery used to be fire-and-forget on a process-local queue: a receiver
returning 500 during its own deploy, or a BlueStick restart with items still
in flight, silently lost the event with nothing but a log line. These tests
pin the property that makes the outbox worth having — a failed attempt is
still *pending work*, not a discarded one.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models_project import (
    WebhookConfig,
    WebhookDelivery,
    WebhookDeliveryStatus,
)
from app.services import webhook_dispatcher as wd


@pytest.fixture
def webhook(db_session, test_project, test_user):
    cfg = WebhookConfig(
        project_id=test_project.id,
        name="outbox-fixture",
        url="https://receiver.invalid/hook",
        events=[],  # all events
        is_active=True,
        created_by_id=test_user.id,
    )
    db_session.add(cfg)
    db_session.commit()
    db_session.refresh(cfg)
    return cfg


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    """Never leave the test process. Individual tests override the outcome."""
    monkeypatch.setattr(
        wd, "_attempt_delivery", lambda *a, **kw: (False, None, "receiver unreachable")
    )
    # Keep the fast path from spawning threads that would race the assertions;
    # every test drives delivery through the sweeper explicitly.
    monkeypatch.setattr(wd, "_ensure_workers", lambda: None)
    monkeypatch.setattr(wd._QUEUE, "put_nowait", lambda item: None)


def _dispatch(db, project_id):
    return wd.WebhookDispatcher(db).dispatch(
        project_id=project_id, event="host_assigned", title="10.0.0.1 assigned to you",
    )


def test_dispatch_persists_the_event_before_attempting_it(
    db_session, test_project, webhook
):
    """The whole point: the event exists in the database independently of
    whether any delivery attempt succeeds."""
    _dispatch(db_session, test_project.id)

    row = db_session.query(WebhookDelivery).one()
    assert row.status == WebhookDeliveryStatus.PENDING.value
    assert row.webhook_config_id == webhook.id
    assert row.event == "host_assigned"
    # The body is stored, not re-derived later from state that may have moved on.
    assert "10.0.0.1 assigned to you" in str(row.payload)


def test_failed_attempt_stays_pending_and_backs_off(
    db_session, test_project, webhook
):
    _dispatch(db_session, test_project.id)
    attempted = wd.sweep_pending_deliveries(db_session)
    assert attempted == 1

    row = db_session.query(WebhookDelivery).one()
    db_session.refresh(row)
    assert row.status == WebhookDeliveryStatus.PENDING.value, (
        "a failed delivery must remain retryable, not be discarded"
    )
    assert row.attempts == 1
    assert row.last_error
    assert row.next_attempt_at > datetime.now(timezone.utc), "retry must be deferred"


def test_retry_eventually_succeeds_and_settles(
    db_session, test_project, webhook, monkeypatch
):
    """A receiver that recovers gets the event it would previously have lost."""
    _dispatch(db_session, test_project.id)
    wd.sweep_pending_deliveries(db_session)  # fails

    row = db_session.query(WebhookDelivery).one()
    row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    monkeypatch.setattr(wd, "_attempt_delivery", lambda *a, **kw: (True, 200, None))
    wd.sweep_pending_deliveries(db_session)

    db_session.refresh(row)
    assert row.status == WebhookDeliveryStatus.DELIVERED.value
    assert row.response_status == 200
    assert row.delivered_at is not None
    assert row.next_attempt_at is None


def test_gives_up_after_max_attempts_but_keeps_the_record(
    db_session, test_project, webhook
):
    """A permanently dead receiver must stop being hammered — and the failure
    must remain visible rather than disappearing into a log."""
    _dispatch(db_session, test_project.id)
    row = db_session.query(WebhookDelivery).one()
    row.max_attempts = 2
    db_session.commit()

    for _ in range(2):
        row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
        wd.sweep_pending_deliveries(db_session)

    db_session.refresh(row)
    assert row.status == WebhookDeliveryStatus.FAILED.value
    assert row.attempts == 2
    assert row.next_attempt_at is None
    assert row.last_error


def test_disabled_config_settles_instead_of_retrying_forever(
    db_session, test_project, webhook
):
    _dispatch(db_session, test_project.id)
    webhook.is_active = False
    db_session.commit()

    wd.sweep_pending_deliveries(db_session)

    row = db_session.query(WebhookDelivery).one()
    db_session.refresh(row)
    assert row.status == WebhookDeliveryStatus.FAILED.value
    assert "disabled" in (row.last_error or "")


def test_sweeper_ignores_rows_that_are_not_due(db_session, test_project, webhook):
    """Backoff must actually be honoured, or a dead receiver gets hammered."""
    _dispatch(db_session, test_project.id)
    row = db_session.query(WebhookDelivery).one()
    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    db_session.commit()

    assert wd.sweep_pending_deliveries(db_session) == 0


def test_prune_keeps_failures_longer_than_successes(
    db_session, test_project, webhook
):
    old = datetime.now(timezone.utc) - timedelta(days=10)
    delivered = WebhookDelivery(
        webhook_config_id=webhook.id, project_id=test_project.id,
        event="host_assigned", payload={}, status=WebhookDeliveryStatus.DELIVERED.value,
        created_at=old,
    )
    failed = WebhookDelivery(
        webhook_config_id=webhook.id, project_id=test_project.id,
        event="host_assigned", payload={}, status=WebhookDeliveryStatus.FAILED.value,
        created_at=old,
    )
    db_session.add_all([delivered, failed])
    db_session.commit()

    wd.prune_delivery_history(db_session)

    remaining = db_session.query(WebhookDelivery).all()
    statuses = {r.status for r in remaining}
    assert WebhookDeliveryStatus.DELIVERED.value not in statuses, "10-day-old success pruned"
    assert WebhookDeliveryStatus.FAILED.value in statuses, (
        "failures are what an operator investigates — keep them longer"
    )
