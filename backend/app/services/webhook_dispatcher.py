"""Outbound webhook delivery (v2.73.0).

The first outbound-HTTP egress in BlueStick.  Fires a JSON POST to each
enabled ``WebhookConfig`` whose event mask includes the dispatched event.
The payload is Slack-incoming-webhook compatible (a top-level ``text``
field) while also carrying structured fields for generic consumers.

Delivery is **best-effort, fire-and-forget**: the HTTP POST runs on a
small thread pool so it never blocks the request, and failures are logged
rather than retried/persisted.  Callers dispatch *after* their DB commit
so a rolled-back transaction doesn't emit a webhook for work that didn't
land.  The ``/test`` path delivers synchronously so the UI can show an
immediate pass/fail.

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
write does NOT itself fan out webhooks (only ``safe_dispatch`` does,
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
from datetime import datetime, timezone
from typing import NamedTuple, Optional
from urllib.parse import urlparse

import httpx
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
            url, secret, payload = task
            _deliver(url, secret, payload)
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
    cfg: WebhookConfig, event: str, title: str,
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
        cfg.id, cfg.url, event,
    )
    if cfg.created_by_id is None:
        return
    now = time.monotonic()
    should_notify = False
    drop_count = 0
    with _DROP_TRACKER_LOCK:
        _prune_drop_tracker_locked(now)
        entry = _DROP_TRACKER.setdefault(
            cfg.id, {"last_notified": 0.0, "drops": 0.0},
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
        f'The webhook "{cfg.name}" dropped {drop_count} event{plural} '
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
            user_id=cfg.created_by_id,
            project_id=cfg.project_id,
            type="webhook_dropped",
            title="Webhook delivery dropped — receiver too slow",
            body=body,
            source_type="webhook",
            source_id=cfg.id,
            actor_id=None,
        ))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Failed to write webhook-drop notification cfg_id=%s", cfg.id,
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


def _post(url: str, secret: Optional[str], payload: dict) -> httpx.Response:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "BlueStick-Webhook/1"}
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


def _deliver(url: str, secret: Optional[str], payload: dict) -> None:
    try:
        resp = _post(url, secret, payload)
        if resp.status_code >= 400:
            logger.warning("Webhook POST to %s returned HTTP %s", url, resp.status_code)
    except Exception as exc:  # network error, timeout, DNS, …
        logger.warning("Webhook delivery to %s failed: %s", url, exc)


class DispatchResult(NamedTuple):
    """Outcome of a ``dispatch()`` call: how many deliveries were enqueued vs
    dropped (queue full).  ``dropped > 0`` means the receiver(s) couldn't keep
    up; each drop also raised a coalesced operator Notification."""
    queued: int
    dropped: int


class WebhookDispatcher:
    def __init__(self, db: Session):
        self.db = db

    def dispatch(
        self,
        *,
        project_id: int,
        event: str,
        title: str,
        body: str = "",
        context: Optional[dict] = None,
    ) -> "DispatchResult":
        """Queue delivery to every enabled webhook subscribed to ``event``.

        Returns ``DispatchResult(queued, dropped)``.  Previously this returned
        ``len(targets)`` — the number of webhooks it *intended* to send — so a
        queue-full drop (still recorded as a user-visible Notification) was
        reported to the caller as a successful send.  Returning the split lets
        callers/tests observe loss.  Reads configs on the caller's session,
        then hands each POST to the thread pool — the worker threads do no DB
        work, so there's no cross-thread session sharing.
        """
        configs = (
            self.db.query(WebhookConfig)
            .filter(WebhookConfig.project_id == project_id, WebhookConfig.is_active.is_(True))
            .all()
        )
        targets = [c for c in configs if not c.events or event in c.events]
        if not targets:
            return DispatchResult(queued=0, dropped=0)
        _ensure_workers()
        payload = build_payload(event, title, body, project_id, context)
        queued = 0
        dropped = 0
        for cfg in targets:
            secret = decrypt_secret(cfg.secret_encrypted) if cfg.secret_encrypted else None
            try:
                _QUEUE.put_nowait((cfg.url, secret, payload))
                queued += 1
            except queue.Full:
                _record_dropped_delivery(cfg, event, title)
                dropped += 1
        return DispatchResult(queued=queued, dropped=dropped)

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


def safe_dispatch(db: Session, **kwargs) -> None:
    """Fire-and-forget dispatch that can never disturb the caller.

    Webhooks are a side effect of a request that has already succeeded
    (committed); a config-query error here must not surface to the user
    or affect the response.  Swallow everything to the log.
    """
    try:
        result = WebhookDispatcher(db).dispatch(**kwargs)
        if result.dropped:
            logger.warning(
                "Webhook dispatch for event=%s dropped %d of %d deliveries "
                "(receiver too slow / queue full)",
                kwargs.get("event"), result.dropped, result.queued + result.dropped,
            )
    except Exception:
        logger.warning("Webhook dispatch failed for event=%s", kwargs.get("event"), exc_info=True)
