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
from typing import List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models_agent import AssistSession, AssistSessionStatus
from app.db.models_auth import APIKey

logger = logging.getLogger(__name__)


def lapse_expired_assist_sessions(db: Session) -> int:
    """End every active assist session whose keys have all expired.

    Returns the number ended.  Idempotent: a session already `ended` is not
    matched, so concurrent sweepers can't double-end one.
    """
    now = datetime.now(timezone.utc)

    # Latest expiry across the session's still-active keys.  A session can hold
    # more than one (a re-mint on resume), and access stops when the LAST one
    # dies — taking the earliest would end sessions that are still usable.
    live_expiry = (
        db.query(
            APIKey.assist_session_id.label("session_id"),
            func.max(APIKey.expires_at).label("expires_at"),
        )
        .filter(
            APIKey.assist_session_id.isnot(None),
            APIKey.is_active.is_(True),
        )
        .group_by(APIKey.assist_session_id)
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
