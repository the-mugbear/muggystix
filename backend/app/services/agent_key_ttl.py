"""
Helpers for resolving per-key TTL on agent key creation and renewal.

Two settings drive the contract:

* ``AGENT_KEY_TTL_HOURS`` (default 24) — the default TTL applied when
  the caller doesn't specify one.
* ``AGENT_KEY_MAX_TTL_HOURS`` (default 168 = 7 days) — the hard cap.
  Requests above this are clamped down, not rejected, so a caller
  asking for 30 days on a deployment whose operator wants ≤ 7 days
  gets the cap rather than a 400.

The clamping warning is logged at WARNING so a future operator
auditing the logs can see "user X requested 720h, got 168h" — useful
context when a key expires earlier than the agent expected.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import settings as _settings

logger = logging.getLogger(__name__)


def resolve_ttl_hours(requested: Optional[int]) -> int:
    """Return the effective TTL in hours.

    * ``None`` -> default (``AGENT_KEY_TTL_HOURS``).
    * Value ≤ 0 -> minimum 1h (zero/negative is nonsensical; a key
      that has already expired serves no one).
    * Value > ``AGENT_KEY_MAX_TTL_HOURS`` -> clamped to the cap with
      a WARNING log so the caller can see the value was reduced.
    """
    default = _settings.AGENT_KEY_TTL_HOURS
    cap = _settings.AGENT_KEY_MAX_TTL_HOURS
    if requested is None:
        return default
    if requested <= 0:
        return 1
    if requested > cap:
        logger.warning(
            "Agent-key TTL clamped: requested=%sh cap=%sh", requested, cap
        )
        return cap
    return requested


def resolve_expires_at(requested_ttl_hours: Optional[int]) -> datetime:
    """Convenience helper: compute ``expires_at`` from the requested
    TTL, applying the same clamping rules as :func:`resolve_ttl_hours`.
    """
    return datetime.now(timezone.utc) + timedelta(
        hours=resolve_ttl_hours(requested_ttl_hours)
    )


def session_renewal_deadline(agent_session) -> Optional[datetime]:
    """When a session stops being renewable — ``started_at`` + the cap
    (``AGENT_SESSION_MAX_LIFETIME_HOURS``).

    Returns None when there is no session to measure from, which makes the
    key non-renewable rather than immortal.  v2.338.0 — lives here (the
    service layer) rather than in ``app.api.deps`` so the hourly sweep in
    ``agent_session_service`` can share it without importing the router
    layer; the auth dependency imports it from here.
    """
    if agent_session is None:
        return None
    started = getattr(agent_session, "started_at", None)
    if started is None:
        return None
    if started.tzinfo is None:
        # Some drivers hand back naive datetimes even for DateTime(timezone=True).
        started = started.replace(tzinfo=timezone.utc)
    return started + timedelta(hours=_settings.AGENT_SESSION_MAX_LIFETIME_HOURS)
