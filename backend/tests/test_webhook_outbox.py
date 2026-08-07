"""
Webhook delivery survives receiver failure and process restart (v2.233.0).

Delivery used to be fire-and-forget on a process-local queue: a receiver
returning 500 during its own deploy, or a BlueStick restart with items still
in flight, silently lost the event with nothing but a log line. These tests
pin the property that makes the outbox worth having — a failed attempt is
still *pending work*, not a discarded one.
"""

import queue
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


def _expire_lease(db):
    """Rows handed to the fast path are leased into the future so the sweeper
    can't claim them mid-POST. Tests that exercise the sweeper have to fast-
    forward past that lease, which is what a real 60s tick does."""
    from app.db.models_project import WebhookDelivery

    for row in db.query(WebhookDelivery).all():
        if row.next_attempt_at is not None:
            row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()


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
    _expire_lease(db_session)
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
    _expire_lease(db_session)
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
    _expire_lease(db_session)
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


# ---------------------------------------------------------------------------
# Fast path vs sweeper
# ---------------------------------------------------------------------------
# The suite above disables the fast path so the sweeper can be driven
# deterministically. That left the two delivery paths never exercised
# together — which is exactly where the duplicate-POST bug lived. These
# re-enable enough of the fast path to test the interaction.


def test_a_freshly_dispatched_row_is_not_immediately_sweepable(
    db_session, test_project, webhook
):
    """The core guard. The fast path is about to POST this row; if the sweeper
    can claim it in that window the receiver gets the event twice, and since
    payloads carry no idempotency key it cannot dedupe."""
    _dispatch(db_session, test_project.id)

    attempted = wd.sweep_pending_deliveries(db_session)
    assert attempted == 0, (
        "the sweeper claimed a row the fast path is still delivering — "
        "that is a duplicate POST"
    )

    row = db_session.query(WebhookDelivery).one()
    assert row.next_attempt_at > datetime.now(timezone.utc), "row must be leased"


def test_queue_full_hands_the_row_straight_back_to_the_sweeper(
    db_session, test_project, webhook, monkeypatch
):
    """When the fast path refuses the work it must drop the lease, or the
    event sits idle for the full lease before anyone retries it."""
    def _full(_item):
        raise queue.Full()

    monkeypatch.setattr(wd._QUEUE, "put_nowait", _full)

    result = _dispatch(db_session, test_project.id)
    assert result.dropped == 1

    row = db_session.query(WebhookDelivery).one()
    db_session.refresh(row)
    assert row.next_attempt_at <= datetime.now(timezone.utc), (
        "a row the fast path refused must be due immediately"
    )
    assert wd.sweep_pending_deliveries(db_session) == 1


def test_a_queued_task_does_not_send_if_the_row_already_settled(
    db_session, test_project, webhook, monkeypatch
):
    """A task can sit in the bounded queue behind a slow receiver long enough
    for the sweeper to deliver it. Without a pre-send status re-read the
    queued task POSTs anyway — a guaranteed duplicate on a deep queue, not a
    narrow race."""
    _dispatch(db_session, test_project.id)
    row = db_session.query(WebhookDelivery).one()

    # Something else (the sweeper) delivered it while the task sat in the queue.
    row.status = WebhookDeliveryStatus.DELIVERED.value
    row.next_attempt_at = None
    db_session.commit()

    sent = []
    monkeypatch.setattr(
        wd, "_attempt_delivery",
        lambda *a, **kw: (sent.append(a) or (True, 200, None)),
    )
    wd._deliver(webhook.url, None, {"x": 1}, row.id)

    assert sent == [], "a settled row must not be POSTed again by a queued task"


# ---------------------------------------------------------------------------
# Delivery history surface
# ---------------------------------------------------------------------------
# The outbox retains FAILED rows so "did that alert ever go out?" is
# answerable. It only actually is if something exposes them.


def test_delivery_history_is_reachable_over_the_api(
    client, db_session, test_project, webhook
):
    _dispatch(db_session, test_project.id)
    _expire_lease(db_session)
    wd.sweep_pending_deliveries(db_session)  # one failed attempt

    resp = client.get(f"/api/v1/projects/{test_project.id}/webhooks/deliveries")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["event"] == "host_assigned"
    assert rows[0]["attempts"] == 1
    assert rows[0]["last_error"]
    assert rows[0]["webhook_name"] == "outbox-fixture"


def test_a_settled_failure_can_be_requeued(
    client, db_session, test_project, webhook
):
    """After fixing the receiver an operator must be able to push the event
    back through without editing the database by hand."""
    _dispatch(db_session, test_project.id)
    row = db_session.query(WebhookDelivery).one()
    row.status = WebhookDeliveryStatus.FAILED.value
    row.attempts = 6
    row.last_error = "receiver unreachable"
    row.next_attempt_at = None
    db_session.commit()

    resp = client.post(
        f"/api/v1/projects/{test_project.id}/webhooks/deliveries/{row.id}/retry",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    # Reset, not resumed: a human decision shouldn't inherit an exhausted
    # backoff and give up on the first transient error.
    assert body["attempts"] == 0
    assert body["last_error"] is None

    db_session.refresh(row)
    assert wd.sweep_pending_deliveries(db_session) == 1


def test_retrying_an_already_queued_delivery_is_rejected(
    client, db_session, test_project, webhook
):
    _dispatch(db_session, test_project.id)
    row = db_session.query(WebhookDelivery).one()

    resp = client.post(
        f"/api/v1/projects/{test_project.id}/webhooks/deliveries/{row.id}/retry",
    )
    assert resp.status_code == 409, resp.text
