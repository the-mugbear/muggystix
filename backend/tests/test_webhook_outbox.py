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
    """Stage + commit, which is what a caller now does (v2.302.0).

    ``stage()`` deliberately does not commit — the rows join the caller's
    transaction so the outbox row and the change it announces are atomic. The
    commit here stands in for the caller's own.
    """
    result = wd.WebhookDispatcher(db).stage(
        project_id=project_id, event="host_assigned", title="10.0.0.1 assigned to you",
    )
    db.commit()
    return result


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


def test_only_one_sender_can_claim_a_delivery(db_session, test_project, webhook):
    """The core guard, stated as the invariant rather than as a lease.

    v2.240.3 (review A6) — this used to assert that a freshly dispatched row
    was hidden from the sweeper for 60 seconds. That lease was the old
    duplicate-prevention mechanism, and it did not work: both senders decided
    by *reading* ``status == 'pending'``, so a fast-path task that outlived
    its lease sent the same row the sweeper was sending. Hiding the row also
    delayed recovery whenever the fast path never ran.

    Claiming is atomic now, so the property to pin is simply that a second
    claim on the same row fails while the first holds it.
    """
    _dispatch(db_session, test_project.id)
    row = db_session.query(WebhookDelivery).one()

    first = wd._claim_for_send(row.id)
    second = wd._claim_for_send(row.id)

    assert first is not None, "the first sender must win the row"
    assert second is None, (
        "a second sender claimed a row already in flight — that is the "
        "duplicate POST this fix exists to prevent"
    )

    db_session.expire_all()
    row = db_session.query(WebhookDelivery).one()
    assert row.status == "sending"
    assert row.claim_token == first


def test_a_dead_senders_row_is_reclaimed_once_its_lease_expires(
    db_session, test_project, webhook
):
    """A crash mid-POST must not wedge the row.

    The claim is ownership, not a permanent lock: once the lease passes,
    another sender may take it. Without this a killed worker would strand its
    delivery in ``sending`` forever, invisible to the sweeper.
    """
    _dispatch(db_session, test_project.id)
    row = db_session.query(WebhookDelivery).one()

    token = wd._claim_for_send(row.id)
    assert token is not None

    # Simulate the owner dying: its lease falls into the past.
    db_session.expire_all()
    row = db_session.query(WebhookDelivery).one()
    row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    reclaimed = wd._claim_for_send(row.id)
    assert reclaimed is not None and reclaimed != token, (
        "an expired lease must be reclaimable by another sender"
    )


def test_the_dead_senders_result_cannot_overwrite_the_new_owner(
    db_session, test_project, webhook
):
    """Only the current claim holder may record an outcome."""
    _dispatch(db_session, test_project.id)
    row = db_session.query(WebhookDelivery).one()

    stale_token = wd._claim_for_send(row.id)
    db_session.expire_all()
    row = db_session.query(WebhookDelivery).one()
    row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    wd._claim_for_send(row.id)  # a new owner takes over

    # The stale sender finally comes back and tries to report success.
    wd._record_attempt(row.id, True, 200, None, token=stale_token)

    db_session.expire_all()
    row = db_session.query(WebhookDelivery).one()
    assert row.status != "delivered", (
        "a sender that lost its claim marked the delivery done"
    )


def test_queue_full_hands_the_row_straight_back_to_the_sweeper(
    db_session, test_project, webhook, monkeypatch
):
    """When the fast path refuses the work the row must still be due now, or
    the event sits idle until someone notices.

    v2.302.0 — the queue-full decision moved AFTER the caller's commit (it is
    made in the after-commit hook), so ``stage()`` cannot report it: at staging
    time nobody knows yet whether the fast path will accept the work. The
    accounting moved; the property that matters did not. `dropped` is therefore
    0 here even though the fast path refused.
    """
    def _full(_item):
        raise queue.Full()

    monkeypatch.setattr(wd._QUEUE, "put_nowait", _full)

    result = _dispatch(db_session, test_project.id)
    assert result.queued == 1
    assert result.dropped == 0, "staging cannot know what the fast path will do"

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


# ---------------------------------------------------------------------------
# Transactional staging (v2.302.0)
# ---------------------------------------------------------------------------

def test_a_rolled_back_change_announces_nothing(
    db_session, test_project, webhook, monkeypatch
):
    """The property the outbox was missing.

    Rows used to be written in a commit of their own, necessarily AFTER the
    caller had committed. So a caller that dispatched and then rolled back had
    already announced something that never happened, and a crash between the
    two commits lost the event outright. Staging inside the caller's
    transaction makes the row and the change atomic in both directions.
    """
    sent = []
    monkeypatch.setattr(wd._QUEUE, "put_nowait", lambda item: sent.append(item))

    wd.WebhookDispatcher(db_session).stage(
        project_id=test_project.id, event="host_assigned", title="never happened",
    )
    # Staged, not committed: visible in this transaction only.
    assert db_session.query(WebhookDelivery).count() == 1

    db_session.rollback()

    assert db_session.query(WebhookDelivery).count() == 0, (
        "the outbox row survived a rollback — the webhook would announce a "
        "change that was never persisted"
    )
    assert sent == [], "a rolled-back transaction must not hand work to the fast path"


def test_the_fast_path_fires_only_after_the_caller_commits(
    db_session, test_project, webhook, monkeypatch
):
    """Nothing leaves the process until the caller's work is durable — the
    other half of atomicity. A POST sent before the commit could beat the
    change it describes to a receiver that then reads back stale state."""
    sent = []
    monkeypatch.setattr(wd._QUEUE, "put_nowait", lambda item: sent.append(item))

    wd.WebhookDispatcher(db_session).stage(
        project_id=test_project.id, event="host_assigned", title="real change",
    )
    assert sent == [], "staging must not send"

    db_session.commit()
    assert len(sent) == 1, "the after-commit hook did not hand off the delivery"
    # The queued task carries the committed row's id, so the fast path and the
    # sweeper are talking about the same row.
    row = db_session.query(WebhookDelivery).one()
    assert sent[0][3] == row.id
