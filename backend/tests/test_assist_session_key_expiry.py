"""The session list must report when a session's key stops working (v2.240.0).

The operator's practical question about a live session is "end it now, or let
it lapse?", which needs an expiry. It can't be computed client-side from
``started_at`` plus a hardcoded 4 hours: ``AGENT_KEY_TTL_HOURS`` can override
the default and ``ttl_hours`` is a per-start parameter, so a derived expiry
would be quietly wrong exactly where it mattered.

It is the KEY's expiry, not the session's — the session row has no lifetime of
its own and can outlive its key.

v2.449.0 — these read ``GET /agent-sessions`` (the one list) by the session id.
They were written against ``GET /assist/sessions``, which was keyed by the
``assist_sessions`` pointer row and reported a DERIVED status: a session whose
key had expired read ``ended``.  The one list reports the stored status beside
``key_expires_at`` and ``renewable_until`` — an expired key on a session inside
its renewal window is resumable, not over (v2.340.0) — and "which of my
sessions can an agent use right now" is decided where it is asked, by the key's
expiry (``frontend/src/utils/agentRuns.ts`` ``hasLiveKey``, pinned by
``tests/hooks/useMyAssistSessions.test.tsx``).
"""

from datetime import datetime, timedelta, timezone

from app.db.models_agent import AgentSession
from app.db.models_auth import APIKey


def _start(client, project_id, **body) -> int:
    r = client.post(f"/api/v1/projects/{project_id}/assist/start", json=body or {})
    assert r.status_code == 201, r.text
    return r.json()["agent_session_id"]


def _list(client, project_id):
    r = client.get(f"/api/v1/projects/{project_id}/agent-sessions")
    assert r.status_code == 200, r.text
    return r.json()["sessions"]


def _row(client, project_id, session_id):
    return next(r for r in _list(client, project_id) if r["id"] == session_id)


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_list_reports_the_key_expiry_for_an_active_session(
    client, db_session, test_project,
):
    sid = _start(client, test_project.id, purpose="ftp sweep", ttl_hours=6)
    row = _row(client, test_project.id, sid)

    assert row["key_expires_at"] is not None
    # Matches the requested TTL, not a default — the whole reason this can't be
    # derived from started_at client-side.
    delta = _when(row["key_expires_at"]) - datetime.now(timezone.utc)
    assert timedelta(hours=5) < delta < timedelta(hours=7), delta


def test_key_expiry_is_null_once_no_active_key_remains(
    client, db_session, test_project,
):
    """A revoked key must stop advertising access."""
    sid = _start(client, test_project.id)

    db_session.query(APIKey).filter(APIKey.agent_session_id == sid).update(
        {"is_active": False}, synchronize_session=False
    )
    db_session.commit()

    assert _row(client, test_project.id, sid)["key_expires_at"] is None, (
        "a session with no live key must not report an expiry — it reads as "
        "'still has access until X' when access is already gone"
    )


def test_expiry_lookup_does_not_scale_with_session_count(
    client, db_session, test_project,
):
    """Guards the grouped query against regressing to a per-row lookup."""
    ids = [_start(client, test_project.id) for _ in range(5)]

    from sqlalchemy import event

    statements = []
    conn = db_session.connection()

    def _record(conn_, cursor, statement, params, context, executemany):
        if "api_keys" in statement.lower():
            statements.append(statement)

    event.listen(conn.engine, "before_cursor_execute", _record)
    try:
        rows = _list(client, test_project.id)
    finally:
        event.remove(conn.engine, "before_cursor_execute", _record)

    assert all(
        r["key_expires_at"] is not None for r in rows if r["id"] in ids
    ), "every freshly started session should report an expiry"
    # One SELECT against api_keys for the whole list, not one per session.
    selects = [s for s in statements if s.lower().lstrip().startswith("select")]
    # Guard against the assertion passing vacuously (listener not wired to the
    # connection the request actually used) — there must be exactly one.
    assert len(selects) == 1, f"expected a single grouped lookup, saw {len(selects)}"


def test_the_list_is_a_bounded_number_of_statements(client, db_session, test_project):
    """Every derived column (key expiry, feedback, roles, calls, notes, tests,
    evidence) is one grouped query for the page — the count must not grow with
    the number of sessions."""
    from sqlalchemy import event

    def _count() -> int:
        seen = []
        engine = db_session.connection().engine

        def _record(conn_, cursor, statement, params, context, executemany):
            if statement.lower().lstrip().startswith("select"):
                seen.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            _list(client, test_project.id)
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        return len(seen)

    for _ in range(2):
        _start(client, test_project.id)
    few = _count()
    for _ in range(6):
        _start(client, test_project.id)
    assert _count() == few, "the session list runs a query per row"


# ---------------------------------------------------------------------------
# Expired keys and lapsing (v2.283.0, v2.340.0)
# ---------------------------------------------------------------------------

def _expire_keys(db_session, session_id, *, when=None):
    """Age the session's key out, the way the TTL would."""
    db_session.query(APIKey).filter(APIKey.agent_session_id == session_id).update(
        {"expires_at": when or (datetime.now(timezone.utc) - timedelta(minutes=5))},
        synchronize_session=False,
    )
    db_session.commit()


def test_a_session_whose_key_expired_says_so_and_stays_resumable(
    client, db_session, test_project,
):
    """The row an operator's "live sessions" panel filters on: the key's expiry
    is in the past (so the panel drops it), while the session itself is still
    active and renewable (so Agent Sessions offers Resume)."""
    sid = _start(client, test_project.id)
    _expire_keys(db_session, sid)

    row = _row(client, test_project.id, sid)
    now = datetime.now(timezone.utc)
    assert _when(row["key_expires_at"]) < now
    assert row["status"] == "active"
    assert _when(row["renewable_until"]) > now
    assert row["can_resume"] is True


def _age_past_renewal(db_session, session_id):
    """Push the session's start back past the renewal cap.  The sweep
    (``lapse_expired_agent_sessions``) deliberately leaves an
    expired-but-renewable session alone — an agent mid-scan can still renew
    with the same key — so a lapse needs BOTH a dead key and a session past
    ``AGENT_SESSION_MAX_LIFETIME_HOURS``."""
    from app.core.config import settings
    db_session.query(AgentSession).filter(AgentSession.id == session_id).update(
        {"started_at": datetime.now(timezone.utc)
         - timedelta(hours=settings.AGENT_SESSION_MAX_LIFETIME_HOURS + 1)},
        synchronize_session=False,
    )
    db_session.commit()


def test_a_live_session_is_left_alone(client, db_session, test_project):
    """The sweep must not end sessions an agent is still using — that would
    revoke work in progress on a timer nobody asked for."""
    from app.services.agent_session_service import lapse_expired_agent_sessions

    sid = _start(client, test_project.id, ttl_hours=6)
    assert lapse_expired_agent_sessions(db_session) == 0

    db_session.expire_all()
    assert db_session.get(AgentSession, sid).status == "active"


def test_an_expired_but_renewable_session_is_left_alone(client, db_session, test_project):
    """A dead key on a session still inside its renewal window is the
    long-scan case: the agent renews with the same key when it wakes."""
    from app.services.agent_session_service import lapse_expired_agent_sessions

    sid = _start(client, test_project.id)
    _expire_keys(db_session, sid)
    assert lapse_expired_agent_sessions(db_session) == 0
    db_session.expire_all()
    assert db_session.get(AgentSession, sid).status == "active"


def test_the_sweep_ends_lapsed_sessions_and_dates_them_honestly(
    client, db_session, test_project,
):
    """``completed_at`` records when access actually stopped — the key's
    expiry, not when the hourly sweep happened to run, which would misdate
    every lapse by up to an hour."""
    from app.services.agent_session_service import lapse_expired_agent_sessions

    sid = _start(client, test_project.id)
    expired_at = datetime.now(timezone.utc) - timedelta(hours=3)
    _expire_keys(db_session, sid, when=expired_at)
    _age_past_renewal(db_session, sid)

    assert lapse_expired_agent_sessions(db_session) == 1

    db_session.expire_all()
    session = db_session.get(AgentSession, sid)
    assert session.status == "ended" and session.end_reason == "lapsed"
    ended_at = session.completed_at
    assert ended_at is not None
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=timezone.utc)
    assert abs((ended_at - expired_at).total_seconds()) < 2, (
        "completed_at should be when the key died, not when the sweep ran"
    )
    # The page reads that same row.
    row = _row(client, test_project.id, sid)
    assert row["status"] == "ended" and row["end_reason"] == "lapsed"

    # Idempotent: a second pass finds nothing, so concurrent workers can't
    # double-end or rewrite the timestamp.
    assert lapse_expired_agent_sessions(db_session) == 0


def test_the_sweep_keeps_the_session_record(client, db_session, test_project):
    """Lapsing is a status change, not a delete — the audit trail is the reason
    the row exists after the key is gone."""
    from app.services.agent_session_service import lapse_expired_agent_sessions

    sid = _start(client, test_project.id, purpose="ftp sweep")
    _expire_keys(db_session, sid)
    _age_past_renewal(db_session, sid)
    lapse_expired_agent_sessions(db_session)

    db_session.expire_all()
    session = db_session.get(AgentSession, sid)
    assert session is not None and session.purpose == "ftp sweep"
