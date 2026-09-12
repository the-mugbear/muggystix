"""Assist-session lifecycle beyond the explicit "End session" click.

An assist session's key expires on a TTL (4h by default).  Its *status*,
however, only ever changed when an operator pressed End — nothing lapsed a
session when its credential died.  The visible result was that the Start AI
Assist dialog's "you have N active sessions" panel accumulated every session the
operator had ever started: rows badged `key expired` / `No live key`, listed as
active, that no longer corresponded to anything an agent could use.  Reading
that list, the honest question is the one the operator asked — "am I supposed to
tidy these up myself?" — and the answer should be no.

`active` is meant to mean "an agent can use this right now".  A session with no
live key cannot be used by anything, so it is not active, and the operator
should not have to perform that inference (or the cleanup) by hand.

v2.337.0 — the hourly sweep itself moved to
``agent_session_service.lapse_expired_agent_sessions``, which lapses the
unified session and mirrors the status onto these legacy detail rows; what
stays here is the derived-status + key-expiry plumbing the review page uses.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models_agent import AssistSession, AssistSessionStatus
from app.db.models_auth import APIKey


def effective_status(
    stored_status: str,
    key_expires_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> str:
    """What the session's status *is*, given the state of its key.

    The stored column is only eventually correct: the sweep below converges it
    hourly, so between a key expiring and the next pass the row still reads
    `active` while nothing can use it.  Callers therefore report the derived
    value, and the API is authoritative over the column.

    Lives here, next to the sweep that writes the column, because this is the
    definition of "active" — the UI filters on it, the start dialog counts it,
    and the sweep converges toward it.  It was briefly implemented twice in
    ``assist.py`` (list and detail), which is one edit away from two surfaces
    disagreeing about whether a session is live.
    """
    if stored_status != AssistSessionStatus.ACTIVE.value:
        return stored_status
    now = now or datetime.now(timezone.utc)
    if key_expires_at is None or key_expires_at <= now:
        return AssistSessionStatus.ENDED.value
    return stored_status


def has_live_key(now: Optional[datetime] = None):
    """Correlated EXISTS: does this session still hold an unexpired active key.

    v2.288.0 — this replaced a grouped subquery (``session_id, max(expires_at)``
    GROUP BY) that was LEFT JOINed for filtering.  The aggregate had no access
    to the outer query's project or page, so it grouped every assist key in the
    deployment on every list request; expired keys keep ``is_active=True``, so
    that workload grew with total historical sessions rather than with the page
    being asked for.

    EXISTS is correlated to the row being tested, so it short-circuits on the
    first matching key and rides ``api_keys.assist_session_id``'s index.  Use
    this for the *filter*; use :func:`key_expiry_for_sessions` for the value to
    display, which only needs the ids on the page.
    """
    now = now or datetime.now(timezone.utc)
    return (
        select(APIKey.id)
        .where(
            # Post-contract: keys bind to the session via their agent_session
            # (the api_keys.assist_session_id column was dropped).
            APIKey.agent_session_id == AssistSession.agent_session_id,
            APIKey.is_active.is_(True),
            APIKey.expires_at.isnot(None),
            APIKey.expires_at > now,
        )
        .exists()
    )


def key_expiry_for_sessions(db: Session, session_ids: List[int]) -> dict:
    """``{session_id: max(expires_at)}`` for the given sessions, one query.

    Scoped to the page's ids rather than the deployment's history.  MAX because
    a session can hold more than one key (a re-mint on resume) and access stops
    when the LAST one dies — the earliest would call a usable session dead.
    """
    if not session_ids:
        return {}
    return {
        sid: expires_at
        for sid, expires_at in (
            # Map keys back to their assist session through agent_session
            # (api_keys.assist_session_id was dropped in the contract phase).
            db.query(AssistSession.id, func.max(APIKey.expires_at))
            .join(APIKey, APIKey.agent_session_id == AssistSession.agent_session_id)
            .filter(
                AssistSession.id.in_(session_ids),
                APIKey.is_active.is_(True),
            )
            .group_by(AssistSession.id)
            .all()
        )
    }
