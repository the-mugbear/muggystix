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

Ending it here is the same transition the End button performs, so the audit
trail keeps its shape: `ended_at` records when access actually stopped — the
key's own expiry, not the moment the sweep happened to run, which would
misdate every lapse by up to an hour.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models_agent import AssistSession, AssistSessionStatus
from app.db.models_auth import APIKey

logger = logging.getLogger(__name__)


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


def lapse_expired_assist_sessions(db: Session) -> int:
    """End every active assist session whose keys have all expired.

    Returns the number ended.  Idempotent: a session already `ended` is not
    matched, so concurrent sweepers can't double-end one.
    """
    now = datetime.now(timezone.utc)

    # The sweep DOES want the deployment-wide aggregate: it is looking at every
    # active session, once an hour, off the request path. That is the opposite
    # of the list endpoint's need, which is why they no longer share a query.
    live_expiry = (
        db.query(
            AssistSession.id.label("session_id"),
            func.max(APIKey.expires_at).label("expires_at"),
        )
        .join(APIKey, APIKey.agent_session_id == AssistSession.agent_session_id)
        .filter(APIKey.is_active.is_(True))
        .group_by(AssistSession.id)
        .subquery()
    )

    rows = (
        db.query(AssistSession, live_expiry.c.expires_at)
        .outerjoin(live_expiry, live_expiry.c.session_id == AssistSession.id)
        .filter(AssistSession.status == AssistSessionStatus.ACTIVE.value)
        .all()
    )

    lapsed: List[AssistSession] = []
    for session, expires_at in rows:
        if expires_at is not None and expires_at > now:
            continue  # still usable
        session.status = AssistSessionStatus.ENDED.value
        # The truthful timestamp is when the credential died. Only fall back to
        # `now` for the pathological case of an active session with no key row
        # at all — there is no better answer there, and pretending otherwise
        # would put a fabricated time in the audit trail.
        session.ended_at = expires_at or now
        lapsed.append(session)

    if lapsed:
        db.commit()
        logger.info(
            "Lapsed %d assist session(s) whose keys had expired: %s",
            len(lapsed),
            ", ".join(f"#{s.id}" for s in lapsed[:20]),
        )
    return len(lapsed)
