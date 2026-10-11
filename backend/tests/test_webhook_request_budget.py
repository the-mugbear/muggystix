"""One receiver cannot hold the sender: a webhook POST has a wall-clock budget
and so has the sweep (review 2026-10-10, H11).

The outbox sweep runs between jobs on the ingestion worker's ONE thread and
POSTs synchronously.  Its only limit was httpx's per-phase timeout — five
seconds to connect, five per read — which is no bound on a request: a receiver
that sends one byte just inside every read timeout was read for ever, and
twenty queued events for a firewalled address were twenty connect timeouts in
a row.  Meanwhile the worker claimed no import and wrote no heartbeat.

The receivers here are fake TRANSPORTS that never trip a per-phase timeout —
one sleeps before answering, one drips its body — put in where
``safe_http_client`` builds the pinned transport (which these tests do not
exercise and the fix does not touch).  The budgets are shortened so the tests
take a second, and every assertion on elapsed time fails by a wide margin
without the fix (the fakes take four seconds or more).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.db.models_project import WebhookConfig, WebhookDelivery, WebhookDeliveryStatus
from app.services import url_validator
from app.services import webhook_dispatcher as wd


class _Sleeps(httpx.BaseTransport):
    """Answers 200 — after ``seconds`` (a slow connect, or headers dripped a
    byte at a time)."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.requests = 0

    def handle_request(self, request):
        self.requests += 1
        time.sleep(self.seconds)
        return httpx.Response(200, content=b"ok")


class _DripBody(httpx.SyncByteStream):
    def __init__(self, chunks: int, every: float):
        self.chunks, self.every = chunks, every

    def __iter__(self):
        for _ in range(self.chunks):
            time.sleep(self.every)   # each read returns well inside any read timeout
            yield b"x"


class _Drips(httpx.BaseTransport):
    """Answers at once, then sends its body one byte every ``every`` seconds."""

    def __init__(self, chunks: int, every: float):
        self.chunks, self.every = chunks, every

    def handle_request(self, request):
        return httpx.Response(200, stream=_DripBody(self.chunks, self.every))


class _Refuses(httpx.BaseTransport):
    def handle_request(self, request):
        raise httpx.ConnectError("connection refused")


@pytest.fixture
def receiver(monkeypatch):
    """Install a fake transport as the client ``safe_request`` builds."""
    def install(transport):
        monkeypatch.setattr(
            url_validator, "safe_http_client",
            lambda **kw: httpx.Client(transport=transport, timeout=kw["timeout"], follow_redirects=False),
        )
        return transport
    return install


def _timed(fn):
    started = time.monotonic()
    try:
        return fn(), time.monotonic() - started
    except BaseException as exc:  # noqa: BLE001 — the caller asserts on it
        return exc, time.monotonic() - started


# ---------------------------------------------------------------------------
# safe_request(total_timeout=)
# ---------------------------------------------------------------------------

def test_a_receiver_that_does_not_answer_is_left_at_the_budget(receiver):
    receiver(_Sleeps(4.0))
    outcome, elapsed = _timed(lambda: url_validator.safe_request(
        "POST", "https://receiver.invalid/hook", timeout=5.0, total_timeout=0.3, content=b"{}",
    ))
    assert isinstance(outcome, url_validator.RequestBudgetExceeded), outcome
    assert isinstance(outcome, TimeoutError)
    assert "receiver.invalid" in str(outcome) and "0.3s" in str(outcome)
    assert elapsed < 1.5, f"waited {elapsed:.1f}s for a 0.3s budget"


def test_a_receiver_that_drips_its_body_is_left_at_the_budget(receiver):
    receiver(_Drips(chunks=40, every=0.1))      # 4 s in all, no read over 0.1 s
    outcome, elapsed = _timed(lambda: url_validator.safe_request(
        "POST", "https://receiver.invalid/hook", timeout=5.0, total_timeout=0.3, content=b"{}",
    ))
    assert isinstance(outcome, url_validator.RequestBudgetExceeded), outcome
    assert elapsed < 1.5, f"waited {elapsed:.1f}s for a 0.3s budget"


def test_inside_the_budget_the_answer_and_the_errors_are_what_they_were(receiver):
    receiver(_Drips(chunks=3, every=0.01))
    response = url_validator.safe_request(
        "POST", "https://receiver.invalid/hook", timeout=5.0, total_timeout=5.0, content=b"{}",
    )
    assert (response.status_code, response.content) == (200, b"xxx")

    # The size cap and a transport error reach the caller as themselves.
    with pytest.raises(url_validator.ResponseTooLarge):
        url_validator.safe_request(
            "POST", "https://receiver.invalid/hook", total_timeout=5.0, max_bytes=2, content=b"{}",
        )
    receiver(_Refuses())
    with pytest.raises(httpx.ConnectError):
        url_validator.safe_request("POST", "https://receiver.invalid/hook", total_timeout=5.0, content=b"{}")


def test_without_a_budget_a_request_is_not_cut_short(receiver):
    """The other callers (LLM providers, integration tests) pass none and keep
    the per-phase timeout only."""
    receiver(_Drips(chunks=5, every=0.1))
    response = url_validator.safe_request("GET", "https://receiver.invalid/slow", timeout=5.0)
    assert response.content == b"xxxxx"


# ---------------------------------------------------------------------------
# The webhook's POST and the sweep
# ---------------------------------------------------------------------------

def test_one_webhook_attempt_ends_at_its_budget_and_is_a_retryable_failure(receiver, monkeypatch):
    receiver(_Drips(chunks=40, every=0.1))
    monkeypatch.setattr(wd, "_REQUEST_BUDGET_SECONDS", 0.3, raising=False)

    (ok, status, error), elapsed = _timed(
        lambda: wd._attempt_delivery("https://receiver.invalid/hook", None, {"event": "x"}, delivery_id=1)
    )

    assert elapsed < 1.5, f"one attempt held its sender for {elapsed:.1f}s"
    assert (ok, status) == (False, None)
    assert "receiver.invalid" in error


@pytest.fixture
def due_deliveries(db_session, test_project, test_user, monkeypatch):
    """Six due outbox rows for one receiver; the fast path is kept out."""
    monkeypatch.setattr(wd, "_ensure_workers", lambda: None)
    monkeypatch.setattr(wd._QUEUE, "put_nowait", lambda item: None)
    db_session.add(WebhookConfig(
        project_id=test_project.id, name="slow receiver", url="https://receiver.invalid/hook",
        events=[], is_active=True, created_by_id=test_user.id,
    ))
    db_session.commit()
    for i in range(6):
        wd.WebhookDispatcher(db_session).stage(
            project_id=test_project.id, event="host_assigned", title=f"10.0.0.{i} assigned to you",
        )
    db_session.commit()
    for row in db_session.query(WebhookDelivery).all():
        row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    return db_session


def test_the_sweep_stops_starting_deliveries_at_its_budget(receiver, due_deliveries, monkeypatch):
    db = due_deliveries
    transport = receiver(_Sleeps(0.25))     # every POST succeeds, slowly
    monkeypatch.setattr(wd, "_REQUEST_BUDGET_SECONDS", 2.0, raising=False)
    monkeypatch.setattr(wd, "_SWEEP_BUDGET_SECONDS", 0.6, raising=False)

    attempted, elapsed = _timed(lambda: wd.sweep_pending_deliveries(db))

    # Six rows at 0.25 s are 1.5 s; the pass starts none after 0.6 s.
    assert 1 <= attempted < 6, attempted
    assert transport.requests == attempted
    assert elapsed < 0.6 + 2.0
    db.expire_all()
    rows = db.query(WebhookDelivery).order_by(WebhookDelivery.id).all()
    delivered = [r for r in rows if r.status == WebhookDeliveryStatus.DELIVERED.value]
    waiting = [r for r in rows if r.status == WebhookDeliveryStatus.PENDING.value]
    assert len(delivered) == attempted and len(waiting) == 6 - attempted
    # What the pass did not reach is untouched — not claimed, not counted as
    # an attempt, still due — and the next pass takes it.
    now = datetime.now(timezone.utc)
    assert all(r.attempts == 0 and r.claim_token is None and r.next_attempt_at <= now for r in waiting)

    monkeypatch.setattr(wd, "_SWEEP_BUDGET_SECONDS", 30.0, raising=False)
    assert wd.sweep_pending_deliveries(db) == 6 - attempted
    db.expire_all()
    assert {r.status for r in db.query(WebhookDelivery).all()} == {WebhookDeliveryStatus.DELIVERED.value}


def test_a_dead_receiver_cannot_hold_the_sweep_past_its_budget(receiver, due_deliveries, monkeypatch):
    """The case in the review: every queued event is for an address that never
    answers.  The pass costs its budget plus at most one request's."""
    db = due_deliveries
    receiver(_Sleeps(4.0))
    monkeypatch.setattr(wd, "_REQUEST_BUDGET_SECONDS", 0.3, raising=False)
    monkeypatch.setattr(wd, "_SWEEP_BUDGET_SECONDS", 0.5, raising=False)

    attempted, elapsed = _timed(lambda: wd.sweep_pending_deliveries(db))

    assert elapsed < 0.5 + 0.3 + 1.0, f"the sweep held its thread for {elapsed:.1f}s"
    assert 1 <= attempted < 6
    db.expire_all()
    failed_once = [r for r in db.query(WebhookDelivery).all() if r.attempts == 1]
    assert len(failed_once) == attempted
    assert all(r.status == WebhookDeliveryStatus.PENDING.value and "receiver.invalid" in r.last_error
               for r in failed_once)
