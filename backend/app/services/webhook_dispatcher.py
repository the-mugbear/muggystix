"""Outbound webhook delivery (v2.73.0).

The first outbound-HTTP egress in BlueStick.  Fires a JSON POST to each
enabled ``WebhookConfig`` whose event mask includes the dispatched event.
The payload is Slack-incoming-webhook compatible (a top-level ``text``
field) while also carrying structured fields for generic consumers.

Delivery is **durable** as of v2.233.0.  Every intended POST is first
persisted to the ``webhook_deliveries`` outbox, then attempted immediately
on a small thread pool so the request path keeps its latency.  Anything
that attempt can't deliver — receiver down, 5xx during a deploy, process
restarted mid-flight, queue full — is retried by ``sweep_pending_deliveries``
on the ingestion worker's tick with exponential backoff, and a row that
exhausts ``max_attempts`` stays as ``failed`` rather than vanishing, so
"did that alert ever go out?" is answerable after the fact.

Before this, delivery was best-effort fire-and-forget: the payload lived
only in a process-local queue and any network or HTTP failure was logged
and dropped.

v2.302.0 made the outbox **transactional**.  Callers used to dispatch after
their own commit, which meant the row was written in a second commit just
after the first: a crash in between lost the event, and a caller that rolled
back after dispatching had already announced work that never landed.  Now
``stage_dispatch`` adds the rows to the *caller's* session before its commit,
and the first POST attempt is handed to the fast path by an ``after_commit``
hook — so intent and change are atomic in both directions, and nothing is sent
until the change is durable.  **Call it BEFORE ``db.commit()``.**

The ``/test`` path still delivers synchronously (and is not persisted) so the
UI shows immediate pass/fail.

v2.91.2 (code review NEW D, Option A) — replaced the unbounded
``ThreadPoolExecutor`` work queue with a fixed-size ``queue.Queue`` +
daemon worker threads.  Pre-fix a slow webhook during a notification
burst could pile up arbitrarily many pending tasks in the executor's
internal queue (each carrying the full payload + signing secret),
inflating backend RAM and delivering stale notifications hours late.
Now the queue caps at ``_QUEUE_MAX``; submissions past the cap drop
the delivery AND create a user-visible Notification addressed to the
webhook's creator so the operator knows to retry and why ("receiver
was too slow under current load — not a backend error").  Drop
notifications are coalesced per-(webhook_config, 5-minute window) so
a sustained outage produces one actionable ping per window rather
than flooding the bell.  Critical invariant: the drop-notification
write does NOT itself fan out webhooks (only ``stage_dispatch`` does,
and it's a separate call path) — avoiding the drop → notify →
webhook → drop loop.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import event as sa_event
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.db import session as _session_module
from app.db.models_project import Notification, WebhookConfig
from app.services.llm_provider_service import decrypt_secret
from app.services.url_validator import safe_request

logger = logging.getLogger(__name__)

# Known event keys.  Extend as new event sources are wired in; the config
# UI offers exactly this set.  An empty config event list means "all".
WEBHOOK_EVENTS = {
    "note_mention": "Someone @mentioned a teammate on a host note",
    "note_status_change": "A host note's status changed",
    "host_assigned": "A host was assigned to someone",
}

# Bounded delivery queue + daemon worker threads.  ``_QUEUE_MAX`` is
# generous enough for normal bursts (200 mention notifications during
# an active discussion fits inside one window) but well below the
# memory danger zone — each task carries a JSON payload + URL + secret,
# so ~256 × ~2 KB ≈ 0.5 MB ceiling per process.
_QUEUE_MAX = 256
_WORKER_COUNT = 4
_QUEUE: "queue.Queue[tuple[str, Optional[str], dict] | None]" = queue.Queue(
    maxsize=_QUEUE_MAX,
)
_WORKERS_STARTED = False
_WORKER_LOCK = threading.Lock()

# Drop-notification coalescing per webhook config.  In-memory only —
# survives the life of the process; on restart we forget which webhooks
# were dropping recently, which is fine: the next drop fires a fresh
# notification.
_DROP_COALESCE_WINDOW_SECONDS = 300  # 5 minutes
_DROP_TRACKER: dict[int, dict[str, float]] = {}
_DROP_TRACKER_LOCK = threading.Lock()

_TIMEOUT_SECONDS = 5.0


def _ensure_workers() -> None:
    """Start the daemon worker threads on first dispatch.  Idempotent +
    thread-safe — multiple concurrent calls converge on a single
    initialization."""
    global _WORKERS_STARTED
    if _WORKERS_STARTED:
        return
    with _WORKER_LOCK:
        if _WORKERS_STARTED:
            return
        for i in range(_WORKER_COUNT):
            t = threading.Thread(
                target=_worker_loop,
                name=f"webhook-{i}",
                daemon=True,
            )
            t.start()
        _WORKERS_STARTED = True


def _worker_loop() -> None:
    """Consume tasks from the bounded queue indefinitely.  Errors in
    delivery are absorbed at the ``_deliver`` boundary; this loop only
    ever propagates a hard interpreter shutdown."""
    while True:
        task = _QUEUE.get()
        try:
            if task is None:
                return  # sentinel; not used in production but useful for tests
            url, secret, payload, delivery_id = task
            _deliver(url, secret, payload, delivery_id)
        except Exception:  # pragma: no cover — defensive: never kill the worker
            logger.exception("webhook worker loop swallowed unexpected error")
        finally:
            _QUEUE.task_done()


def _prune_drop_tracker_locked(now: float) -> None:
    """Drop tracker entries that have gone quiet, so the module-global
    dict can't grow unbounded across the process lifetime (one entry per
    webhook config that ever overflowed, surviving config deletion).
    Caller must hold ``_DROP_TRACKER_LOCK``.  An entry is removed once it
    has no pending drops AND hasn't notified within 2× the coalesce
    window — i.e. it's fully drained and idle."""
    stale_cutoff = now - 2 * _DROP_COALESCE_WINDOW_SECONDS
    stale = [
        cfg_id
        for cfg_id, entry in _DROP_TRACKER.items()
        if entry["drops"] == 0 and entry["last_notified"] < stale_cutoff
    ]
    for cfg_id in stale:
        del _DROP_TRACKER[cfg_id]


def _record_dropped_delivery(
    *, cfg_id: int, cfg_url: str, cfg_name: str, created_by_id: Optional[int],
    project_id: int, event: str, title: str,
) -> None:
    """Log + (coalesced) Notification when the bounded queue refuses a
    new delivery.  The notification is addressed to the webhook's
    creator (``WebhookConfig.created_by_id``) so the operator who
    configured it gets the actionable ping; if creator is null (e.g.
    the user was deleted), we log only.

    Coalescing: within ``_DROP_COALESCE_WINDOW_SECONDS`` after a
    notification fires for a given webhook config, further drops are
    counted but suppressed.  The next notification (after the window
    closes) reports the accumulated count.  This keeps a sustained
    outage from flooding the bell.

    The notification write uses a fresh short-lived session (not the
    request's), so webhook backpressure can never commit or roll back
    whatever transaction the calling request happens to be mid-way
    through — same isolation the rest of this fire-and-forget module
    relies on.
    """
    logger.warning(
        "Webhook delivery dropped (queue full) cfg_id=%s url=%s event=%s",
        cfg_id, cfg_url, event,
    )
    if created_by_id is None:
        return
    now = time.monotonic()
    should_notify = False
    drop_count = 0
    with _DROP_TRACKER_LOCK:
        _prune_drop_tracker_locked(now)
        entry = _DROP_TRACKER.setdefault(
            cfg_id, {"last_notified": 0.0, "drops": 0.0},
        )
        entry["drops"] += 1
        if now - entry["last_notified"] > _DROP_COALESCE_WINDOW_SECONDS:
            should_notify = True
            drop_count = int(entry["drops"])
            entry["drops"] = 0
            entry["last_notified"] = now
    if not should_notify:
        return
    plural = "s" if drop_count != 1 else ""
    body = (
        f'The webhook "{cfg_name}" dropped {drop_count} event{plural} '
        f"in the last few minutes because the delivery queue was full. "
        "This is a temporary backpressure signal — the receiver was too "
        "slow under current load, not a backend error.  If the action "
        "that produced these events was important, retry it; the "
        "webhook will deliver again once the queue clears.  Most recent "
        f"event: {event} — {title}."
    )
    # Look up SessionLocal lazily so tests can rebind it onto the test
    # engine (same pattern as agent_api_log_service).
    db = _session_module.SessionLocal()
    try:
        db.add(Notification(
            user_id=created_by_id,
            project_id=project_id,
            type="webhook_dropped",
            title="Webhook delivery dropped — receiver too slow",
            body=body,
            source_type="webhook",
            source_id=cfg_id,
            actor_id=None,
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to write webhook-drop notification cfg_id=%s", cfg_id,
        )
    finally:
        db.close()


def is_valid_webhook_url(url: str) -> bool:
    """Accept only absolute http/https URLs.

    Host-level SSRF filtering is intentionally NOT applied: webhook
    targets are configured by project admins (a trusted action) and
    legitimately point at internal chat/SIEM endpoints (self-hosted
    Mattermost, an internal Slack proxy, a SOAR listener).  Scheme
    validation keeps out file:// and other non-HTTP schemes.
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def build_payload(event: str, title: str, body: str, project_id: int, context: Optional[dict] = None) -> dict:
    text = f"*{title}*\n{body}" if body else f"*{title}*"
    return {
        # Slack incoming-webhook reads `text`; generic consumers read the rest.
        "text": text,
        "event": event,
        "title": title,
        "body": body,
        "project_id": project_id,
        "context": context or {},
        "source": "bluestick",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _post(
    url: str, secret: Optional[str], payload: dict, delivery_id: Optional[int] = None,
) -> httpx.Response:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "BlueStick-Webhook/1"}
    if delivery_id is not None:
        # Stable per-delivery identifier so a receiver can dedupe on its own
        # side. The atomic claim makes a duplicate POST very unlikely, but a
        # retry after a response we never saw (timeout on a receiver that DID
        # process it) is indistinguishable from a first attempt without this.
        headers["X-BlueStick-Delivery-Id"] = str(delivery_id)
    if secret:
        headers["X-BlueStick-Signature"] = _sign(secret, body)
    # Route through the SSRF-aware client.  Webhook targets are admin-
    # configured and legitimately internal (allow_private=True keeps
    # self-hosted Mattermost / SOAR on the LAN working), but the
    # two-tier policy in _host_resolves_safely still blocks cloud-
    # metadata / link-local addresses and refuses redirects to private
    # IPs — closing the one egress that previously used raw httpx.post
    # and bypassed url_validator entirely.
    # safe_request streams + size-caps the response (a hostile receiver could
    # otherwise return a huge body httpx would buffer); a ResponseTooLarge or
    # transport error propagates to _deliver/send_test, which already log it.
    return safe_request(
        "POST", url, allow_private=True, timeout=_TIMEOUT_SECONDS,
        content=body, headers=headers,
    )


# Retry schedule for a persisted delivery: ~30s, 1m, 4m, 16m, 64m … capped.
# Exponential so a receiver that is briefly down recovers fast, while a
# receiver that is properly broken stops being hammered.
_RETRY_BASE_SECONDS = 30
_RETRY_MAX_SECONDS = 3600
# How long a claimed (``sending``) row stays owned before another sender may
# reclaim it. This is a crash-recovery bound, not a correctness mechanism:
# correctness comes from the atomic claim in ``_claim_for_send``. It only has
# to exceed a realistic POST duration so a live sender is not undercut —
# generous margin over _TIMEOUT_SECONDS to cover DNS + TLS + the response.
_CLAIM_LEASE_SECONDS = 60
# Back-compat alias: the old name described the pre-A6 design (hiding a row
# from the sweeper). Kept so external references don't break.
_FASTPATH_LEASE_SECONDS = _CLAIM_LEASE_SECONDS
# Rows the sweeper will attempt per pass — bounded so a large backlog can't
# monopolise the worker tick.
_SWEEP_BATCH = 20
# How long delivered rows are kept before the sweeper prunes them. Failed rows
# are kept longer (they're the ones an operator investigates).
_DELIVERED_RETENTION_DAYS = 7
_FAILED_RETENTION_DAYS = 30


def _backoff_seconds(attempts: int) -> int:
    """Delay before attempt N+1. Grows 4x per failure, capped."""
    return min(_RETRY_BASE_SECONDS * (4 ** max(attempts - 1, 0)), _RETRY_MAX_SECONDS)


def _attempt_delivery(
    url: str, secret: Optional[str], payload: dict, delivery_id: Optional[int] = None,
):
    """POST once. Returns (ok, response_status, error) — never raises.

    A 2xx is success. Anything else, including a transport failure, is a
    retryable error: a receiver returning 500 during a deploy is exactly the
    case the outbox exists for.
    """
    try:
        resp = _post(url, secret, payload, delivery_id=delivery_id)
    except Exception as exc:  # network error, timeout, DNS, size cap, …
        return False, None, str(exc)[:1000]
    if 200 <= resp.status_code < 300:
        return True, resp.status_code, None
    return False, resp.status_code, f"receiver returned HTTP {resp.status_code}"


def _record_attempt(
    delivery_id: int, ok: bool, status: Optional[int], error: Optional[str],
    token: Optional[str] = None,
) -> None:
    """Persist the outcome of one attempt on its own short-lived session.

    Runs on a delivery thread, so it must not touch the caller's session.
    """
    from app.db.models_project import WebhookDelivery, WebhookDeliveryStatus

    SessionLocal = _session_module.SessionLocal
    try:
        with SessionLocal() as db:
            row = db.get(WebhookDelivery, delivery_id)
            if row is None:
                return
            # Only the claim holder may record. Without this a sender whose
            # lease expired mid-POST could still write an outcome over the row
            # a second sender now owns.
            if token is not None:
                if row.claim_token != token:
                    logger.debug(
                        "Webhook delivery %s changed hands before its outcome "
                        "was recorded; discarding this sender's result",
                        delivery_id,
                    )
                    return
            elif row.status != WebhookDeliveryStatus.PENDING.value:
                return
            row.claim_token = None
            row.attempts = (row.attempts or 0) + 1
            row.response_status = status
            if ok:
                row.status = WebhookDeliveryStatus.DELIVERED.value
                row.delivered_at = datetime.now(timezone.utc)
                row.next_attempt_at = None
                row.last_error = None
            else:
                row.last_error = error
                if row.attempts >= (row.max_attempts or 6):
                    row.status = WebhookDeliveryStatus.FAILED.value
                    row.next_attempt_at = None
                    logger.warning(
                        "Webhook delivery %s to config %s gave up after %d attempts: %s",
                        delivery_id, row.webhook_config_id, row.attempts, error,
                    )
                else:
                    # Hand the row back as retryable. It is currently
                    # ``sending`` (we own it), so this MUST reset the status —
                    # leaving it ``sending`` would strand the row: the
                    # sweeper's due-query would skip it until its lease
                    # expired, turning a routine retry into a lease timeout.
                    row.status = WebhookDeliveryStatus.PENDING.value
                    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                        seconds=_backoff_seconds(row.attempts)
                    )
            db.commit()
    except Exception:  # pragma: no cover — bookkeeping must never kill a worker
        logger.exception("Could not record webhook delivery outcome for %s", delivery_id)


def _claim_for_send(delivery_id: int) -> Optional[str]:
    """Atomically take ownership of a delivery. Returns a token, or None.

    v2.240.3 (review A6) — this used to *read* ``status == 'pending'`` and
    return a bool. That is check-then-act, not a claim: the sweeper leaves a
    row ``pending`` while its own POST is in flight, so a fast-path task that
    had waited in the bounded queue past its lease would read ``pending`` and
    send the very delivery the sweeper was sending. Four workers, a 256-slot
    queue, a 5s HTTP timeout and a 60s lease make that reachable with ~48 slow
    receivers. Payloads carry no idempotency key, so the receiver couldn't
    dedupe it either.

    The claim is now one UPDATE. A row is claimable when it is due and either
    ``pending`` or ``sending`` with an expired lease (the previous owner died
    mid-POST). Winning flips it to ``sending``, stamps a token, and pushes the
    lease out; the loser gets zero rows back and stands down.
    """
    from app.db.models_project import WebhookDelivery, WebhookDeliveryStatus

    token = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    SessionLocal = _session_module.SessionLocal
    try:
        with SessionLocal() as db:
            result = db.execute(
                sa_update(WebhookDelivery)
                .where(
                    WebhookDelivery.id == delivery_id,
                    WebhookDelivery.status.in_(
                        [
                            WebhookDeliveryStatus.PENDING.value,
                            WebhookDeliveryStatus.SENDING.value,
                        ]
                    ),
                    WebhookDelivery.next_attempt_at.isnot(None),
                    WebhookDelivery.next_attempt_at <= now,
                )
                .values(
                    status=WebhookDeliveryStatus.SENDING.value,
                    claim_token=token,
                    next_attempt_at=now + timedelta(seconds=_CLAIM_LEASE_SECONDS),
                )
            )
            db.commit()
            return token if result.rowcount == 1 else None
    except Exception:  # pragma: no cover — never let bookkeeping block delivery
        # Failing CLOSED here: an unclaimed delivery is retried by the sweeper,
        # whereas sending unclaimed is the duplicate this fix exists to stop.
        logger.exception("Could not claim webhook delivery %s; leaving it queued", delivery_id)
        return None


def _deliver(url: str, secret: Optional[str], payload: dict, delivery_id: Optional[int] = None) -> None:
    """Attempt one delivery and, when it is backed by an outbox row, record the
    outcome so a failure is retried instead of vanishing into a log line."""
    token: Optional[str] = None
    if delivery_id is not None:
        token = _claim_for_send(delivery_id)
        if token is None:
            logger.debug(
                "Webhook delivery %s is owned by another sender or already "
                "settled; skipping",
                delivery_id,
            )
            return
    ok, status, error = _attempt_delivery(url, secret, payload, delivery_id=delivery_id)
    if not ok:
        logger.warning("Webhook delivery to %s failed: %s", url, error)
    if delivery_id is not None:
        _record_attempt(delivery_id, ok, status, error, token=token)


class DispatchResult(NamedTuple):
    """Outcome of a ``dispatch()`` call: how many deliveries were enqueued vs
    dropped (queue full).  ``dropped > 0`` means the receiver(s) couldn't keep
    up; each drop also raised a coalesced operator Notification."""
    queued: int
    dropped: int


class WebhookDispatcher:
    def __init__(self, db: Session):
        self.db = db

    def stage(
        self,
        *,
        project_id: int,
        event: str,
        title: str,
        body: str = "",
        context: Optional[dict] = None,
    ) -> "DispatchResult":
        """Add outbox rows to the CALLER'S transaction; send nothing yet.

        v2.302.0.  This used to be ``dispatch()``, which wrote the rows in a
        commit of its own — necessarily *after* the caller had committed, since
        every call site dispatched post-commit.  Two problems with that, and
        the second is the worse one:

        * a crash between the two commits lost the event outright;
        * a caller whose transaction rolled back *after* dispatching had
          already announced something that never happened.

        Staging inside the caller's transaction makes the outbox row and the
        change it describes atomic — each exists if and only if the other
        does.  The first POST attempt is fired by the ``after_commit`` hook
        (see ``_drain_pending``), so nothing leaves the process until the
        caller's work is durable.

        Returns ``DispatchResult(queued, dropped)`` where ``queued`` counts
        rows STAGED — the send itself can still be deferred to the sweeper if
        the fast-path queue is full at commit time.
        """
        configs = (
            self.db.query(WebhookConfig)
            .filter(WebhookConfig.project_id == project_id, WebhookConfig.is_active.is_(True))
            .all()
        )
        targets = [c for c in configs if not c.events or event in c.events]
        if not targets:
            return DispatchResult(queued=0, dropped=0)
        payload = build_payload(event, title, body, project_id, context)

        from app.db.models_project import WebhookDelivery, WebhookDeliveryStatus

        now = datetime.now(timezone.utc)
        rows = [
            WebhookDelivery(
                webhook_config_id=cfg.id,
                project_id=project_id,
                event=event,
                payload=payload,
                status=WebhookDeliveryStatus.PENDING.value,
                # Due immediately: whoever claims it first wins, and claiming
                # is atomic, so the sweeper is a safety net rather than a
                # competitor. Leasing it into the future would only delay
                # recovery if the fast path never ran.
                next_attempt_at=now,
            )
            for cfg in targets
        ]
        self.db.add_all(rows)
        # Flush — not commit — so the rows get ids to hand the fast path while
        # still belonging to the caller's transaction.
        self.db.flush()

        # Capture plain values now. After the commit these ORM objects are
        # expired, and touching them from the hook would emit a fresh SELECT
        # per row on a session the caller may already have closed.
        pending = self.db.info.setdefault(_PENDING_KEY, [])
        for cfg, row in zip(targets, rows):
            secret = decrypt_secret(cfg.secret_encrypted) if cfg.secret_encrypted else None
            pending.append((
                cfg.url, secret, payload, row.id,
                cfg.id, cfg.name, cfg.created_by_id, cfg.project_id, event, title,
            ))
        return DispatchResult(queued=len(rows), dropped=0)

    def deliver_test(self, config: WebhookConfig) -> dict:
        """Synchronously deliver a test event and return the outcome —
        used by the config UI's 'send test' button for instant feedback."""
        secret = decrypt_secret(config.secret_encrypted) if config.secret_encrypted else None
        payload = build_payload(
            "test",
            "BlueStick webhook test",
            "If you can read this, this webhook is wired up correctly.",
            config.project_id,
            {"config_id": config.id, "config_name": config.name},
        )
        try:
            resp = _post(config.url, secret, payload)
            return {"ok": resp.status_code < 400, "status_code": resp.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Transactional outbox (v2.302.0)
#
# Outbox rows used to be written by ``dispatch()`` in a commit of their own,
# just AFTER the caller had already committed its own work.  The model's
# docstring said so plainly: "a crash in the microseconds between the two still
# loses the event".  Worse than the window is the asymmetry — a caller whose
# transaction rolled back after dispatching would have sent a webhook for
# something that never happened.
#
# Now the rows join the caller's transaction: ``stage_dispatch`` adds them
# WITHOUT committing, and the fast-path POST is fired from an ``after_commit``
# hook.  Commit and delivery-intent are therefore atomic — the row exists if
# and only if the change it describes exists — and a rollback discards both.
#
# The pending payloads live on ``session.info`` rather than in a closure, so
# one pair of module-level listeners serves every session: a per-call listener
# would have to be removed again on rollback, and a leaked one would fire on
# some later unrelated commit and deliver an event whose row was rolled back.
# ---------------------------------------------------------------------------

_PENDING_KEY = "_webhook_pending_sends"


def _drain_pending(session) -> None:
    """Hand every staged send to the fast path. Runs after the commit."""
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    _ensure_workers()
    for (url, secret, payload, row_id, cfg_id, cfg_name,
         created_by_id, cfg_project_id, event, title) in pending:
        try:
            _QUEUE.put_nowait((url, secret, payload, row_id))
        except queue.Full:
            # A deferral, not a loss: the row is committed and due now, so the
            # sweeper delivers it on the next tick. The operator notification
            # still fires — a receiver too slow to keep up in real time is
            # worth knowing about even though the event will land.
            _record_dropped_delivery(
                cfg_id=cfg_id, cfg_url=url, cfg_name=cfg_name,
                created_by_id=created_by_id, project_id=cfg_project_id,
                event=event, title=title,
            )


def _clear_pending(session, *args) -> None:
    """Drop staged sends when the transaction they belonged to went away."""
    session.info.pop(_PENDING_KEY, None)


sa_event.listen(Session, "after_commit", _drain_pending)
sa_event.listen(Session, "after_rollback", _clear_pending)
sa_event.listen(Session, "after_soft_rollback", _clear_pending)


def stage_dispatch(db: Session, **kwargs) -> None:
    """Stage webhook deliveries inside the CALLER'S transaction.

    Call this **before** ``db.commit()``, not after.  The outbox rows are added
    to the caller's session and committed with the caller's own work, so the
    intent to deliver and the change being announced are atomic: no window in
    which one exists without the other, in either direction.

    Nothing is sent here.  The first POST attempt is fired from an
    ``after_commit`` hook, so a rollback silently discards both the rows and
    the intent.

    Never raises.  A webhook is a side effect of the user's request; a
    misconfigured receiver or a config-query error must not fail the operation
    that triggered it.  Note the failure mode this preserves: because staging
    only *adds* to the session, an exception here leaves the caller's own work
    intact to commit.
    """
    try:
        WebhookDispatcher(db).stage(**kwargs)
    except Exception:
        logger.warning(
            "Webhook staging failed for event=%s", kwargs.get("event"), exc_info=True,
        )


# ---------------------------------------------------------------------------
# Outbox sweeper
# ---------------------------------------------------------------------------

def sweep_pending_deliveries(db: Session, *, limit: int = _SWEEP_BATCH) -> int:
    """Retry due outbox rows. Returns how many were attempted.

    This is what turns the outbox from a log into a guarantee: the fast path
    handles the happy case, and anything it couldn't deliver — receiver down,
    process restarted mid-flight, queue full — lands here on the next worker
    tick and keeps being retried with backoff until it succeeds or exhausts
    its attempts.

    Claimed with ``FOR UPDATE SKIP LOCKED`` so concurrent SWEEPERS can't both
    take the same row. That lock does NOT reach the API's fast-path threads —
    they hold no row lock while POSTing — so the fast path is kept out of the
    way by leasing instead: a row handed to the in-process queue is created
    ``_FASTPATH_LEASE_SECONDS`` in the future, and ``_claim_for_send``
    re-reads its status before any queued task actually sends. Delivery
    happens synchronously here; the batch is bounded so a pile-up of dead
    receivers can't monopolise the tick.
    """
    from app.db.models_project import (
        WebhookConfig, WebhookDelivery, WebhookDeliveryStatus,
    )

    now = datetime.now(timezone.utc)
    query = (
        db.query(WebhookDelivery)
        .filter(
            # ``sending`` rows are included so a sender that died mid-POST is
            # recovered: its lease (next_attempt_at) has passed, and the
            # atomic claim below decides who actually gets it. Without this a
            # crashed worker would wedge its row permanently.
            WebhookDelivery.status.in_(
                [
                    WebhookDeliveryStatus.PENDING.value,
                    WebhookDeliveryStatus.SENDING.value,
                ]
            ),
            WebhookDelivery.next_attempt_at.isnot(None),
            WebhookDelivery.next_attempt_at <= now,
        )
        .order_by(WebhookDelivery.next_attempt_at.asc())
        .limit(limit)
    )
    try:
        due = query.with_for_update(skip_locked=True).all()
    except Exception:
        # SQLite (test fallback) has no SKIP LOCKED; correctness there doesn't
        # depend on it since there's a single writer.
        due = query.all()
    if not due:
        return 0

    # Resolve configs and retire rows whose target is gone, then take each
    # remaining row through the SAME atomic claim the fast path uses.
    #
    # v2.240.3 (review A6) — this used to push ``next_attempt_at`` forward and
    # send while the row stayed ``pending``. FOR UPDATE SKIP LOCKED kept two
    # sweepers apart, but the fast-path threads hold no row lock, so a queued
    # task could read ``pending`` and POST the same event this loop was
    # POSTing. Claiming makes exactly one sender win, whichever it is.
    config_ids = {row.webhook_config_id for row in due}
    configs = {
        c.id: c
        for c in db.query(WebhookConfig).filter(WebhookConfig.id.in_(config_ids)).all()
    }
    candidates = []
    for row in due:
        cfg = configs.get(row.webhook_config_id)
        if cfg is None or not cfg.is_active:
            # Config deleted or disabled since the event fired — nothing to
            # deliver to. Terminal, not an error.
            row.status = WebhookDeliveryStatus.FAILED.value
            row.next_attempt_at = None
            row.claim_token = None
            row.last_error = "webhook config removed or disabled before delivery"
            continue
        candidates.append((row.id, cfg, dict(row.payload or {})))
    db.commit()

    sent = 0
    for delivery_id, cfg, payload in candidates:
        token = _claim_for_send(delivery_id)
        if token is None:
            # The fast path (or another sweeper) owns it. Not an error.
            continue
        secret = decrypt_secret(cfg.secret_encrypted) if cfg.secret_encrypted else None
        ok, status, error = _attempt_delivery(
            cfg.url, secret, payload, delivery_id=delivery_id,
        )
        _record_attempt(delivery_id, ok, status, error, token=token)
        sent += 1
    return sent


def prune_delivery_history(db: Session) -> int:
    """Drop old terminal rows. Delivered rows are noise after a week; failed
    ones are kept longer because they're what an operator investigates."""
    from app.db.models_project import WebhookDelivery, WebhookDeliveryStatus

    now = datetime.now(timezone.utc)
    deleted = (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.status == WebhookDeliveryStatus.DELIVERED.value,
            WebhookDelivery.created_at < now - timedelta(days=_DELIVERED_RETENTION_DAYS),
        )
        .delete(synchronize_session=False)
    )
    deleted += (
        db.query(WebhookDelivery)
        .filter(
            WebhookDelivery.status == WebhookDeliveryStatus.FAILED.value,
            WebhookDelivery.created_at < now - timedelta(days=_FAILED_RETENTION_DAYS),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted
